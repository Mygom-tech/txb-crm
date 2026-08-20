# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.desk.form.assign_to import add as assign_add
from frappe.desk.form.assign_to import remove as assign_remove
from frappe.tests.utils import FrappeTestCase

from crm.api import activities as activities_api
from crm.fcrm.doctype.crm_lead.crm_lead import CONTACT_ORGANIZATION_LINK_FIELD, convert_to_deal
from crm.txb.constants import (
	FIELD_CONVERTED_AT,
	FIELD_CONVERTED_CONTACT,
	FIELD_CONVERTED_DEAL,
	PIPELINE_INDIVIDUAL_SESSION,
)


class TestCRMLead(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		"""Set up test records once for all tests"""
		if not frappe.db.exists("Salutation", "Mr"):
			frappe.get_doc({"doctype": "Salutation", "salutation": "Mr"}).insert(ignore_permissions=True)
			frappe.db.commit()  # nosemgrep

		super().setUpClass()

	@classmethod
	def tearDownClass(cls):
		"""Clean up test records after all tests"""
		frappe.db.rollback()
		super().tearDownClass()

	def tearDown(self):
		frappe.db.rollback()

	def test_lead_creation_with_first_name(self):
		"""Test creating a lead with first name"""
		lead = create_lead(
			first_name="John",
			last_name="Doe",
			email="john.doe@example.com",
			mobile_no="+1234567890",
		)

		self.assertTrue(lead.name)
		self.assertEqual(lead.first_name, "John")
		self.assertEqual(lead.last_name, "Doe")
		self.assertEqual(lead.email, "john.doe@example.com")
		self.assertEqual(lead.lead_name, "John Doe")

	def test_lead_name_with_salutation(self):
		"""Test lead name generation with salutation"""
		lead = create_lead(
			salutation="Mr",
			first_name="James",
			middle_name="Robert",
			last_name="Smith",
			email="james.smith@example.com",
		)

		self.assertEqual(lead.lead_name, "Mr James Robert Smith")
		self.assertEqual(lead.first_name, "James")
		self.assertEqual(lead.middle_name, "Robert")
		self.assertEqual(lead.last_name, "Smith")

	def test_invalid_email_validation(self):
		"""Test that invalid email raises validation error"""
		with self.assertRaises(frappe.exceptions.ValidationError):
			create_lead(
				first_name="Invalid",
				email="not-an-email",
			)

	def test_set_lead_name_scenarios(self):
		"""Test various scenarios for setting lead_name"""
		# Test 1: lead_name from organization when no first_name
		lead1 = frappe.get_doc({"doctype": "CRM Lead", "organization": "Tech Corp"})
		lead1.flags.ignore_mandatory = True
		lead1.insert()
		self.assertEqual(lead1.lead_name, "Tech Corp")
		self.assertEqual(lead1.title, "Tech Corp")

		# Test 2: lead_name from email prefix when no first_name or organization
		lead2 = frappe.get_doc({"doctype": "CRM Lead", "email": "contact@company.com"})
		lead2.flags.ignore_mandatory = True
		lead2.insert()
		self.assertEqual(lead2.lead_name, "contact")
		self.assertEqual(lead2.title, "contact")

		# Test 3: Throws error without first_name, organization, or email
		with self.assertRaises(frappe.exceptions.ValidationError) as context:
			create_lead()
		self.assertIn(
			"A Lead requires either a person's name or an organization's name", str(context.exception)
		)

		# Test 4: lead_name set to 'Unnamed Lead' with ignore_mandatory flag
		lead3 = frappe.get_doc({"doctype": "CRM Lead"})
		lead3.flags.ignore_mandatory = True
		lead3.insert()
		self.assertEqual(lead3.lead_name, "Unnamed Lead")
		self.assertEqual(lead3.title, "Unnamed Lead")

	def test_lead_title_generation(self):
		"""Test that title is set correctly"""
		lead = create_lead(
			first_name="Alice",
			organization="Acme Corp",
			email="alice@acme.com",
		)

		# Title should be organization if provided, otherwise lead_name
		self.assertEqual(lead.title, "Acme Corp")

		lead2 = create_lead(
			first_name="Bob",
			email="bob@example.com",
		)

		self.assertEqual(lead2.title, "Bob")

	def test_lead_owner_cannot_be_same_as_email(self):
		"""Test that lead owner cannot be same as lead email address"""

		with self.assertRaises(frappe.exceptions.ValidationError) as context:
			create_lead(
				first_name="Test",
				email="crm.user1@example.com",
				lead_owner="crm.user1@example.com",
			)
		self.assertIn("Lead Owner cannot be same as the Lead Email Address", str(context.exception))

	def test_update_lead_owner(self):
		"""Test that updating lead owner assigns and shares with the new owner"""
		# Create a lead without owner
		# TXB-106: the creator owns every new record, so a lead is never ownerless. The
		# fixture therefore starts owned by someone else -- otherwise this test would set
		# lead_owner to the value it already had, has_value_changed would be false, and the
		# assign-and-share this test exists to verify would never fire.
		lead = create_lead(
			first_name="Owner",
			last_name="Test",
			email="ownertest@example.com",
			lead_owner="crm.user1@example.com",
		)

		self.assertEqual(lead.lead_owner, "crm.user1@example.com")

		# Update lead owner
		lead.lead_owner = "Administrator"
		lead.save()

		# Verify owner was updated
		lead.reload()
		self.assertEqual(lead.lead_owner, "Administrator")

		# Verify agent was assigned
		assignees = lead.get_assigned_users()
		self.assertIn("Administrator", assignees)
		initial_assignees_count = len(assignees)

		# Verify document was shared with agent
		docshare = frappe.db.exists(
			"DocShare",
			{"user": "Administrator", "share_name": lead.name, "share_doctype": "CRM Lead"},
		)
		self.assertTrue(docshare)

		# Try to assign the same agent again - should not duplicate
		lead.assign_agent("Administrator")
		assignees_after = lead.get_assigned_users()
		self.assertEqual(len(assignees_after), initial_assignees_count)
		self.assertIn("Administrator", assignees_after)

		# Share with same agent again - should not duplicate docshare
		initial_docshares = frappe.get_all(
			"DocShare",
			filters={"share_name": lead.name, "share_doctype": "CRM Lead"},
		)
		initial_docshare_count = len(initial_docshares)
		lead.share_with_agent("Administrator")
		after_docshares = frappe.get_all(
			"DocShare",
			filters={"share_name": lead.name, "share_doctype": "CRM Lead"},
		)
		self.assertEqual(len(after_docshares), initial_docshare_count)

		lead.lead_owner = "crm.user1@example.com"
		lead.save()
		lead.reload()

		# Verify new owner is assigned and shared
		self.assertEqual(lead.lead_owner, "crm.user1@example.com")
		new_docshare = frappe.db.exists(
			"DocShare",
			{"user": "crm.user1@example.com", "share_name": lead.name, "share_doctype": "CRM Lead"},
		)
		self.assertTrue(new_docshare)

		# Verify old owner's share was removed
		old_docshare = frappe.db.exists(
			"DocShare",
			{"user": "Administrator", "share_name": lead.name, "share_doctype": "CRM Lead"},
		)
		self.assertFalse(old_docshare)

	def test_lead_creation_with_owner(self):
		"""Test creating a lead with lead owner assigns agent on insert"""
		lead = create_lead(
			first_name="Owned",
			last_name="Lead",
			email="ownedlead@example.com",
			lead_owner="Administrator",
		)

		# Verify lead was created with owner
		self.assertEqual(lead.lead_owner, "Administrator")

		# Verify agent was assigned during after_insert
		assignees = lead.get_assigned_users()
		self.assertIn("Administrator", assignees)

	def test_create_contact_from_lead(self):
		"""Test creating a contact from lead data"""
		lead = create_lead(
			first_name="Michael",
			last_name="Jordan",
			email="mj@bulls.com",
			mobile_no="+1234567890",
			phone="+0987654321",
			organization="Chicago Bulls",
			job_title="Player",
			salutation="Mr",
		)

		contact_name = lead.create_contact()
		self.assertTrue(contact_name)

		contact = frappe.get_doc("Contact", contact_name)
		self.assertEqual(contact.first_name, "Michael")
		self.assertEqual(contact.last_name, "Jordan")
		self.assertEqual(contact.email_id, "mj@bulls.com")
		self.assertEqual(contact.mobile_no, "+1234567890")
		self.assertEqual(contact.company_name, "Chicago Bulls")
		self.assertEqual(contact.designation, "Player")

	def test_create_organization_from_lead(self):
		"""Test creating an organization from lead data"""
		lead = create_lead(
			first_name="Steve",
			last_name="Jobs",
			email="steve@apple.com",
			organization="Apple Inc",
			website="https://apple.com",
			annual_revenue=1000000,
		)

		org_name = lead.create_organization()
		self.assertTrue(org_name)

		org = frappe.get_doc("CRM Organization", org_name)
		self.assertEqual(org.organization_name, "Apple Inc")
		self.assertEqual(org.website, "https://apple.com")
		self.assertEqual(org.annual_revenue, 1000000)

	def test_create_organization_with_existing_org(self):
		"""Test that existing organization is reused instead of creating duplicate"""
		# Create first lead with organization
		lead1 = create_lead(
			first_name="Person",
			last_name="One",
			email="person1@example.com",
			organization="Existing Corp",
		)
		org_name1 = lead1.create_organization()

		# Create second lead with same organization
		lead2 = create_lead(
			first_name="Person",
			last_name="Two",
			email="person2@example.com",
			organization="Existing Corp",
		)
		org_name2 = lead2.create_organization()

		# Should return the same organization
		self.assertEqual(org_name1, org_name2)

	def test_contact_exists_with_email(self):
		"""Test checking if contact already exists with same email"""
		lead1 = create_lead(
			first_name="John",
			last_name="Existing",
			email="existing@example.com",
			mobile_no="+1111111111",
		)
		lead1.create_contact()

		lead2 = create_lead(
			first_name="Jane",
			last_name="Duplicate",
			email="existing@example.com",
			mobile_no="+2222222222",
		)

		# Should throw error as contact with same email exists
		with self.assertRaises(frappe.exceptions.ValidationError) as context:
			lead2.create_contact()
		self.assertIn("Contact already exists", str(context.exception))

	def test_contact_not_reused_when_only_phone_matches(self):
		"""A different person sharing only a phone must not be reused as the contact"""
		lead1 = create_lead(
			first_name="Jane",
			last_name="Doe",
			email="frappe@example.com",
			mobile_no="+910000000099",
		)
		existing_contact = lead1.create_contact()

		# Different person, no email, but the same mobile number
		lead2 = create_lead(
			first_name="John",
			last_name="Doe",
			mobile_no="+910000000099",
		)
		contact_name = lead2.create_contact()

		self.assertNotEqual(contact_name, existing_contact)
		contact = frappe.get_doc("Contact", contact_name)
		self.assertEqual(contact.first_name, "John")
		self.assertEqual(contact.last_name, "Doe")

	def test_convert_lead_to_deal(self):
		"""Test converting a lead to a deal with new contact and organization"""
		lead = create_lead(
			first_name="Deal",
			last_name="Maker",
			email="dealmaker@example.com",
			mobile_no="+1234567890",
			organization="Deal Corp",
			annual_revenue=500000,
		)

		# Convert lead to deal
		deal_name = lead.convert_to_deal()
		self.assertTrue(deal_name)

		# Verify lead is marked as converted
		lead.reload()
		self.assertEqual(lead.converted, 1)

		# Verify deal was created
		deal = frappe.get_doc("CRM Deal", deal_name)
		self.assertEqual(deal.first_name, "Deal")
		self.assertEqual(deal.last_name, "Maker")
		self.assertEqual(deal.lead, lead.name)
		self.assertTrue(deal.organization)

		# Verify contact was created
		self.assertTrue(len(deal.contacts) > 0)
		contact_name = deal.contacts[0].contact
		contact = frappe.get_doc("Contact", contact_name)
		self.assertEqual(contact.first_name, "Deal")
		self.assertEqual(contact.last_name, "Maker")
		self.assertEqual(contact.email_id, "dealmaker@example.com")

		# Verify organization was created
		org = frappe.get_doc("CRM Organization", deal.organization)
		self.assertEqual(org.organization_name, "Deal Corp")
		self.assertEqual(org.annual_revenue, 500000)

	def test_convert_lead_with_existing_contact_and_org(self):
		"""Test converting lead with existing contact and organization"""
		# Create existing contact
		existing_contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": "Existing",
				"last_name": "Contact",
				"email_ids": [{"email_id": "existing@contact.com", "is_primary": 1}],
			}
		).insert()

		# Create existing organization
		existing_org = frappe.get_doc(
			{
				"doctype": "CRM Organization",
				"organization_name": "Existing Org Inc",
				"annual_revenue": 2000000,
			}
		).insert()

		# Create lead
		lead = create_lead(
			first_name="Existing",
			last_name="Contact",
			email="existing@contact.com",
			organization="Existing Org Inc",
		)

		# Convert lead using existing contact and org
		deal_name = lead.convert_to_deal()

		# Verify deal was created with existing records
		deal = frappe.get_doc("CRM Deal", deal_name)
		self.assertTrue(deal.name)

		# Verify existing contact is linked
		self.assertTrue(len(deal.contacts) > 0)
		self.assertEqual(deal.contacts[0].contact, existing_contact.name)

		# Verify existing organization is linked
		self.assertEqual(deal.organization, existing_org.name)

	def test_contact_gets_organization_link_on_convert(self):
		"""A contact created during conversion points at the resolved organization"""
		create_contact_organization_link_field()

		lead = create_lead(
			first_name="Linked",
			last_name="Person",
			email="linked@orglink.com",
			organization="Org Link Corp",
		)

		deal = frappe.get_doc("CRM Deal", lead.convert_to_deal())
		contact = frappe.get_doc("Contact", deal.contacts[0].contact)

		self.assertTrue(deal.organization)
		self.assertEqual(contact.get(CONTACT_ORGANIZATION_LINK_FIELD), deal.organization)

	def test_existing_contact_organization_not_overwritten(self):
		"""Reusing a contact must not repoint the organization it already has"""
		create_contact_organization_link_field()

		other_org = frappe.get_doc(
			{"doctype": "CRM Organization", "organization_name": "Other Org Ltd"}
		).insert()

		existing_contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": "Reused",
				"last_name": "Contact",
				"email_ids": [{"email_id": "reused@orglink.com", "is_primary": 1}],
				CONTACT_ORGANIZATION_LINK_FIELD: other_org.name,
			}
		).insert()

		lead = create_lead(
			first_name="Reused",
			last_name="Contact",
			email="reused@orglink.com",
			organization="Different Corp",
		)

		deal = frappe.get_doc("CRM Deal", lead.convert_to_deal())

		self.assertEqual(deal.contacts[0].contact, existing_contact.name)
		existing_contact.reload()
		self.assertEqual(existing_contact.get(CONTACT_ORGANIZATION_LINK_FIELD), other_org.name)

	def test_convert_reuses_existing_organization(self):
		"""Converting reuses a matching organization instead of duplicating it"""
		existing_org = frappe.get_doc(
			{"doctype": "CRM Organization", "organization_name": "Dedup Corp"}
		).insert()

		lead = create_lead(
			first_name="Dedup",
			last_name="Tester",
			email="dedup@example.com",
			organization="Dedup Corp",
		)

		deal = frappe.get_doc("CRM Deal", lead.convert_to_deal())

		self.assertEqual(deal.organization, existing_org.name)
		self.assertEqual(frappe.db.count("CRM Organization", {"organization_name": "Dedup Corp"}), 1)

	def test_convert_to_deal_api(self):
		"""Test convert_to_deal API function"""
		lead = create_lead(
			first_name="API",
			last_name="Test",
			email="apitest@example.com",
			mobile_no="+5555555555",
			organization="API Test Corp",
			annual_revenue=300000,
		)

		# Convert lead to deal using API
		deal_name = convert_to_deal(lead=lead.name)
		self.assertTrue(deal_name)

		# Verify lead is marked as converted
		lead.reload()
		self.assertEqual(lead.converted, 1)

		# Verify deal was created
		deal = frappe.get_doc("CRM Deal", deal_name)
		self.assertEqual(deal.first_name, "API")
		self.assertEqual(deal.last_name, "Test")
		self.assertEqual(deal.lead, lead.name)
		self.assertTrue(deal.organization)

		# Verify contact was created
		self.assertTrue(len(deal.contacts) > 0)
		contact_name = deal.contacts[0].contact
		contact = frappe.get_doc("Contact", contact_name)
		self.assertEqual(contact.first_name, "API")
		self.assertEqual(contact.email_id, "apitest@example.com")

		# Verify organization was created
		org = frappe.get_doc("CRM Organization", deal.organization)
		self.assertEqual(org.organization_name, "API Test Corp")
		self.assertEqual(org.annual_revenue, 300000)

	def test_convert_to_deal_api_with_existing_records(self):
		"""Test convert_to_deal API with existing contact and organization parameters"""
		# Create existing contact
		existing_contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": "API",
				"last_name": "Contact",
				"email_ids": [{"email_id": "apicontact@example.com", "is_primary": 1}],
			}
		).insert()

		# Create existing organization
		existing_org = frappe.get_doc(
			{
				"doctype": "CRM Organization",
				"organization_name": "API Org Ltd",
				"annual_revenue": 1500000,
			}
		).insert()

		# Create lead
		lead = create_lead(
			first_name="API",
			last_name="Lead",
			email="apilead@example.com",
			organization="Should Be Replaced",
		)

		# Convert lead using API with existing records
		deal_name = convert_to_deal(
			lead=lead.name,
			existing_contact=existing_contact.name,
			existing_organization=existing_org.name,
		)

		# Verify deal was created with existing records
		deal = frappe.get_doc("CRM Deal", deal_name)
		self.assertTrue(deal.name)

		# Verify existing contact is linked
		self.assertTrue(len(deal.contacts) > 0)
		self.assertEqual(deal.contacts[0].contact, existing_contact.name)

		# Verify existing organization is linked
		self.assertEqual(deal.organization, existing_org.name)

	def test_lead_fields_copied_to_deal(self):
		"""Test that relevant lead fields are copied to deal during conversion"""
		lead = create_lead(
			first_name="Copy",
			last_name="Test",
			email="copytest@example.com",
			mobile_no="+9999999999",
			organization="Copy Test Inc",
			website="https://copytest.com",
			annual_revenue=750000,
			job_title="CEO",
		)

		deal_name = lead.convert_to_deal()
		deal = frappe.get_doc("CRM Deal", deal_name)

		# Verify fields are copied
		self.assertEqual(deal.first_name, "Copy")
		self.assertEqual(deal.last_name, "Test")
		self.assertEqual(deal.website, "https://copytest.com")
		self.assertEqual(deal.annual_revenue, 750000)
		self.assertEqual(deal.job_title, "CEO")

	def test_custom_fields_copied_to_deal_by_label(self):
		"""Custom Lead fields map to matching custom Deal fields."""
		create_lead_deal_custom_fields()
		lead = create_lead(
			first_name="Custom",
			organization="Custom Field Inc",
			custom_lead_conversion_region="North",
		)

		deal_name = lead.convert_to_deal()
		deal = frappe.get_doc("CRM Deal", deal_name)

		self.assertEqual(deal.custom_deal_conversion_region, "North")

	def test_assignees_transferred_on_conversion(self):
		"""Test that additional assignees are transferred from lead to deal on conversion"""
		lead = create_lead(
			first_name="Transfer",
			lead_owner="Administrator",
		)

		lead.assign_agent("crm.user1@example.com")

		lead_assignees = lead.get_assigned_users()

		self.assertIn("Administrator", lead_assignees)
		self.assertIn("crm.user1@example.com", lead_assignees)

		deal_name = lead.convert_to_deal()
		deal = frappe.get_doc("CRM Deal", deal_name)

		deal_assignees = deal.get_assigned_users()
		self.assertIn("Administrator", deal_assignees)
		self.assertIn("crm.user1@example.com", deal_assignees)

	def test_unassigning_the_owner_leaves_lead_owner_unchanged(self):
		"""TXB-106: assignment grants access, not ownership. Cancelling an assignment must
		not touch lead_owner -- only an Admin editing the field, or a Claim Request, may."""
		lead = create_lead(first_name="Owner", lead_owner="crm.user1@example.com")
		self.assertEqual(lead.lead_owner, "crm.user1@example.com")

		assign_remove("CRM Lead", lead.name, "crm.user1@example.com")

		self.assertEqual(frappe.db.get_value("CRM Lead", lead.name, "lead_owner"), "crm.user1@example.com")

	def test_assignment_does_not_override_owner(self):
		"""TXB-106: a new assignment must not take ownership away from the existing owner."""
		lead = create_lead(first_name="Override", lead_owner="crm.user1@example.com")
		assign_add({"assign_to": ["crm.user2@example.com"], "doctype": "CRM Lead", "name": lead.name})
		self.assertEqual(frappe.db.get_value("CRM Lead", lead.name, "lead_owner"), "crm.user1@example.com")

	# --- TXB-125: conversion is restricted to approved pipelines with a server-derived state.

	def test_bulk_conversion_defaults_to_individual_session_submitted(self):
		"""Bulk conversion sends only the lead (no deal payload); the server must land the
		deal on the primary Individual Session pipeline in its Submitted entry state."""
		ensure_deal_statuses()
		lead = create_lead(first_name="Bulk", organization="Bulk Corp")

		deal = frappe.get_doc("CRM Deal", convert_to_deal(lead=lead.name))

		self.assertEqual(deal.pipeline_type, "Individual Session")
		self.assertEqual(deal.status, "Submitted")

	def test_conversion_derives_initial_state_from_pipeline(self):
		"""Each approved pipeline creates a deal in exactly its required initial state."""
		ensure_deal_statuses()
		expected = {
			"Individual Session": "Submitted",
			"Workshop": "Workshop submitted",
			"Selling Training": "Training submitted",
		}
		for pipeline, status in expected.items():
			lead = create_lead(first_name="Pipe", organization=f"{pipeline} Corp")
			deal = frappe.get_doc(
				"CRM Deal",
				convert_to_deal(lead=lead.name, deal={"pipeline_type": pipeline}),
			)
			self.assertEqual(deal.pipeline_type, pipeline)
			self.assertEqual(deal.status, status)

	def test_conversion_ignores_client_supplied_status(self):
		"""A payload cannot override the server-derived initial state, even for an approved
		pipeline -- the direct-API bypass this restriction exists to close."""
		ensure_deal_statuses()
		lead = create_lead(first_name="Override", organization="Override Corp")

		deal = frappe.get_doc(
			"CRM Deal",
			convert_to_deal(
				lead=lead.name,
				deal={"pipeline_type": "Individual Session", "status": "Won"},
			),
		)

		self.assertEqual(deal.status, "Submitted")

	def test_conversion_accepts_json_string_deal_payload(self):
		"""Whitelisted calls may arrive with the deal payload as a JSON string; the same
		derivation must apply so a stringified body is not a way around it."""
		ensure_deal_statuses()
		lead = create_lead(first_name="Json", organization="Json Corp")

		deal = frappe.get_doc(
			"CRM Deal",
			convert_to_deal(
				lead=lead.name,
				deal='{"pipeline_type": "Workshop", "status": "Sold"}',
			),
		)

		self.assertEqual(deal.pipeline_type, "Workshop")
		self.assertEqual(deal.status, "Workshop submitted")

	def test_conversion_rejects_unapproved_pipeline(self):
		"""Selecting a pipeline outside the approved set is refused outright."""
		lead = create_lead(first_name="Blocked", organization="Blocked Corp")

		with self.assertRaises(frappe.exceptions.ValidationError) as ctx:
			convert_to_deal(lead=lead.name, deal={"pipeline_type": "Delivering Coaching"})
		self.assertIn("not an approved pipeline", str(ctx.exception))

		lead.reload()
		self.assertEqual(lead.converted, 0)

	# ── TXB-132: atomic, idempotent, auditable conversion ────────────────────────────────

	def test_conversion_records_result_and_archives_lead(self):
		"""One conversion creates the Contact + one initial Opportunity, records the result
		fields, and archives the Lead."""
		ensure_conversion_result_fields()
		lead = create_lead(first_name="Audit", email="audit@convert.com", organization="Audit Co")

		deal_name = lead.convert_to_deal()

		lead.reload()
		self.assertEqual(lead.converted, 1)
		self.assertEqual(lead.get(FIELD_CONVERTED_DEAL), deal_name)
		self.assertTrue(lead.get(FIELD_CONVERTED_AT))

		deal = frappe.get_doc("CRM Deal", deal_name)
		self.assertEqual(deal.lead, lead.name)
		self.assertEqual(lead.get(FIELD_CONVERTED_CONTACT), deal.contacts[0].contact)

		# Exactly one initial Opportunity for this Lead.
		self.assertEqual(frappe.db.count("CRM Deal", {"lead": lead.name}), 1)

	def test_forced_failure_leaves_nothing_persisted(self):
		"""A failure mid-conversion rolls back every change: no Deal, no Contact, and the Lead
		stays unconverted with no result recorded."""
		ensure_conversion_result_fields()
		lead = create_lead(first_name="Rollback", email="rollback@convert.com", organization="RB Co")

		from crm.fcrm.doctype.crm_lead.crm_lead import CRMLead

		with patch.object(CRMLead, "create_deal", side_effect=RuntimeError("boom")):
			with self.assertRaises(RuntimeError):
				lead.convert_to_deal()

		lead.reload()
		self.assertEqual(lead.converted, 0)
		self.assertFalse(lead.get(FIELD_CONVERTED_DEAL))
		self.assertFalse(lead.get(FIELD_CONVERTED_CONTACT))
		self.assertEqual(frappe.db.count("CRM Deal", {"lead": lead.name}), 0)
		# The Contact created before the failing step was rolled back with the savepoint.
		self.assertFalse(frappe.db.exists("Contact Email", {"email_id": "rollback@convert.com"}))

	def test_repeated_conversion_resolves_to_existing_result(self):
		"""A repeated conversion request returns the already-recorded Opportunity and never
		inserts a second one."""
		ensure_conversion_result_fields()
		lead = create_lead(first_name="Retry", email="retry@convert.com", organization="Retry Co")

		first = lead.convert_to_deal()
		second = convert_to_deal(lead=lead.name)

		self.assertEqual(first, second)
		self.assertEqual(frappe.db.count("CRM Deal", {"lead": lead.name}), 1)

	def test_repeated_conversion_does_not_reinsert_when_result_authority_holds(self):
		"""The persisted result is the retry authority: even if the create path is forced to
		run again it must not be reached once a live Opportunity is already recorded."""
		ensure_conversion_result_fields()
		lead = create_lead(first_name="Once", email="once@convert.com", organization="Once Co")

		first = lead.convert_to_deal()

		from crm.fcrm.doctype.crm_lead.crm_lead import CRMLead

		# A second attempt must short-circuit on the recorded result before create_deal.
		with patch.object(CRMLead, "create_deal", side_effect=AssertionError("must not reinsert")):
			second = convert_to_deal(lead=lead.name)

		self.assertEqual(first, second)
		self.assertEqual(frappe.db.count("CRM Deal", {"lead": lead.name}), 1)

	def test_converted_lead_is_readable_but_rejects_user_edits(self):
		"""An archived (converted) Lead can still be read, but a user-originated save is
		refused."""
		ensure_conversion_result_fields()
		lead = create_lead(first_name="Archived", email="archived@convert.com", organization="Arc Co")
		lead.convert_to_deal()

		# Readable.
		reloaded = frappe.get_doc("CRM Lead", lead.name)
		self.assertEqual(reloaded.converted, 1)

		# User-originated mutation is rejected.
		reloaded.job_title = "Edited"
		with self.assertRaises(frappe.exceptions.ValidationError) as ctx:
			reloaded.save()
		self.assertIn("converted", str(ctx.exception).lower())

	def test_later_opportunity_may_reference_contact_and_archived_lead(self):
		"""A later, independently created Opportunity may still reference both the conversion
		Contact and the archived Lead -- CRM Deal.lead stays non-unique."""
		ensure_conversion_result_fields()
		ensure_deal_statuses()
		lead = create_lead(first_name="Prov", email="prov@convert.com", organization="Prov Co")
		lead.convert_to_deal()
		lead.reload()

		contact = lead.get(FIELD_CONVERTED_CONTACT)
		later = frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"pipeline_type": PIPELINE_INDIVIDUAL_SESSION,
				"status": "Submitted",
				"lead": lead.name,
				"contacts": [{"contact": contact}],
			}
		).insert(ignore_permissions=True)

		self.assertEqual(later.lead, lead.name)
		self.assertEqual(later.contacts[0].contact, contact)
		# Two Opportunities now reference the one archived Lead.
		self.assertEqual(frappe.db.count("CRM Deal", {"lead": lead.name}), 2)


def ensure_conversion_result_fields():
	"""Install the TXB-132 conversion-result fields, mirroring the registered patch.

	The conversion code guards on `has_field`, so without these the result would be silently
	skipped and the assertions would pass vacuously. Idempotent -- safe to call per test.
	"""
	from crm.patches.v1_0.add_conversion_result_fields import execute

	execute()


def ensure_deal_statuses():
	"""Guarantee the entry statuses the conversion mapping requires exist as CRM Deal Status
	records, so the deal's status Link validation passes regardless of seed state."""
	for status in ("Submitted", "Workshop submitted", "Training submitted"):
		if not frappe.db.exists("CRM Deal Status", status):
			frappe.get_doc({"doctype": "CRM Deal Status", "deal_status": status}).insert(
				ignore_permissions=True
			)


def create_lead(**kwargs):
	"""Helper function to create a CRM Lead for testing.

	`last_name` and `email` are both reqd on this site through Property Setters, which are
	invisible in crm_lead.json -- without defaults every caller fails in
	`_validate_mandatory` before reaching the behaviour under test. The email carries a hash
	so `prevent_duplicate`, which rejects a lead matching an existing first name, last name
	and email, never fires between fixtures.
	"""
	data = {"doctype": "CRM Lead"}
	data.update(kwargs)
	data.setdefault("last_name", "Test")
	data.setdefault("email", f"lead-{frappe.generate_hash(length=8)}@example.com")
	return frappe.get_doc(data).insert()


def create_lead_deal_custom_fields():
	create_custom_fields(
		{
			"CRM Lead": [conversion_region_field("custom_lead_conversion_region")],
			"CRM Deal": [conversion_region_field("custom_deal_conversion_region")],
		},
		ignore_validate=True,
	)
	frappe.clear_cache(doctype="CRM Lead")
	frappe.clear_cache(doctype="CRM Deal")


def create_contact_organization_link_field():
	"""Mirror the site's Contact -> CRM Organization custom field.

	The conversion code guards on `has_field`, so without this the link is silently skipped
	and the assertions would pass vacuously.
	"""
	create_custom_fields(
		{
			"Contact": [
				{
					"fieldname": CONTACT_ORGANIZATION_LINK_FIELD,
					"fieldtype": "Link",
					"options": "CRM Organization",
					"insert_after": "company_name",
					"label": "Organization",
				}
			]
		},
		ignore_validate=True,
	)
	frappe.clear_cache(doctype="Contact")


def conversion_region_field(fieldname):
	return {
		"fieldname": fieldname,
		"fieldtype": "Data",
		"insert_after": "source",
		"label": "Conversion Region",
	}


def add_activity_note(doctype, name, title, content="body"):
	return frappe.get_doc(
		{
			"doctype": "FCRM Note",
			"title": title,
			"content": content,
			"reference_doctype": doctype,
			"reference_docname": name,
		}
	).insert(ignore_permissions=True)


def activity_note_titles(notes):
	return [note.get("title") for note in notes]


class TestContactActivities(FrappeTestCase):
	"""Regression tests for the unified Contact Activity Log read model (TXB-132).

	The aggregate spans every distinct archived Lead associated with a Contact (its
	pre-conversion history) and every linked Opportunity (its post-conversion history). These
	pin the three acceptance guarantees: deterministic, content-preserving chronology across
	multiple Leads and Opportunities (ac-1); read-once-per-record dedup with source/route/phase
	tagging even when several Opportunities reference the same Lead (ac-2); and a single
	Contact-level authorization seam that leaves the direct Lead/Deal endpoints' own permission
	checks intact (ac-3).
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_conversion_result_fields()
		ensure_deal_statuses()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _contact_of(self, lead):
		return frappe.db.get_value("CRM Lead", lead.name, FIELD_CONVERTED_CONTACT)

	# -- ac-1: multi-Lead / multi-Opportunity aggregate, chronology, preservation -----------

	def test_contact_activities_aggregate_multiple_leads_and_opportunities(self):
		"""A Contact with two archived Leads and two linked Opportunities returns every
		source's pre- and post-conversion records, preserving original content and authors."""
		lead_a = create_lead(first_name="Ann", email="ann@ex.com", organization="Ann Co")
		add_activity_note("CRM Lead", lead_a.name, "Lead A note", content="reached out")
		deal_a = lead_a.convert_to_deal()
		contact = self._contact_of(lead_a)
		self.assertTrue(contact)
		add_activity_note("CRM Deal", deal_a, "Deal A note")

		lead_b = create_lead(first_name="Ann", email="ann-b@ex.com", organization="Ann Co")
		add_activity_note("CRM Lead", lead_b.name, "Lead B note")
		deal_b = convert_to_deal(lead=lead_b.name, existing_contact=contact)

		activities, calls, notes, tasks, attachments = activities_api.get_contact_activities(contact)

		# Both archived Leads and both Opportunities contribute their notes, once each.
		self.assertEqual(
			sorted(activity_note_titles(notes)),
			["Deal A note", "Lead A note", "Lead B note"],
		)

		# Every source produced its creation activity, tagged pre/post-conversion correctly.
		lead_creations = [
			a
			for a in activities
			if a["activity_type"] == "creation" and a["source_doctype"] == "CRM Lead"
		]
		deal_creations = [
			a
			for a in activities
			if a["activity_type"] == "creation" and a["source_doctype"] == "CRM Deal"
		]
		self.assertEqual({a["source_docname"] for a in lead_creations}, {lead_a.name, lead_b.name})
		self.assertEqual({a["source_docname"] for a in deal_creations}, {deal_a, deal_b})
		self.assertTrue(all(a["phase"] == activities_api.PHASE_PRE_CONVERSION for a in lead_creations))
		self.assertTrue(all(a["phase"] == activities_api.PHASE_POST_CONVERSION for a in deal_creations))

		# Original content and author are preserved verbatim on the Lead note.
		lead_a_note = next(n for n in notes if n["title"] == "Lead A note")
		self.assertEqual(lead_a_note["content"], "reached out")
		self.assertEqual(lead_a_note["owner"], "Administrator")
		self.assertEqual(lead_a_note["source_docname"], lead_a.name)
		self.assertEqual(lead_a_note["phase"], activities_api.PHASE_PRE_CONVERSION)

	def test_contact_activities_chronology_is_deterministic_and_newest_first(self):
		"""The feed is ordered newest-first and identical across repeated calls."""
		lead = create_lead(first_name="Chr", email="chr@ex.com", organization="Chr Co")
		add_activity_note("CRM Lead", lead.name, "n1")
		deal = lead.convert_to_deal()
		contact = self._contact_of(lead)
		add_activity_note("CRM Deal", deal, "n2")

		first = activities_api.get_contact_activities(contact)[0]
		second = activities_api.get_contact_activities(contact)[0]

		def identity(feed):
			return [(a["activity_type"], a.get("source_docname"), str(a["creation"])) for a in feed]

		# Deterministic: same identities in the same order on every call.
		self.assertEqual(identity(first), identity(second))

		# Non-increasing creation timestamps (newest first).
		creations = [a["creation"] for a in first]
		self.assertEqual(creations, sorted(creations, reverse=True))

	# -- ac-2: dedup by stable identity + source/route/phase metadata -----------------------

	def test_contact_activities_read_shared_lead_once_across_opportunities(self):
		"""When several Opportunities reference the same archived Lead, each underlying record
		appears exactly once."""
		lead = create_lead(first_name="Dee", email="dee@ex.com", organization="Dee Co")
		add_activity_note("CRM Lead", lead.name, "shared lead note")
		deal_a = lead.convert_to_deal()
		contact = self._contact_of(lead)

		# A second, later Opportunity referencing the same Lead and Contact (CRM Deal.lead is
		# non-unique). The Lead is now reachable three ways: converted_contact and two Deal.lead.
		frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"pipeline_type": PIPELINE_INDIVIDUAL_SESSION,
				"status": "Submitted",
				"lead": lead.name,
				"contacts": [{"contact": contact}],
			}
		).insert(ignore_permissions=True)

		_, _, notes, _, _ = activities_api.get_contact_activities(contact)

		# The Lead note appears once despite the Lead being reachable through both Opportunities.
		self.assertEqual(activity_note_titles(notes).count("shared lead note"), 1)
		# The Lead's own creation activity is likewise emitted once.
		lead_creations = [
			a
			for a in activities_api.get_contact_activities(contact)[0]
			if a["activity_type"] == "creation" and a["source_docname"] == lead.name
		]
		self.assertEqual(len(lead_creations), 1)

	def test_contact_activities_identify_source_route_and_phase(self):
		"""Every result identifies its source record, source route, source doctype, and
		pre/post-conversion phase."""
		lead = create_lead(first_name="Eve", email="eve@ex.com", organization="Eve Co")
		add_activity_note("CRM Lead", lead.name, "lead note")
		deal = lead.convert_to_deal()
		contact = self._contact_of(lead)
		add_activity_note("CRM Deal", deal, "deal note")

		streams = activities_api.get_contact_activities(contact)
		every_record = [r for stream in streams for r in stream if isinstance(r, dict)]

		for record in every_record:
			self.assertIn(record["source_doctype"], ("CRM Lead", "CRM Deal"))
			self.assertIn(
				record["phase"],
				(activities_api.PHASE_PRE_CONVERSION, activities_api.PHASE_POST_CONVERSION),
			)
			prefix = "leads" if record["source_doctype"] == "CRM Lead" else "deals"
			self.assertEqual(record["source_route"], f"{prefix}/{record['source_docname']}")

		notes = streams[2]
		lead_note = next(n for n in notes if n["title"] == "lead note")
		deal_note = next(n for n in notes if n["title"] == "deal note")
		self.assertEqual(lead_note["source_doctype"], "CRM Lead")
		self.assertEqual(lead_note["source_docname"], lead.name)
		self.assertEqual(lead_note["source_route"], f"leads/{lead.name}")
		self.assertEqual(lead_note["phase"], activities_api.PHASE_PRE_CONVERSION)
		self.assertEqual(deal_note["source_doctype"], "CRM Deal")
		self.assertEqual(deal_note["source_docname"], deal)
		self.assertEqual(deal_note["source_route"], f"deals/{deal}")
		self.assertEqual(deal_note["phase"], activities_api.PHASE_POST_CONVERSION)

	# -- ac-3: one centralized authorization seam; direct endpoints unchanged ----------------

	def test_contact_activities_gated_only_by_contact_permission(self):
		"""The aggregate is gated by the single Contact read seam."""
		lead = create_lead(first_name="Fay", email="fay@ex.com", organization="Fay Co")
		lead.convert_to_deal()
		contact = self._contact_of(lead)

		def deny_contact(doctype, ptype=None, doc=None, *a, **k):
			return doctype != "Contact"

		with patch("frappe.has_permission", side_effect=deny_contact):
			with self.assertRaises(frappe.PermissionError):
				activities_api.get_contact_activities(contact)

	def test_contact_activities_not_filtered_by_source_permissions(self):
		"""With Contact read allowed, the aggregate still surfaces Lead- and Deal-sourced
		records even when the caller lacks direct Lead/Deal read -- source-level filtering is a
		deliberate non-goal, so authorization stays centralized at the one Contact seam."""
		lead = create_lead(first_name="Gus", email="gus@ex.com", organization="Gus Co")
		add_activity_note("CRM Lead", lead.name, "lead note")
		deal = lead.convert_to_deal()
		contact = self._contact_of(lead)
		add_activity_note("CRM Deal", deal, "deal note")

		def only_contact(doctype, ptype=None, doc=None, *a, **k):
			return doctype == "Contact"

		with patch("frappe.has_permission", side_effect=only_contact):
			_, _, notes, _, _ = activities_api.get_contact_activities(contact)

		self.assertEqual(sorted(activity_note_titles(notes)), ["deal note", "lead note"])

	def test_direct_lead_activities_endpoint_keeps_its_permission_check(self):
		"""The centralized seam does not weaken the direct Lead endpoint's own check."""
		lead = create_lead(first_name="Hal", email="hal@ex.com", organization="Hal Co")

		def deny_lead(doctype, ptype=None, doc=None, *a, **k):
			return doctype != "CRM Lead"

		with patch("frappe.has_permission", side_effect=deny_lead):
			with self.assertRaises(frappe.PermissionError):
				activities_api.get_lead_activities(lead.name)

	def test_direct_deal_activities_endpoint_keeps_its_permission_check(self):
		"""The centralized seam does not weaken the direct Deal endpoint's own check."""
		lead = create_lead(first_name="Ivy", email="ivy@ex.com", organization="Ivy Co")
		deal = lead.convert_to_deal()

		def deny_deal(doctype, ptype=None, doc=None, *a, **k):
			return doctype != "CRM Deal"

		with patch("frappe.has_permission", side_effect=deny_deal):
			with self.assertRaises(frappe.PermissionError):
				activities_api.get_deal_activities(deal)

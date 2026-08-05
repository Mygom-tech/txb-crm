# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-106: who owns a record, and who may change that.

Ownership decides commission, so it is enforced on the document lifecycle rather than in
any one screen -- `before_insert` decides the initial owner, `validate` refuses later
changes. These tests exercise the hooks directly, since the point is that every write path
reaches them.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.txb.constants import ADMIN_ROLE

SALESMAN = "txb-owner-sales@example.com"
OTHER_SALESMAN = "txb-owner-sales2@example.com"
ADMIN = "txb-owner-admin@example.com"


def ensure_user(email: str, roles: list[str]):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)

	user = frappe.get_doc("User", email)
	user.add_roles(*roles)
	return user


class OwnershipTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(SALESMAN, ["Sales User"])
		ensure_user(OTHER_SALESMAN, ["Sales User"])
		ensure_user(ADMIN, ["Sales User", ADMIN_ROLE])
		frappe.db.commit()  # nosemgrep -- roles must outlive per-test rollback

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def make_lead(self, **kwargs):
		# email and last_name are both reqd on this site via Property Setter, so the
		# fixture supplies them; either missing fails in _validate_mandatory before the
		# ownership hook is reached.
		return frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Owner",
				"last_name": "Test",
				"email": "txb-owner-lead@example.com",
				**kwargs,
			}
		).insert(ignore_permissions=True)

	def make_deal(self, **kwargs):
		# pipeline_type is always explicit: the Select has no blank option and no default,
		# so Frappe's _set_defaults would otherwise silently fill "Individual Session".
		return frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"pipeline_type": "Individual Session",
				"status": "Submitted",
				**kwargs,
			}
		).insert(ignore_permissions=True)

	def make_contact(self, **kwargs):
		return frappe.get_doc(
			{"doctype": "Contact", "first_name": "Owner", "last_name": "Test", **kwargs}
		).insert(ignore_permissions=True)


class TestOwnerOnInsert(OwnershipTestCase):
	def test_lead_creator_becomes_owner(self):
		frappe.set_user(SALESMAN)
		self.assertEqual(self.make_lead().lead_owner, SALESMAN)

	def test_deal_creator_becomes_owner(self):
		frappe.set_user(SALESMAN)
		self.assertEqual(self.make_deal().deal_owner, SALESMAN)

	def test_contact_creator_becomes_owner(self):
		frappe.set_user(SALESMAN)
		self.assertEqual(self.make_contact().custom_contact_owner, SALESMAN)

	def test_a_non_admin_cannot_nominate_someone_else_at_creation(self):
		"""The ticket is explicit: the creator owns it, whatever the client sent."""
		frappe.set_user(SALESMAN)
		deal = self.make_deal(deal_owner=OTHER_SALESMAN)
		self.assertEqual(deal.deal_owner, SALESMAN)

	def test_an_admin_may_nominate_someone_else_at_creation(self):
		frappe.set_user(ADMIN)
		deal = self.make_deal(deal_owner=SALESMAN)
		self.assertEqual(deal.deal_owner, SALESMAN)

	def test_an_admin_who_nominates_nobody_owns_it(self):
		frappe.set_user(ADMIN)
		self.assertEqual(self.make_deal().deal_owner, ADMIN)

	def test_guest_never_becomes_an_owner(self):
		"""The public registration endpoint runs as Guest and sets deal_owner itself,
		carrying it over from the source deal. Overwriting it with "Guest" would be wrong."""
		frappe.set_user("Guest")
		try:
			deal = self.make_deal(deal_owner=SALESMAN)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(deal.deal_owner, SALESMAN)


class TestOwnerChangeGuard(OwnershipTestCase):
	def test_a_salesman_cannot_take_an_owned_deal(self):
		deal = self.make_deal(deal_owner=OTHER_SALESMAN)

		frappe.set_user(SALESMAN)
		deal.reload()
		deal.deal_owner = SALESMAN

		with self.assertRaises(frappe.PermissionError):
			deal.save(ignore_permissions=True)

	def test_a_salesman_cannot_take_an_unowned_deal(self):
		"""The hole in the script this replaces: it returned early when there was no
		previous owner, so anyone could claim an unowned record."""
		deal = self.make_deal()
		frappe.db.set_value("CRM Deal", deal.name, "deal_owner", "")

		frappe.set_user(SALESMAN)
		deal.reload()
		deal.deal_owner = SALESMAN

		with self.assertRaises(frappe.PermissionError):
			deal.save(ignore_permissions=True)

	def test_a_salesman_cannot_take_an_owned_lead(self):
		lead = self.make_lead(lead_owner=OTHER_SALESMAN)

		frappe.set_user(SALESMAN)
		lead.reload()
		lead.lead_owner = SALESMAN

		with self.assertRaises(frappe.PermissionError):
			lead.save(ignore_permissions=True)

	def test_a_salesman_cannot_take_an_owned_contact(self):
		contact = self.make_contact(custom_contact_owner=OTHER_SALESMAN)

		frappe.set_user(SALESMAN)
		contact.reload()
		contact.custom_contact_owner = SALESMAN

		with self.assertRaises(frappe.PermissionError):
			contact.save(ignore_permissions=True)

	def test_an_admin_may_change_any_owner(self):
		deal = self.make_deal(deal_owner=OTHER_SALESMAN)

		frappe.set_user(ADMIN)
		deal.reload()
		deal.deal_owner = SALESMAN
		deal.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("CRM Deal", deal.name, "deal_owner"), SALESMAN)

	def test_a_salesman_may_still_edit_other_fields_on_a_deal_they_own(self):
		"""Day-to-day work must not break; the ticket restricts one field, not the record."""
		deal = self.make_deal(deal_owner=SALESMAN)

		frappe.set_user(SALESMAN)
		deal.reload()
		deal.next_step = "Call them back"
		deal.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("CRM Deal", deal.name, "next_step"), "Call them back")

	def test_saving_without_touching_the_owner_is_allowed(self):
		"""A client that round-trips the whole document must not trip the guard."""
		deal = self.make_deal(deal_owner=OTHER_SALESMAN)

		frappe.set_user(SALESMAN)
		deal.reload()
		deal.deal_owner = OTHER_SALESMAN  # unchanged
		deal.next_step = "Unchanged owner"
		deal.save(ignore_permissions=True)

		self.assertEqual(
			frappe.db.get_value("CRM Deal", deal.name, "deal_owner"), OTHER_SALESMAN
		)

	def test_the_error_names_the_claim_request(self):
		"""The DoD asks for a clear error, not a silent revert."""
		deal = self.make_deal(deal_owner=OTHER_SALESMAN)

		frappe.set_user(SALESMAN)
		deal.reload()
		deal.deal_owner = SALESMAN

		with self.assertRaises(frappe.PermissionError) as caught:
			deal.save(ignore_permissions=True)

		self.assertIn("Request Ownership", str(caught.exception))


class TestConversionOwnership(OwnershipTestCase):
	# CRM Lead.organization is a Link to CRM Organization on this site via Property Setter
	# (fieldtype Data in crm_lead.json, overridden here), unlike the stock app. That makes
	# `make_lead(organization=...)` hit Document._validate_links during the lead's own
	# insert, before conversion runs at all -- so every organization name a fixture below
	# passes must already exist as a CRM Organization.
	def setUp(self):
		super().setUp()
		for name in ("Convert Co", "Convert Admin Co", "Contact Co", "Not Mine Co", "Reuse Co"):
			if not frappe.db.exists("CRM Organization", name):
				frappe.get_doc({"doctype": "CRM Organization", "organization_name": name}).insert(
					ignore_permissions=True
				)

	def convert(self, lead):
		from crm.fcrm.doctype.crm_lead.crm_lead import convert_to_deal

		return convert_to_deal(lead=lead.name, deal={"pipeline_type": "Individual Session"})

	def test_the_converter_owns_the_new_deal(self):
		lead = self.make_lead(lead_owner=OTHER_SALESMAN, organization="Convert Co")

		frappe.set_user(SALESMAN)
		deal_name = self.convert(lead)

		self.assertEqual(frappe.db.get_value("CRM Deal", deal_name, "deal_owner"), SALESMAN)

	def test_an_admin_converting_also_owns_the_new_deal(self):
		"""LEAD_DEAL_FIELD_MAP used to carry lead_owner across, which the insert hook reads
		as an Admin's deliberate nomination -- so the Admin path silently kept the lead's
		owner. The map entry is gone."""
		lead = self.make_lead(lead_owner=OTHER_SALESMAN, organization="Convert Admin Co")

		frappe.set_user(ADMIN)
		deal_name = self.convert(lead)

		self.assertEqual(frappe.db.get_value("CRM Deal", deal_name, "deal_owner"), ADMIN)

	def test_the_converter_owns_the_new_contact(self):
		lead = self.make_lead(
			lead_owner=OTHER_SALESMAN,
			organization="Contact Co",
			email="convert-owner@example.com",
		)

		frappe.set_user(SALESMAN)
		self.convert(lead)

		# Contact's email lives in the `Contact Email` child table, not on the parent.
		parents = frappe.get_all(
			"Contact Email", filters={"email_id": "convert-owner@example.com"}, pluck="parent"
		)
		self.assertEqual(len(parents), 1)
		self.assertEqual(
			frappe.db.get_value("Contact", parents[0], "custom_contact_owner"), SALESMAN
		)

	def test_conversion_is_not_blocked_by_not_owning_the_lead(self):
		"""Explicitly required: conversion must not depend on source ownership."""
		lead = self.make_lead(lead_owner=OTHER_SALESMAN, organization="Not Mine Co")

		frappe.set_user(SALESMAN)
		self.assertTrue(self.convert(lead))

	def test_an_existing_contact_keeps_its_owner_through_conversion(self):
		"""A reused Contact is not a new record, so its owner is not up for grabs.

		The two records share an email but not a name, and both halves matter.
		`CRMLead.contact_exists` matches on email alone, so a differing email would make
		conversion mint a *new* contact and this assertion would pass without testing
		anything. A matching name as well would trip `prevent_duplicate`, which rejects a
		lead whose first name, last name and email all match an existing Contact.
		"""
		self.make_contact(
			first_name="Reused",
			last_name="Person",
			custom_contact_owner=OTHER_SALESMAN,
			email_ids=[{"email_id": "reused-owner@example.com", "is_primary": 1}],
		)
		lead = self.make_lead(
			first_name="Different",
			last_name="Spelling",
			email="reused-owner@example.com",
			lead_owner=OTHER_SALESMAN,
			organization="Reuse Co",
		)

		frappe.set_user(SALESMAN)
		self.convert(lead)

		contacts = frappe.get_all(
			"Contact",
			filters={"name": ("in", frappe.get_all(
				"Contact Email", filters={"email_id": "reused-owner@example.com"}, pluck="parent"
			))},
			fields=["name", "custom_contact_owner"],
		)
		self.assertEqual(len(contacts), 1, "conversion should reuse the contact, not mint one")
		self.assertEqual(contacts[0].custom_contact_owner, OTHER_SALESMAN)


class TestOwnerFieldRendering(OwnershipTestCase):
	def field(self, fieldname="deal_owner"):
		return frappe._dict({"fieldname": fieldname, "read_only": 0, "permlevel": 0})

	def test_a_salesman_sees_the_owner_read_only(self):
		from crm.txb.ownership import restrict_owner_field

		frappe.set_user(SALESMAN)
		field = self.field()
		restrict_owner_field(field, "CRM Deal")

		self.assertEqual(field.read_only, 1)

	def test_an_admin_may_still_edit_it(self):
		from crm.txb.ownership import restrict_owner_field

		frappe.set_user(ADMIN)
		field = self.field()
		restrict_owner_field(field, "CRM Deal")

		self.assertEqual(field.read_only, 0)

	def test_other_fields_are_untouched(self):
		from crm.txb.ownership import restrict_owner_field

		frappe.set_user(SALESMAN)
		field = self.field("next_step")
		restrict_owner_field(field, "CRM Deal")

		self.assertEqual(field.read_only, 0)

	def test_the_contact_owner_is_covered_too(self):
		from crm.txb.ownership import restrict_owner_field

		frappe.set_user(SALESMAN)
		field = self.field("custom_contact_owner")
		restrict_owner_field(field, "Contact")

		self.assertEqual(field.read_only, 1)

	def test_a_doctype_with_no_owner_concept_is_untouched(self):
		from crm.txb.ownership import restrict_owner_field

		frappe.set_user(SALESMAN)
		field = self.field("organization_name")
		restrict_owner_field(field, "CRM Organization")

		self.assertEqual(field.read_only, 0)


class TestAssignmentDoesNotChangeOwner(OwnershipTestCase):
	"""crm/api/todo.py used to mirror assignment onto deal_owner/lead_owner via
	frappe.db.set_value, bypassing guard_owner_change entirely. Exercised here through
	frappe.desk.form.assign_to, the same path the UI takes, so a regression here means the
	real assign/unassign buttons are open again."""

	def test_assigning_a_non_owner_leaves_deal_owner_unchanged(self):
		from frappe.desk.form.assign_to import add as assign_add

		deal = self.make_deal(deal_owner=OTHER_SALESMAN)

		frappe.set_user(ADMIN)
		assign_add({"assign_to": [SALESMAN], "doctype": "CRM Deal", "name": deal.name})

		self.assertEqual(frappe.db.get_value("CRM Deal", deal.name, "deal_owner"), OTHER_SALESMAN)

	def test_cancelling_an_assignment_leaves_deal_owner_unchanged(self):
		from frappe.desk.form.assign_to import add as assign_add
		from frappe.desk.form.assign_to import remove as assign_remove

		deal = self.make_deal(deal_owner=OTHER_SALESMAN)

		frappe.set_user(ADMIN)
		assign_add({"assign_to": [SALESMAN], "doctype": "CRM Deal", "name": deal.name})
		assign_remove("CRM Deal", deal.name, SALESMAN)

		self.assertEqual(frappe.db.get_value("CRM Deal", deal.name, "deal_owner"), OTHER_SALESMAN)

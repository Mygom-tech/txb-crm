# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""Tests for logic ported out of Server Scripts.

The handlers are deliberately written against a plain document interface, so the pure
branches can be exercised without a database. That matters here: the app targets Frappe
v16 in CI while production runs v15, so the more logic that is verifiable without the
test runner, the better.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.txb.doc_events.call_log import default_phone_numbers
from crm.txb.doc_events.contact import sync_organization
from crm.txb.doc_events.deal import primary_contact, sync_contact_name
from crm.txb.doc_events.lead import default_disqualified_reason


class FakeDoc:
	"""Minimal document stand-in supporting get/set and attribute access."""

	def __init__(self, **fields):
		self.__dict__.update(fields)

	def get(self, fieldname, default=None):
		return self.__dict__.get(fieldname, default)

	def set(self, fieldname, value):
		self.__dict__[fieldname] = value


class FakeContactRow:
	def __init__(self, contact, is_primary=0):
		self.contact = contact
		self.is_primary = is_primary


class TestCallLogEvents(FrappeTestCase):
	def test_missing_numbers_get_placeholder(self):
		"""An empty from/to crashes the frontend, so both are backfilled."""
		doc = FakeDoc(**{"from": None, "to": ""})
		default_phone_numbers(doc)
		self.assertEqual(doc.get("from"), "-")
		self.assertEqual(doc.get("to"), "-")

	def test_existing_numbers_are_untouched(self):
		doc = FakeDoc(**{"from": "+37060000000", "to": "+37061111111"})
		default_phone_numbers(doc)
		self.assertEqual(doc.get("from"), "+37060000000")
		self.assertEqual(doc.get("to"), "+37061111111")


class TestLeadEvents(FrappeTestCase):
	def test_disqualified_lead_gets_a_default_reason(self):
		doc = FakeDoc(status="Disqualified", lost_reason=None)
		default_disqualified_reason(doc)
		self.assertEqual(doc.lost_reason, "Pending Review")

	def test_existing_reason_is_kept(self):
		doc = FakeDoc(status="Disqualified", lost_reason="Budget")
		default_disqualified_reason(doc)
		self.assertEqual(doc.lost_reason, "Budget")

	def test_other_statuses_are_untouched(self):
		doc = FakeDoc(status="New", lost_reason=None)
		default_disqualified_reason(doc)
		self.assertIsNone(doc.lost_reason)


class TestDealEvents(FrappeTestCase):
	def test_primary_contact_prefers_the_primary_flag(self):
		doc = FakeDoc(contacts=[FakeContactRow("Second"), FakeContactRow("First", is_primary=1)])
		self.assertEqual(primary_contact(doc), "First")

	def test_primary_contact_falls_back_to_the_first_row(self):
		doc = FakeDoc(contacts=[FakeContactRow("Only"), FakeContactRow("Other")])
		self.assertEqual(primary_contact(doc), "Only")

	def test_primary_contact_handles_no_contacts(self):
		self.assertIsNone(primary_contact(FakeDoc(contacts=[])))
		self.assertIsNone(primary_contact(FakeDoc(contacts=None)))

	def test_name_sync_skipped_when_both_names_present(self):
		"""A name typed on the deal must win over the contact's."""
		doc = FakeDoc(first_name="Set", last_name="Already", contacts=[FakeContactRow("X")])
		sync_contact_name(doc)
		self.assertEqual(doc.first_name, "Set")
		self.assertEqual(doc.last_name, "Already")


class TestContactOrganizationSync(FrappeTestCase):
	"""Requires the site's custom Contact field, so these touch the DB."""

	def setUp(self):
		self.has_field = frappe.get_meta("Contact").has_field("custom_organization_link")

	def test_company_name_follows_the_organization_link(self):
		if not self.has_field:
			self.skipTest("custom_organization_link is not installed on this site")

		doc = FakeDoc(custom_organization_link="Vilniaus Turtas", company_name=None)
		sync_organization(doc)
		self.assertEqual(doc.company_name, "Vilniaus Turtas")

	def test_clearing_the_link_clears_company_name(self):
		if not self.has_field:
			self.skipTest("custom_organization_link is not installed on this site")

		doc = FakeDoc(custom_organization_link=None, company_name="Stale Org")
		sync_organization(doc)
		self.assertIsNone(doc.company_name)

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
from crm.txb.doc_events.lead import (
	default_disqualified_reason,
	require_discovery_details,
	require_reach_for_contacted,
)


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


class FakeLead(FakeDoc):
	"""Lead stand-in for the reach guard: models is_new / has_value_changed."""

	def __init__(self, *, name="CRM-LEAD-0001", status="New", is_new=False, status_changed=True):
		super().__init__(name=name, status=status)
		self._is_new = is_new
		self._status_changed = status_changed

	def is_new(self):
		return self._is_new

	def has_value_changed(self, fieldname):
		return self._status_changed if fieldname == "status" else False


class TestRequireReachForContacted(FrappeTestCase):
	"""TXB-128: the server is the single enforcement point for entering Contacted.

	The two Lead.vue handlers only prompt for the reach; the guard is what stops the kanban
	drag, the mobile control, a bulk edit or a raw API write from reaching Contacted with no
	reach recorded. These exercise the guard directly, so every bypassing route is covered
	by the one rule rather than by each caller.
	"""

	def tearDown(self):
		frappe.flags.txb_action = None

	def test_bare_move_to_contacted_is_rejected(self):
		"""A kanban/mobile/bulk write (no reach flag armed) cannot reach Contacted."""
		frappe.flags.txb_action = None
		doc = FakeLead(status="Contacted", status_changed=True)
		with self.assertRaises(frappe.ValidationError):
			require_reach_for_contacted(doc)

	def test_log_reach_save_is_allowed(self):
		"""The reach endpoint arms the flag with the lead's own name, so its save passes."""
		doc = FakeLead(name="CRM-LEAD-0007", status="Contacted", status_changed=True)
		frappe.flags.txb_action = "CRM-LEAD-0007"
		require_reach_for_contacted(doc)  # must not raise

	def test_flag_for_another_lead_does_not_exempt(self):
		"""The exemption is scoped to the document, so it cannot leak across a request."""
		doc = FakeLead(name="CRM-LEAD-0007", status="Contacted", status_changed=True)
		frappe.flags.txb_action = "CRM-LEAD-0009"
		with self.assertRaises(frappe.ValidationError):
			require_reach_for_contacted(doc)

	def test_unchanged_status_is_ignored(self):
		"""Re-saving a lead already in Contacted does not re-demand a reach."""
		doc = FakeLead(status="Contacted", status_changed=False)
		require_reach_for_contacted(doc)  # must not raise

	def test_insert_in_contacted_is_exempt(self):
		"""A lead created directly in Contacted (import/seed) is not a transition."""
		doc = FakeLead(status="Contacted", is_new=True, status_changed=True)
		require_reach_for_contacted(doc)  # must not raise

	def test_moving_to_another_status_is_unaffected(self):
		doc = FakeLead(status="Nurture", status_changed=True)
		require_reach_for_contacted(doc)  # must not raise


class TestRequireDiscoveryDetails(FrappeTestCase):
	"""TXB-129: the server is the single enforcement point for entering Discovery meeting set.

	The two Lead.vue handlers only prompt for the schedule; the guard is what stops the kanban
	drag, the mobile control, a bulk edit or a raw API write from reaching Discovery meeting set
	with no scheduling details recorded. These exercise the guard directly, so every bypassing
	route is covered by the one rule rather than by each caller.
	"""

	def tearDown(self):
		frappe.flags.txb_action = None

	def test_bare_move_to_discovery_is_rejected(self):
		"""A kanban/mobile/bulk write (no schedule flag armed) cannot reach the status."""
		frappe.flags.txb_action = None
		doc = FakeLead(status="Discovery meeting set", status_changed=True)
		with self.assertRaises(frappe.ValidationError):
			require_discovery_details(doc)

	def test_schedule_discovery_save_is_allowed(self):
		"""The schedule endpoint arms the flag with the lead's own name, so its save passes."""
		doc = FakeLead(name="CRM-LEAD-0007", status="Discovery meeting set", status_changed=True)
		frappe.flags.txb_action = "CRM-LEAD-0007"
		require_discovery_details(doc)  # must not raise

	def test_flag_for_another_lead_does_not_exempt(self):
		"""The exemption is scoped to the document, so it cannot leak across a request."""
		doc = FakeLead(name="CRM-LEAD-0007", status="Discovery meeting set", status_changed=True)
		frappe.flags.txb_action = "CRM-LEAD-0009"
		with self.assertRaises(frappe.ValidationError):
			require_discovery_details(doc)

	def test_unchanged_status_is_ignored(self):
		"""Re-saving a lead already in the status does not re-demand a schedule."""
		doc = FakeLead(status="Discovery meeting set", status_changed=False)
		require_discovery_details(doc)  # must not raise

	def test_insert_in_discovery_is_exempt(self):
		"""A lead created directly in the status (import/seed) is not a transition."""
		doc = FakeLead(status="Discovery meeting set", is_new=True, status_changed=True)
		require_discovery_details(doc)  # must not raise

	def test_moving_to_another_status_is_unaffected(self):
		doc = FakeLead(status="Nurture", status_changed=True)
		require_discovery_details(doc)  # must not raise


class TestValidateDiscovery(FrappeTestCase):
	"""TXB-129: the server re-validates the schedule so a direct API call meets the dialog's rule."""

	def _valid_virtual(self):
		return {
			"meeting_date": "2026-09-01",
			"meeting_time": "10:30:00",
			"meeting_type": "Virtual",
			"meeting_link": "https://meet.example.com/abc",
		}

	def _valid_onsite(self):
		return {
			"meeting_date": "2026-09-01",
			"meeting_time": "10:30:00",
			"meeting_type": "Onsite",
			"meeting_address": "1 Gedimino Ave, Vilnius",
		}

	def test_complete_virtual_passes(self):
		from crm.txb.api.actions import validate_discovery

		validate_discovery(self._valid_virtual())  # must not raise

	def test_complete_onsite_passes(self):
		from crm.txb.api.actions import validate_discovery

		validate_discovery(self._valid_onsite())  # must not raise

	def test_virtual_without_link_is_rejected(self):
		from crm.txb.api.actions import validate_discovery

		values = self._valid_virtual()
		values["meeting_link"] = "   "
		with self.assertRaises(frappe.MandatoryError):
			validate_discovery(values)

	def test_onsite_without_address_is_rejected(self):
		from crm.txb.api.actions import validate_discovery

		values = self._valid_onsite()
		del values["meeting_address"]
		with self.assertRaises(frappe.MandatoryError):
			validate_discovery(values)

	def test_missing_date_time_or_type_is_rejected(self):
		from crm.txb.api.actions import validate_discovery

		for field in ("meeting_date", "meeting_time", "meeting_type"):
			values = self._valid_virtual()
			values[field] = ""
			with self.assertRaises(frappe.MandatoryError):
				validate_discovery(values)

	def test_onsite_link_is_not_demanded_and_virtual_address_is_not(self):
		"""Only the location detail for the chosen type is required; the other is irrelevant."""
		from crm.txb.api.actions import validate_discovery

		# Onsite carrying a stray link but no address still fails on the address, not the link.
		values = self._valid_onsite()
		del values["meeting_address"]
		values["meeting_link"] = "https://ignored.example.com"
		with self.assertRaises(frappe.MandatoryError):
			validate_discovery(values)


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

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

from crm.txb.constants import PIPELINE_DELIVERING_COACHING
from crm.txb.doc_events.call_log import default_phone_numbers
from crm.txb.doc_events.contact import sync_organization
from crm.txb.doc_events.lead import (
	default_disqualified_reason,
	require_discovery_details,
	require_follow_up_context,
	require_nurture_context,
	require_reach_for_contacted,
)
from crm.txb.pipelines.common import DEAL_DOCTYPE, NOTE_DOCTYPE
from crm.txb.pipelines.delivering_coaching import (
	missing_activation_readiness,
	require_activation_readiness,
)


class FakeDoc:
	"""Minimal document stand-in supporting get/set and attribute access."""

	def __init__(self, **fields):
		self.__dict__.update(fields)

	def get(self, fieldname, default=None):
		return self.__dict__.get(fieldname, default)

	def set(self, fieldname, value):
		self.__dict__[fieldname] = value


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


class TestRequireFollowUpContext(FrappeTestCase):
	"""TXB-210: the server is the single enforcement point for entering Follow-up.

	The browser dialog only prompts for the follow-up; the guard is what stops a Kanban drag, the
	mobile control, a bulk edit or a raw API write from reaching Follow-up with nothing recorded.
	These exercise the guard directly, so every bypassing route is covered by the one rule.
	"""

	def tearDown(self):
		frappe.flags.txb_action = None

	def test_bare_move_to_follow_up_is_rejected(self):
		frappe.flags.txb_action = None
		doc = FakeLead(status="Follow-up", status_changed=True)
		with self.assertRaises(frappe.ValidationError):
			require_follow_up_context(doc)

	def test_schedule_follow_up_save_is_allowed(self):
		"""The action arms the flag with the lead's own name, so its save passes."""
		doc = FakeLead(name="CRM-LEAD-0007", status="Follow-up", status_changed=True)
		frappe.flags.txb_action = "CRM-LEAD-0007"
		require_follow_up_context(doc)  # must not raise

	def test_flag_for_another_lead_does_not_exempt(self):
		"""The exemption is scoped to the document, so it cannot leak across a request."""
		doc = FakeLead(name="CRM-LEAD-0007", status="Follow-up", status_changed=True)
		frappe.flags.txb_action = "CRM-LEAD-0009"
		with self.assertRaises(frappe.ValidationError):
			require_follow_up_context(doc)

	def test_unchanged_status_is_ignored(self):
		doc = FakeLead(status="Follow-up", status_changed=False)
		require_follow_up_context(doc)  # must not raise

	def test_insert_in_follow_up_is_exempt(self):
		doc = FakeLead(status="Follow-up", is_new=True, status_changed=True)
		require_follow_up_context(doc)  # must not raise

	def test_moving_to_another_status_is_unaffected(self):
		doc = FakeLead(status="Nurture", status_changed=True)
		require_follow_up_context(doc)  # must not raise


class TestRequireNurtureContext(FrappeTestCase):
	"""TXB-210: the server is the single enforcement point for entering Nurture.

	The browser dialog only prompts for the nurture plan; the guard is what stops a Kanban drag,
	the mobile control, a bulk edit or a raw API write from reaching Nurture with nothing recorded.
	The dedicated action and the discovery-meeting Nurture outcome both arm the flag, so both pass.
	"""

	def tearDown(self):
		frappe.flags.txb_action = None

	def test_bare_move_to_nurture_is_rejected(self):
		frappe.flags.txb_action = None
		doc = FakeLead(status="Nurture", status_changed=True)
		with self.assertRaises(frappe.ValidationError):
			require_nurture_context(doc)

	def test_action_save_is_allowed(self):
		"""set_nurture / run_discovery_meeting arm the flag with the lead's own name, so it passes."""
		doc = FakeLead(name="CRM-LEAD-0007", status="Nurture", status_changed=True)
		frappe.flags.txb_action = "CRM-LEAD-0007"
		require_nurture_context(doc)  # must not raise

	def test_flag_for_another_lead_does_not_exempt(self):
		doc = FakeLead(name="CRM-LEAD-0007", status="Nurture", status_changed=True)
		frappe.flags.txb_action = "CRM-LEAD-0009"
		with self.assertRaises(frappe.ValidationError):
			require_nurture_context(doc)

	def test_unchanged_status_is_ignored(self):
		doc = FakeLead(status="Nurture", status_changed=False)
		require_nurture_context(doc)  # must not raise

	def test_insert_in_nurture_is_exempt(self):
		doc = FakeLead(status="Nurture", is_new=True, status_changed=True)
		require_nurture_context(doc)  # must not raise

	def test_moving_to_another_status_is_unaffected(self):
		doc = FakeLead(status="Follow-up", status_changed=True)
		require_nurture_context(doc)  # must not raise


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
	"""TXB-129/TXB-245: the server re-validates the schedule so a direct API call meets the dialog's
	rule. Only date, time and type are required; the type-specific location detail is optional."""

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

	def test_virtual_without_link_is_accepted(self):
		"""TXB-245: the link is optional — a Virtual schedule with a blank link still passes."""
		from crm.txb.api.actions import validate_discovery

		values = self._valid_virtual()
		values["meeting_link"] = "   "
		validate_discovery(values)  # must not raise

	def test_onsite_without_address_is_accepted(self):
		"""TXB-245: the address is optional — an Onsite schedule with no address still passes."""
		from crm.txb.api.actions import validate_discovery

		values = self._valid_onsite()
		del values["meeting_address"]
		validate_discovery(values)  # must not raise

	def test_missing_date_time_or_type_is_rejected(self):
		from crm.txb.api.actions import validate_discovery

		for field in ("meeting_date", "meeting_time", "meeting_type"):
			values = self._valid_virtual()
			values[field] = ""
			with self.assertRaises(frappe.MandatoryError):
				validate_discovery(values)


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


READY_FIELDS = {
	"custom_delivery_coach": "Administrator",
	"custom_contract_signed": "Yes",
	"custom_payment_confirmed": "Yes",
	"custom_test_completed": "Yes",
	"custom_delivery_notes": "Kick-off agreed.",
}

ALL_READINESS_LABELS = [
	"Delivery Coach",
	"Delivery Coach Name",
	"Contract Signed?",
	"Payment Confirmed?",
	"Required Test/Check Completed?",
	"Delivery Notes",
]


class TestActivationReadinessRules(FrappeTestCase):
	"""TXB-251: the pure readiness rule, independent of the database."""

	def test_every_unmet_condition_is_listed_in_display_order(self):
		doc = FakeDoc(custom_contract_signed="No", custom_delivery_notes="   ")
		self.assertEqual(missing_activation_readiness(doc), ALL_READINESS_LABELS)

	def test_a_fully_ready_deal_has_nothing_missing(self):
		doc = FakeDoc(custom_delivery_coach_name="Coach", **READY_FIELDS)
		self.assertEqual(missing_activation_readiness(doc), [])

	def test_one_error_names_every_unmet_field(self):
		doc = FakeDoc(custom_delivery_coach_name="Coach", **{**READY_FIELDS, "custom_payment_confirmed": "No"})
		doc.custom_delivery_notes = ""
		with self.assertRaises(frappe.ValidationError) as ctx:
			require_activation_readiness(doc)
		message = str(ctx.exception)
		self.assertIn("Payment Confirmed?", message)
		self.assertIn("Delivery Notes", message)
		self.assertNotIn("Contract Signed?", message)


class TestActivationReadinessGate(FrappeTestCase):
	"""TXB-251: every door into Active meets the same gate; already-Active deals save freely."""

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.flags.txb_action = None
		frappe.db.rollback()

	def make_deal(self, status, **fields):
		return frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"pipeline_type": PIPELINE_DELIVERING_COACHING,
				"status": status,
				**fields,
			}
		).insert(ignore_permissions=True)

	def note_count(self, deal_name):
		return frappe.db.count(
			NOTE_DOCTYPE, {"reference_doctype": DEAL_DOCTYPE, "reference_docname": deal_name}
		)

	def test_direct_save_into_active_is_refused_with_all_labels(self):
		deal = self.make_deal("Contract Cleared")
		deal.status = "Active"
		with self.assertRaises(frappe.ValidationError) as ctx:
			deal.save()
		for label in ALL_READINESS_LABELS:
			self.assertIn(label, str(ctx.exception))
		self.assertEqual(frappe.db.get_value("CRM Deal", deal.name, "status"), "Contract Cleared")

	def test_set_value_into_active_is_refused(self):
		"""Kanban drag and the status control write through set_value."""
		from frappe.client import set_value

		deal = self.make_deal("On Hold")
		with self.assertRaises(frappe.ValidationError):
			set_value("CRM Deal", deal.name, "status", "Active")
		self.assertEqual(frappe.db.get_value("CRM Deal", deal.name, "status"), "On Hold")

	def test_ready_deal_enters_active(self):
		deal = self.make_deal("Contract Cleared", **READY_FIELDS)
		deal.status = "Active"
		deal.save()
		self.assertEqual(frappe.db.get_value("CRM Deal", deal.name, "status"), "Active")

	def test_already_active_deal_saves_unrelated_edits(self):
		deal = self.make_deal("Active")
		deal.custom_delivery_notes = "Historical record, no readiness data."
		deal.save()
		self.assertEqual(frappe.db.get_value("CRM Deal", deal.name, "status"), "Active")

	def test_reactivate_action_is_refused_before_any_note_is_written(self):
		from crm.txb.api.actions import execute_action

		deal = self.make_deal("Inactive")
		before = self.note_count(deal.name)
		with self.assertRaises(frappe.ValidationError) as ctx:
			execute_action(deal.name, "reactivate", {"reactivation_notes": "Back on track"})
		self.assertIn("Delivery Notes", str(ctx.exception))
		self.assertEqual(self.note_count(deal.name), before)
		self.assertEqual(frappe.db.get_value("CRM Deal", deal.name, "status"), "Inactive")

	def test_reactivate_action_succeeds_when_ready(self):
		from crm.txb.api.actions import execute_action

		deal = self.make_deal("Payment Hold", **READY_FIELDS)
		result = execute_action(deal.name, "reactivate", {"reactivation_notes": "Paid"})
		self.assertEqual(result["status"], "Active")


READINESS_PATCH = {**READY_FIELDS}


class TestActivationReadinessRemediation(FrappeTestCase):
	"""TXB-258: the structured preflight and the atomic remediate-and-enter-Active contract."""

	# Shared fixtures, borrowed rather than inherited so the TXB-251 tests do not run twice.
	tearDown = TestActivationReadinessGate.tearDown
	make_deal = TestActivationReadinessGate.make_deal
	note_count = TestActivationReadinessGate.note_count

	def test_preflight_lists_editable_source_fields_not_the_derived_name(self):
		from crm.txb.api.actions import get_activation_readiness

		deal = self.make_deal("Contract Cleared")
		result = get_activation_readiness(deal.name, action="set_first_call_date")
		self.assertFalse(result["ready"])
		self.assertEqual(result["missing"], ALL_READINESS_LABELS)
		self.assertEqual(
			[field["fieldname"] for field in result["fields"]],
			[
				"custom_delivery_coach",
				"custom_contract_signed",
				"custom_payment_confirmed",
				"custom_test_completed",
				"custom_delivery_notes",
			],
		)
		for field in result["fields"]:
			self.assertIn("fieldtype", field)
			self.assertIn("options", field)
			self.assertIn("value", field)
		self.assertEqual(result["fields"][1]["required_value"], "Yes")

	def test_preflight_returns_only_what_is_still_missing(self):
		from crm.txb.api.actions import get_activation_readiness

		deal = self.make_deal("Inactive", **{**READY_FIELDS, "custom_payment_confirmed": "No"})
		result = get_activation_readiness(deal.name, action="reactivate")
		self.assertEqual(result["missing"], ["Payment Confirmed?"])
		self.assertEqual([f["fieldname"] for f in result["fields"]], ["custom_payment_confirmed"])
		self.assertEqual(result["fields"][0]["value"], "No")

	def test_initial_activation_applies_patch_and_action_together(self):
		from crm.txb.api.actions import complete_activation

		deal = self.make_deal("Contract Cleared")
		result = complete_activation(
			deal.name,
			readiness=READINESS_PATCH,
			action="set_first_call_date",
			data={"first_call_date": "2026-10-01 09:00:00", "call_notes": "Kick-off"},
			expected_status="Contract Cleared",
		)
		self.assertEqual(result["status"], "Active")
		saved = frappe.get_doc("CRM Deal", deal.name)
		self.assertEqual(saved.custom_contract_signed, "Yes")
		self.assertTrue(saved.custom_delivery_coach_name)

	def test_reactivation_applies_patch_and_action_together(self):
		from crm.txb.api.actions import complete_activation

		deal = self.make_deal("On Hold")
		result = complete_activation(
			deal.name, readiness=READINESS_PATCH, action="reactivate", data={"reactivation_notes": "Back"}
		)
		self.assertEqual(result["status"], "Active")

	def test_admin_direct_status_transition_applies_patch(self):
		from crm.txb.api.actions import complete_activation

		deal = self.make_deal("Payment Hold")
		result = complete_activation(deal.name, readiness=READINESS_PATCH, status="Active")
		self.assertEqual(result["status"], "Active")

	def test_incomplete_patch_leaves_everything_unchanged(self):
		from crm.txb.api.actions import complete_activation

		deal = self.make_deal("Inactive")
		before = self.note_count(deal.name)
		with self.assertRaises(frappe.ValidationError):
			complete_activation(
				deal.name,
				readiness={**READINESS_PATCH, "custom_delivery_notes": ""},
				action="reactivate",
				data={"reactivation_notes": "Back"},
			)
		self.assertEqual(self.note_count(deal.name), before)
		saved = frappe.db.get_value(
			"CRM Deal", deal.name, ["status", "custom_contract_signed"], as_dict=True
		)
		self.assertEqual(saved.status, "Inactive")
		self.assertNotEqual(saved.custom_contract_signed, "Yes")

	def test_derived_coach_name_cannot_be_submitted(self):
		from crm.txb.api.actions import complete_activation

		deal = self.make_deal("Contract Cleared")
		with self.assertRaises(frappe.ValidationError):
			complete_activation(
				deal.name,
				readiness={**READINESS_PATCH, "custom_delivery_coach_name": "Someone Else"},
				action="set_first_call_date",
				data={"first_call_date": "2026-10-01 09:00:00"},
			)
		self.assertEqual(frappe.db.get_value("CRM Deal", deal.name, "status"), "Contract Cleared")

	def test_stale_state_is_refused_before_any_write(self):
		from crm.txb.api.actions import complete_activation

		deal = self.make_deal("Inactive")
		with self.assertRaises(frappe.ValidationError):
			complete_activation(
				deal.name, readiness=READINESS_PATCH, action="reactivate", expected_status="On Hold"
			)
		self.assertNotEqual(frappe.db.get_value("CRM Deal", deal.name, "custom_contract_signed"), "Yes")

	def test_action_not_available_from_current_status_is_refused(self):
		from crm.txb.api.actions import complete_activation

		deal = self.make_deal("Contract Cleared")
		with self.assertRaises(frappe.ValidationError):
			complete_activation(deal.name, readiness=READINESS_PATCH, action="reactivate")
		self.assertEqual(frappe.db.get_value("CRM Deal", deal.name, "status"), "Contract Cleared")

	def test_non_active_bound_request_is_refused(self):
		from crm.txb.api.actions import complete_activation

		deal = self.make_deal("Active", **READY_FIELDS)
		with self.assertRaises(frappe.ValidationError):
			complete_activation(deal.name, readiness={}, status="Inactive")
		with self.assertRaises(frappe.ValidationError):
			complete_activation(deal.name, readiness={}, action="mark_inactive", data={"inactive_reason": "Other"})
		self.assertEqual(frappe.db.get_value("CRM Deal", deal.name, "status"), "Active")

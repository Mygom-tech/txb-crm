# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-209: one canonical, linked CRM Event per TxB meeting.

Two layers, matching the rest of the suite (see test_doc_events / test_lead_actions):

- Pure tests exercise the helper's identity, description and participant derivation with no
  database, so the logic is verifiable even where the runner cannot install the custom field.
- Database-backed tests drive the real Lead Discovery endpoint and the Opportunity meeting
  handlers through Frappe, proving linkage, the complete meeting-flow mapping, retry idempotency,
  reschedule/cancel identity preservation, activity visibility and transaction rollback. They skip
  cleanly on a site that has not yet run `add_meeting_event_key_field`, following the same
  has_field guard the permissions suite uses for app-managed fields.
"""

import inspect

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.txb import meetings
from crm.txb.api.actions import (
	DISCOVERY_MEETING_FLOW,
	discovery_starts_on,
	schedule_discovery,
)
from crm.txb.constants import FIELD_MEETING_KEY
from crm.txb.meetings import (
	cancel_meeting_event,
	deal_participants,
	meeting_key,
	sync_meeting_event,
)
from crm.txb.pipelines.individual_session import (
	MEETING_FLOW_BAP,
	bap_address,
	book_bap,
	cancel_bap,
	reschedule_bap,
)
from crm.txb.pipelines.selling_training import (
	MEETING_FLOW_DISCOVERY,
	MEETING_FLOW_PROPOSAL,
	MEETING_FLOW_TRAINING,
	contract_signed,
	set_discovery_meeting,
	set_proposal_meeting,
	set_training_date,
)
from crm.txb.pipelines.workshop import (
	MEETING_FLOW_VCS,
	MEETING_FLOW_WORKSHOP,
	cancel_workshop,
	reschedule_workshop,
	run_vcs_call,
	set_vcs_call,
	set_workshop,
)

LEAD_DOCTYPE = "CRM Lead"
DEAL_DOCTYPE = "CRM Deal"


class FakeRow:
	def __init__(self, contact, is_primary=0):
		self.contact = contact
		self.is_primary = is_primary

	def get(self, key, default=None):
		return getattr(self, key, default)


class FakeDeal:
	def __init__(self, contacts=None):
		self.contacts = contacts or []

	def get(self, key, default=None):
		return getattr(self, key, default)


class TestMeetingIdentity(FrappeTestCase):
	"""The pure identity, description and participant logic -- no database required."""

	def test_key_is_source_plus_flow(self):
		self.assertEqual(
			meeting_key("CRM Deal", "CRM-DEAL-0001", "workshop"),
			"CRM Deal:CRM-DEAL-0001:workshop",
		)

	def test_distinct_flows_on_one_source_get_distinct_keys(self):
		"""A discovery and a proposal meeting on the same deal are separate Events."""
		discovery = meeting_key("CRM Deal", "D1", MEETING_FLOW_DISCOVERY)
		proposal = meeting_key("CRM Deal", "D1", MEETING_FLOW_PROPOSAL)
		self.assertNotEqual(discovery, proposal)

	def test_description_shows_only_supplied_details_and_escapes(self):
		body = meetings._meeting_description("Virtual", "http://x/<b>", None)
		self.assertIn("Virtual", body)
		self.assertIn("http://x/&lt;b&gt;", body)
		self.assertNotIn("Address", body)

	def test_description_is_empty_when_nothing_extra(self):
		self.assertEqual(meetings._meeting_description(None, None, None), "")

	def test_participants_are_the_deals_contacts(self):
		deal = FakeDeal(contacts=[FakeRow("Contact-A"), FakeRow("Contact-B")])
		self.assertEqual(
			deal_participants(deal),
			[
				{"reference_doctype": "Contact", "reference_docname": "Contact-A"},
				{"reference_doctype": "Contact", "reference_docname": "Contact-B"},
			],
		)

	def test_participants_skip_blank_contact_rows(self):
		deal = FakeDeal(contacts=[FakeRow(""), FakeRow("Contact-A")])
		self.assertEqual(
			deal_participants(deal),
			[{"reference_doctype": "Contact", "reference_docname": "Contact-A"}],
		)

	def test_bap_address_joins_present_parts(self):
		self.assertEqual(bap_address("1 St", "", "LT"), "1 St, LT")
		self.assertEqual(bap_address(None, None, None), "")

	def test_discovery_starts_on_combines_date_and_time(self):
		starts = discovery_starts_on({"meeting_date": "2026-09-10", "meeting_time": "14:30:00"})
		self.assertEqual(starts, "2026-09-10 14:30:00")

	def test_no_google_calendar_dependency_in_helper(self):
		"""The Non-goal: no calendar provider is introduced. The helper never mentions one."""
		source = inspect.getsource(meetings)
		self.assertNotIn("google", source.lower())
		self.assertNotIn("calendar", source.lower())


class TestMeetingLifecycle(FrappeTestCase):
	"""Real Events, driven end to end through the endpoint and the pipeline handlers."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.installed = frappe.get_meta("Event").has_field(FIELD_MEETING_KEY)

	def setUp(self):
		if not self.installed:
			self.skipTest(f"{FIELD_MEETING_KEY} is not installed on this site")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	# -- fixtures -----------------------------------------------------------------------

	def make_lead(self):
		return frappe.get_doc(
			{"doctype": LEAD_DOCTYPE, "first_name": "TXB209", "status": "New"}
		).insert(ignore_permissions=True)

	def make_deal(self, pipeline, status, contacts=None):
		return frappe.get_doc(
			{
				"doctype": DEAL_DOCTYPE,
				"pipeline_type": pipeline,
				"status": status,
				"contacts": contacts or [],
			}
		).insert(ignore_permissions=True)

	def events_for(self, doctype, name, flow=None):
		filters = {"reference_doctype": doctype, "reference_docname": name}
		if flow:
			filters[FIELD_MEETING_KEY] = meeting_key(doctype, name, flow)
		return frappe.get_all(
			"Event", filters=filters, fields=["name", "starts_on", "status", "subject"]
		)

	# -- ac-1: creation, linkage, visibility --------------------------------------------

	def test_lead_discovery_creates_one_linked_event(self):
		lead = self.make_lead()

		schedule_discovery(
			lead.name,
			activity={
				"meeting_date": "2026-09-10",
				"meeting_time": "14:30:00",
				"meeting_type": "Virtual",
				"meeting_link": "https://meet.example/abc",
			},
		)

		events = self.events_for(LEAD_DOCTYPE, lead.name, DISCOVERY_MEETING_FLOW)
		self.assertEqual(len(events), 1)
		self.assertEqual(str(events[0]["starts_on"]), "2026-09-10 14:30:00")
		self.assertEqual(events[0]["status"], "Open")

		# The Event surfaces on the Lead's normalized activity stream (the Events/Activity surface).
		from crm.api.activities import _meeting_events

		activities = _meeting_events(LEAD_DOCTYPE, lead.name, is_lead=True)
		scheduled = [a for a in activities if a["data"].get("meeting_action") == "scheduled"]
		self.assertEqual(len(scheduled), 1)
		self.assertEqual(scheduled[0]["target"]["name"], events[0]["name"])

	def test_meeting_carries_available_participants(self):
		contact = frappe.get_doc(
			{"doctype": "Contact", "first_name": "TXB209 Participant"}
		).insert(ignore_permissions=True)
		deal = self.make_deal(
			"Individual Session", "Submitted", contacts=[{"contact": contact.name}]
		)

		book_bap(deal, {"bap_datetime": "2026-09-11 09:00:00", "bap_location_type": "Virtual"})

		event_name = self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_BAP)[0]["name"]
		event = frappe.get_doc("Event", event_name)
		refs = [(p.reference_doctype, p.reference_docname) for p in event.event_participants]
		self.assertIn(("Contact", contact.name), refs)

	# -- ac-1: complete meeting-flow mapping --------------------------------------------

	def test_selling_training_flows_map_to_distinct_events(self):
		deal = self.make_deal("Selling Training", "Training submitted")

		set_discovery_meeting(deal, {"discovery_datetime": "2026-09-12 10:00:00"})
		set_proposal_meeting(deal, {"proposal_meeting_datetime": "2026-09-13 10:00:00"})
		set_training_date(deal, {"training_datetime": "2026-09-14 10:00:00"})

		for flow in (MEETING_FLOW_DISCOVERY, MEETING_FLOW_PROPOSAL, MEETING_FLOW_TRAINING):
			self.assertEqual(len(self.events_for(DEAL_DOCTYPE, deal.name, flow)), 1, flow)
		self.assertEqual(len(self.events_for(DEAL_DOCTYPE, deal.name)), 3)

	def test_contract_and_set_training_date_share_one_training_event(self):
		"""The training session is one flow however its datetime arrives -- never a duplicate."""
		deal = self.make_deal("Selling Training", "Training negotiations")

		contract_signed(deal, {"contract_signed": "Yes", "training_datetime": "2026-09-20 10:00:00"})
		set_training_date(deal, {"training_datetime": "2026-09-21 11:00:00"})

		events = self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_TRAINING)
		self.assertEqual(len(events), 1)
		self.assertEqual(str(events[0]["starts_on"]), "2026-09-21 11:00:00")

	def test_workshop_vcs_and_workshop_are_distinct_events(self):
		deal = self.make_deal("Workshop", "Workshop submitted")

		set_vcs_call(deal, {"vcs_datetime": "2026-09-15 09:00:00"})
		set_workshop(deal, {"ws_datetime": "2026-09-16 09:00:00"})

		self.assertEqual(len(self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_VCS)), 1)
		self.assertEqual(len(self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_WORKSHOP)), 1)

	def test_run_vcs_call_confirmed_date_opens_the_workshop_event(self):
		deal = self.make_deal("Workshop", "VCS call set")

		run_vcs_call(
			deal,
			{"ws_confirmed": "Yes", "confirmed_ws_date": "2026-09-17 09:00:00", "vcs_notes": "ok"},
		)

		events = self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_WORKSHOP)
		self.assertEqual(len(events), 1)
		self.assertEqual(str(events[0]["starts_on"]), "2026-09-17 09:00:00")

	def test_individual_session_books_one_bap_event(self):
		deal = self.make_deal("Individual Session", "Submitted")

		book_bap(
			deal,
			{
				"bap_datetime": "2026-09-18 09:00:00",
				"bap_location_type": "On-Site",
				"bap_street": "1 Main",
				"bap_city": "Vilnius",
			},
		)

		events = self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_BAP)
		self.assertEqual(len(events), 1)
		event = frappe.get_doc("Event", events[0]["name"])
		self.assertIn("Vilnius", event.location)

	# -- ac-2: idempotency, reschedule and cancel identity ------------------------------

	def test_repeated_submit_is_idempotent(self):
		lead = self.make_lead()
		payload = {
			"meeting_date": "2026-09-10",
			"meeting_time": "14:30:00",
			"meeting_type": "Onsite",
			"meeting_address": "HQ",
		}

		schedule_discovery(lead.name, activity=dict(payload))
		schedule_discovery(lead.name, activity=dict(payload))

		self.assertEqual(len(self.events_for(LEAD_DOCTYPE, lead.name, DISCOVERY_MEETING_FLOW)), 1)

	def test_reschedule_moves_the_same_event(self):
		lead = self.make_lead()
		schedule_discovery(
			lead.name,
			activity={
				"meeting_date": "2026-09-10",
				"meeting_time": "14:30:00",
				"meeting_type": "Onsite",
				"meeting_address": "HQ",
			},
		)
		first = self.events_for(LEAD_DOCTYPE, lead.name, DISCOVERY_MEETING_FLOW)[0]["name"]

		schedule_discovery(
			lead.name,
			activity={
				"meeting_date": "2026-09-11",
				"meeting_time": "16:00:00",
				"meeting_type": "Onsite",
				"meeting_address": "HQ",
			},
		)
		events = self.events_for(LEAD_DOCTYPE, lead.name, DISCOVERY_MEETING_FLOW)

		self.assertEqual(len(events), 1)
		self.assertEqual(events[0]["name"], first)
		self.assertEqual(str(events[0]["starts_on"]), "2026-09-11 16:00:00")

	def test_bap_reschedule_then_cancel_preserve_one_event(self):
		deal = self.make_deal("Individual Session", "Session Set")
		book_bap(deal, {"bap_datetime": "2026-09-18 09:00:00", "bap_location_type": "Virtual"})
		original = self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_BAP)[0]["name"]

		reschedule_bap(
			deal,
			{
				"reschedule_type": "Yes, I have a new date",
				"reschedule_reason": "clash",
				"new_bap_datetime": "2026-09-19 10:00:00",
				"new_location_type": "On-Site",
			},
		)
		cancel_bap(deal, {"cancel_reason": "No-show"})

		events = self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_BAP)
		self.assertEqual(len(events), 1)
		self.assertEqual(events[0]["name"], original)
		self.assertEqual(events[0]["status"], "Cancelled")

	def test_workshop_reschedule_and_cancel_preserve_one_event(self):
		deal = self.make_deal("Workshop", "Workshop set")
		set_workshop(deal, {"ws_datetime": "2026-09-16 09:00:00"})
		original = self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_WORKSHOP)[0]["name"]

		reschedule_workshop(
			deal,
			{
				"reschedule_type": "Yes, I have a new date",
				"reschedule_reason": "venue",
				"new_datetime": "2026-09-17 09:00:00",
			},
		)
		cancel_workshop(deal, {"cancellation_notes": "client dropped"})

		events = self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_WORKSHOP)
		self.assertEqual(len(events), 1)
		self.assertEqual(events[0]["name"], original)
		self.assertEqual(events[0]["status"], "Cancelled")

	def test_cancel_with_no_meeting_is_a_no_op(self):
		"""Cancelling a deal that never scheduled its flow creates nothing."""
		deal = self.make_deal("Workshop", "Workshop submitted")

		cancel_workshop(deal, {"cancellation_notes": "n/a"})

		self.assertEqual(self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_WORKSHOP), [])

	# -- ac-3: transaction consistency, no provider dependency --------------------------

	def test_event_participates_in_the_request_transaction(self):
		"""Rolling back the transaction removes the Event -- it is not committed on its own."""
		deal = self.make_deal("Workshop", "Workshop submitted")

		frappe.db.savepoint("txb209_probe")
		sync_meeting_event(
			reference_doctype=DEAL_DOCTYPE,
			reference_docname=deal.name,
			flow=MEETING_FLOW_WORKSHOP,
			subject="Workshop",
			starts_on="2026-09-16 09:00:00",
		)
		self.assertEqual(len(self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_WORKSHOP)), 1)

		frappe.db.rollback(save_point="txb209_probe")
		self.assertEqual(self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_WORKSHOP), [])

	def test_blank_datetime_schedules_nothing(self):
		deal = self.make_deal("Selling Training", "Training submitted")

		self.assertIsNone(
			sync_meeting_event(
				reference_doctype=DEAL_DOCTYPE,
				reference_docname=deal.name,
				flow=MEETING_FLOW_DISCOVERY,
				subject="Training Discovery Meeting",
				starts_on=None,
			)
		)
		self.assertEqual(self.events_for(DEAL_DOCTYPE, deal.name), [])

	def test_cancel_is_idempotent(self):
		deal = self.make_deal("Individual Session", "Session Set")
		book_bap(deal, {"bap_datetime": "2026-09-18 09:00:00", "bap_location_type": "Virtual"})

		cancel_meeting_event(DEAL_DOCTYPE, deal.name, MEETING_FLOW_BAP)
		cancel_meeting_event(DEAL_DOCTYPE, deal.name, MEETING_FLOW_BAP)

		events = self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_BAP)
		self.assertEqual(len(events), 1)
		self.assertEqual(events[0]["status"], "Cancelled")

	def test_created_event_has_no_calendar_provider_link(self):
		deal = self.make_deal("Workshop", "Workshop submitted")
		set_vcs_call(deal, {"vcs_datetime": "2026-09-15 09:00:00"})

		event = frappe.get_doc("Event", self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_VCS)[0]["name"])
		# No Google Calendar sync is requested on the Event we create.
		self.assertFalse(event.get("sync_with_google_calendar"))

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
	SUBJECT_NAME_SEPARATOR,
	_compose_subject,
	_format_person_name,
	_normalize_email,
	_primary_first,
	_reconcile_participants,
	cancel_meeting_event,
	deal_participants,
	lead_participants,
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


class FakeLead:
	def __init__(self, name=None, email=None, lead_owner=None):
		self.name = name
		self.email = email
		self.lead_owner = lead_owner

	def get(self, key, default=None):
		return getattr(self, key, default)


class FakeParticipant:
	"""Stands in for a saved Event Participant child row in the pure reconcile tests."""

	def __init__(self, reference_doctype=None, reference_docname=None, email=None):
		self.reference_doctype = reference_doctype
		self.reference_docname = reference_docname
		self.email = email

	def get(self, key, default=None):
		return getattr(self, key, default)


class FakeEvent:
	"""A minimal Event exposing only the participant table access `_reconcile_participants` uses."""

	def __init__(self, participants=None):
		self.event_participants = list(participants or [])

	def get(self, key, default=None):
		return getattr(self, key, default)

	def append(self, table, row):
		self.event_participants.append(
			FakeParticipant(row.get("reference_doctype"), row.get("reference_docname"), row.get("email"))
		)


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

	def test_lead_participants_target_only_when_no_owner(self):
		"""TXB-212: with only an email, the Lead itself is the resolvable customer target."""
		lead = FakeLead(name="LEAD-1", email="who@example.com")
		self.assertEqual(
			lead_participants(lead),
			[{"reference_doctype": "CRM Lead", "reference_docname": "LEAD-1", "email": "who@example.com"}],
		)

	def test_lead_participants_empty_when_no_owner_or_email(self):
		"""TXB-212: an unresolvable owner and target omit both attendees rather than blocking."""
		self.assertEqual(lead_participants(FakeLead(name="LEAD-2")), [])

	def test_deal_participants_omit_owner_when_unresolvable(self):
		"""TXB-212: a Deal with no owner still yields its Contacts -- the owner is simply absent."""
		deal = FakeDeal(contacts=[FakeRow("Contact-A")])
		self.assertEqual(
			deal_participants(deal),
			[{"reference_doctype": "Contact", "reference_docname": "Contact-A"}],
		)

	def test_normalize_email_trims_and_lowercases(self):
		self.assertEqual(_normalize_email("  Who@Example.COM "), "who@example.com")
		self.assertEqual(_normalize_email(None), "")

	def test_reconcile_is_additive_and_dedupes(self):
		"""TXB-212: required attendees are added once; existing manual rows survive untouched."""
		manual = FakeParticipant("Contact", "Manual-Contact", "manual@example.com")
		event = FakeEvent([manual])

		required = [
			{"reference_doctype": "User", "reference_docname": "owner@example.com", "email": "owner@example.com"},
			{"reference_doctype": "CRM Lead", "reference_docname": "LEAD-1", "email": "lead@example.com"},
		]
		_reconcile_participants(event, required)
		refs = {(p.reference_doctype, p.reference_docname) for p in event.event_participants}
		self.assertEqual(
			refs,
			{("Contact", "Manual-Contact"), ("User", "owner@example.com"), ("CRM Lead", "LEAD-1")},
		)

		# A second reconcile with the same required set adds nothing -- idempotent by reference.
		_reconcile_participants(event, required)
		self.assertEqual(len(event.event_participants), 3)

	def test_reconcile_collapses_shared_email_to_one_row(self):
		"""TXB-212: an owner and target that share a normalized email produce a single participant."""
		event = FakeEvent()
		_reconcile_participants(
			event,
			[
				{"reference_doctype": "User", "reference_docname": "same@example.com", "email": "Same@example.com"},
				{"reference_doctype": "CRM Lead", "reference_docname": "LEAD-1", "email": "same@example.com"},
			],
		)
		self.assertEqual(len(event.event_participants), 1)
		self.assertEqual(event.event_participants[0].reference_doctype, "User")

	def test_bap_address_joins_present_parts(self):
		self.assertEqual(bap_address("1 St", "", "LT"), "1 St, LT")
		self.assertEqual(bap_address(None, None, None), "")

	def test_discovery_starts_on_combines_date_and_time(self):
		starts = discovery_starts_on({"meeting_date": "2026-09-10", "meeting_time": "14:30:00"})
		self.assertEqual(starts, "2026-09-10 14:30:00")

	# -- TXB-213: customer-name subject composition (pure) ------------------------------

	def test_format_person_name_joins_present_parts(self):
		self.assertEqual(_format_person_name("  Ada  ", "Lovelace"), "Ada Lovelace")
		self.assertEqual(_format_person_name("Ada", None, ""), "Ada")

	def test_format_person_name_empty_when_nothing_resolves(self):
		"""No blank separators or stray whitespace when every part is absent."""
		self.assertEqual(_format_person_name(None, "", "   "), "")

	def test_primary_first_prefers_primary_then_keeps_table_order(self):
		"""The primary Contact sorts first; the rest keep their original listed order."""
		rows = [FakeRow("Contact-A"), FakeRow("Contact-B", is_primary=1), FakeRow("Contact-C")]
		self.assertEqual([r.contact for r in _primary_first(rows)], ["Contact-B", "Contact-A", "Contact-C"])

	def test_primary_first_without_primary_preserves_order(self):
		rows = [FakeRow("Contact-A"), FakeRow("Contact-B")]
		self.assertEqual([r.contact for r in _primary_first(rows)], ["Contact-A", "Contact-B"])

	def test_compose_subject_appends_nothing_for_unknown_source(self):
		"""A non-TXB source resolves no customer name, so the base title is returned unchanged --
		no dangling separator."""
		self.assertEqual(_compose_subject("Discovery Meeting", "ToDo", "X"), "Discovery Meeting")

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

	def make_user(self, email):
		if frappe.db.exists("User", email):
			return frappe.get_doc("User", email)
		return frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "TXB212", "send_welcome_email": 0}
		).insert(ignore_permissions=True)

	def participants_of(self, event_name):
		event = frappe.get_doc("Event", event_name)
		return event.event_participants

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

	# -- ac-1: required owner + target attendees (TXB-212) ------------------------------

	def test_lead_discovery_adds_owner_and_target_attendees(self):
		owner = self.make_user("txb212-owner@example.com")
		lead = frappe.get_doc(
			{
				"doctype": LEAD_DOCTYPE,
				"first_name": "TXB212",
				"status": "New",
				"lead_owner": owner.name,
				"email": "txb212-lead@example.com",
			}
		).insert(ignore_permissions=True)

		schedule_discovery(
			lead.name,
			activity={
				"meeting_date": "2026-09-10",
				"meeting_time": "14:30:00",
				"meeting_type": "Virtual",
				"meeting_link": "https://meet.example/abc",
			},
		)

		event_name = self.events_for(LEAD_DOCTYPE, lead.name, DISCOVERY_MEETING_FLOW)[0]["name"]
		refs = {(p.reference_doctype, p.reference_docname) for p in self.participants_of(event_name)}
		self.assertIn(("User", owner.name), refs)
		self.assertIn((LEAD_DOCTYPE, lead.name), refs)

	def test_lead_attendees_dedupe_shared_email(self):
		"""Owner and target that resolve to the same email land as one participant."""
		owner = self.make_user("shared212@example.com")
		lead = frappe.get_doc(
			{
				"doctype": LEAD_DOCTYPE,
				"first_name": "TXB212",
				"status": "New",
				"lead_owner": owner.name,
				"email": "shared212@example.com",
			}
		).insert(ignore_permissions=True)

		schedule_discovery(
			lead.name,
			activity={"meeting_date": "2026-09-10", "meeting_time": "14:30:00", "meeting_type": "Onsite", "meeting_address": "HQ"},
		)

		event_name = self.events_for(LEAD_DOCTYPE, lead.name, DISCOVERY_MEETING_FLOW)[0]["name"]
		shared = [p for p in self.participants_of(event_name) if _normalize_email(p.email) == "shared212@example.com"]
		self.assertEqual(len(shared), 1)

	def test_opportunity_meeting_adds_owner_and_contacts(self):
		owner = self.make_user("deal212-owner@example.com")
		contact = frappe.get_doc(
			{"doctype": "Contact", "first_name": "TXB212 Contact"}
		).insert(ignore_permissions=True)
		deal = self.make_deal("Individual Session", "Submitted", contacts=[{"contact": contact.name}])
		deal.deal_owner = owner.name

		book_bap(deal, {"bap_datetime": "2026-09-11 09:00:00", "bap_location_type": "Virtual"})

		event_name = self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_BAP)[0]["name"]
		refs = {(p.reference_doctype, p.reference_docname) for p in self.participants_of(event_name)}
		self.assertIn(("User", owner.name), refs)
		self.assertIn(("Contact", contact.name), refs)

	# -- ac-3: partial resolution never blocks the meeting ------------------------------

	def test_partial_resolution_owner_only(self):
		"""A Lead with an owner but no email schedules its meeting with just the owner attendee."""
		owner = self.make_user("partial212-owner@example.com")
		lead = frappe.get_doc(
			{"doctype": LEAD_DOCTYPE, "first_name": "TXB212", "status": "New", "lead_owner": owner.name}
		).insert(ignore_permissions=True)

		schedule_discovery(
			lead.name,
			activity={"meeting_date": "2026-09-10", "meeting_time": "14:30:00", "meeting_type": "Onsite", "meeting_address": "HQ"},
		)

		events = self.events_for(LEAD_DOCTYPE, lead.name, DISCOVERY_MEETING_FLOW)
		self.assertEqual(len(events), 1)
		refs = {(p.reference_doctype, p.reference_docname) for p in self.participants_of(events[0]["name"])}
		self.assertIn(("User", owner.name), refs)
		self.assertNotIn((LEAD_DOCTYPE, lead.name), refs)

	def test_no_resolvable_attendee_still_schedules(self):
		"""Neither owner nor email resolves, yet the meeting Event is still created."""
		lead = self.make_lead()

		schedule_discovery(
			lead.name,
			activity={"meeting_date": "2026-09-10", "meeting_time": "14:30:00", "meeting_type": "Onsite", "meeting_address": "HQ"},
		)

		self.assertEqual(len(self.events_for(LEAD_DOCTYPE, lead.name, DISCOVERY_MEETING_FLOW)), 1)

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

	def test_reschedule_adds_newly_resolvable_owner_and_keeps_manual(self):
		"""ac-2: a reschedule reconciles a now-resolvable owner onto the same Event without
		creating another and without dropping a manually added participant."""
		lead = frappe.get_doc(
			{"doctype": LEAD_DOCTYPE, "first_name": "TXB212", "status": "New", "email": "resched212@example.com"}
		).insert(ignore_permissions=True)

		schedule_discovery(
			lead.name,
			activity={"meeting_date": "2026-09-10", "meeting_time": "14:30:00", "meeting_type": "Onsite", "meeting_address": "HQ"},
		)
		event_name = self.events_for(LEAD_DOCTYPE, lead.name, DISCOVERY_MEETING_FLOW)[0]["name"]

		# A user manually adds an unrelated attendee to the canonical Event.
		manual = frappe.get_doc({"doctype": "Contact", "first_name": "TXB212 Manual"}).insert(
			ignore_permissions=True
		)
		event = frappe.get_doc("Event", event_name)
		event.append("event_participants", {"reference_doctype": "Contact", "reference_docname": manual.name})
		event.save(ignore_permissions=True)

		# The Lead gains an owner, then the meeting is rescheduled.
		owner = self.make_user("resched212-owner@example.com")
		frappe.db.set_value(LEAD_DOCTYPE, lead.name, "lead_owner", owner.name)
		schedule_discovery(
			lead.name,
			activity={"meeting_date": "2026-09-12", "meeting_time": "10:00:00", "meeting_type": "Onsite", "meeting_address": "HQ"},
		)

		events = self.events_for(LEAD_DOCTYPE, lead.name, DISCOVERY_MEETING_FLOW)
		self.assertEqual(len(events), 1)
		self.assertEqual(events[0]["name"], event_name)
		refs = {(p.reference_doctype, p.reference_docname) for p in self.participants_of(event_name)}
		self.assertIn(("User", owner.name), refs)  # newly resolvable owner reconciled in
		self.assertIn((LEAD_DOCTYPE, lead.name), refs)  # target still present
		self.assertIn(("Contact", manual.name), refs)  # manual participant survived

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

	# -- TXB-213: generated Event subjects name the customer ----------------------------

	def make_contact(self, first_name, last_name=None):
		return frappe.get_doc(
			{"doctype": "Contact", "first_name": first_name, "last_name": last_name}
		).insert(ignore_permissions=True)

	def test_lead_meeting_subject_names_the_lead(self):
		"""ac-1: a Lead meeting reads `<meeting title> — <Lead display name>`."""
		lead = frappe.get_doc(
			{"doctype": LEAD_DOCTYPE, "first_name": "Ada", "last_name": "Lovelace", "status": "New"}
		).insert(ignore_permissions=True)

		schedule_discovery(
			lead.name,
			activity={"meeting_date": "2026-09-10", "meeting_time": "14:30:00", "meeting_type": "Onsite", "meeting_address": "HQ"},
		)

		subject = self.events_for(LEAD_DOCTYPE, lead.name, DISCOVERY_MEETING_FLOW)[0]["subject"]
		self.assertTrue(subject.startswith(f"Discovery Meeting{SUBJECT_NAME_SEPARATOR}"), subject)
		self.assertIn("Ada", subject)
		self.assertIn("Lovelace", subject)

	def test_opportunity_meeting_subject_names_primary_contact(self):
		"""ac-1: an Opportunity meeting names the primary linked Contact, not the first listed."""
		first = self.make_contact("First", "Listed")
		primary = self.make_contact("Primary", "Choice")
		deal = self.make_deal(
			"Individual Session",
			"Submitted",
			contacts=[{"contact": first.name}, {"contact": primary.name, "is_primary": 1}],
		)

		book_bap(deal, {"bap_datetime": "2026-09-11 09:00:00", "bap_location_type": "Virtual"})

		subject = self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_BAP)[0]["subject"]
		self.assertTrue(subject.endswith(f"{SUBJECT_NAME_SEPARATOR}Primary Choice"), subject)

	def test_opportunity_meeting_subject_falls_back_to_first_contact(self):
		"""ac-1: with no primary Contact, the first linked Contact names the meeting."""
		first = self.make_contact("First", "Listed")
		second = self.make_contact("Second", "Listed")
		deal = self.make_deal(
			"Individual Session",
			"Submitted",
			contacts=[{"contact": first.name}, {"contact": second.name}],
		)

		book_bap(deal, {"bap_datetime": "2026-09-11 09:00:00", "bap_location_type": "Virtual"})

		subject = self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_BAP)[0]["subject"]
		self.assertTrue(subject.endswith(f"{SUBJECT_NAME_SEPARATOR}First Listed"), subject)

	def test_opportunity_meeting_subject_keeps_generic_title_without_contacts(self):
		"""ac-2: no resolvable customer name leaves the flow's generic title untouched."""
		deal = self.make_deal("Workshop", "Workshop submitted")

		set_vcs_call(deal, {"vcs_datetime": "2026-09-15 09:00:00"})

		subject = self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_VCS)[0]["subject"]
		self.assertNotIn(SUBJECT_NAME_SEPARATOR, subject)
		self.assertTrue(subject.strip())

	def test_reschedule_updates_subject_with_current_primary_contact(self):
		"""ac-3: a reschedule re-resolves the customer name onto the same Event identity."""
		alpha = self.make_contact("Alpha", "One")
		bravo = self.make_contact("Bravo", "Two")
		deal = self.make_deal(
			"Individual Session",
			"Session Set",
			contacts=[{"contact": alpha.name, "is_primary": 1}, {"contact": bravo.name}],
		)

		book_bap(deal, {"bap_datetime": "2026-09-18 09:00:00", "bap_location_type": "Virtual"})
		original = self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_BAP)[0]
		self.assertTrue(original["subject"].endswith(f"{SUBJECT_NAME_SEPARATOR}Alpha One"), original["subject"])

		# The primary Contact changes, then the meeting is rescheduled.
		deal = frappe.get_doc(DEAL_DOCTYPE, deal.name)
		for row in deal.contacts:
			row.is_primary = 1 if row.contact == bravo.name else 0
		deal.save(ignore_permissions=True)
		reschedule_bap(
			deal,
			{
				"reschedule_type": "Yes, I have a new date",
				"reschedule_reason": "clash",
				"new_bap_datetime": "2026-09-19 10:00:00",
				"new_location_type": "Virtual",
			},
		)

		events = self.events_for(DEAL_DOCTYPE, deal.name, MEETING_FLOW_BAP)
		self.assertEqual(len(events), 1)
		self.assertEqual(events[0]["name"], original["name"])  # same canonical Event
		self.assertTrue(events[0]["subject"].endswith(f"{SUBJECT_NAME_SEPARATOR}Bravo Two"), events[0]["subject"])

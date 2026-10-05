# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-277: typed, human-origin contact events.

Drives the real entry points -- the composer's send endpoint, the WhatsApp send endpoints, Log
Coaching Call and the meeting completion actions -- and checks that each records exactly one
CRM Human Contact Event, while the look-alike system writes (a Communication inserted directly,
an inbound or failed message, a reaction, a scheduled or cancelled meeting) record none.
"""

import json
from unittest.mock import patch

import frappe
from frappe.core.doctype.communication.communication import Communication
from frappe.tests.utils import FrappeTestCase

from crm.txb import human_contact
from crm.txb.api.human_contact import complete_meeting, human_send_email, reopen_meeting
from crm.txb.constants import FIELD_MEETING_KEY, PIPELINE_DELIVERING_COACHING
from crm.txb.human_contact import EVENT_DOCTYPE, classify, source_key, sync_source
from crm.txb.meetings import cancel_meeting_event, sync_meeting_event
from crm.txb.pipelines.delivering_coaching import log_coaching_call

LEAD_DOCTYPE = "CRM Lead"
DEAL_DOCTYPE = "CRM Deal"
NOTE_DOCTYPE = "FCRM Note"
WHATSAPP_DOCTYPE = "WhatsApp Message"


def events_for(doctype, name):
	return frappe.get_all(
		EVENT_DOCTYPE,
		filters={"source_key": source_key(doctype, name)},
		fields=["name", "channel", "provenance", "actor", "occurred_at", "recipients", "voided"],
	)


class HumanContactTestCase(FrappeTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def make_lead(self):
		return frappe.get_doc(
			{"doctype": LEAD_DOCTYPE, "first_name": "TXB277", "status": "New"}
		).insert(ignore_permissions=True)


class TestEmail(HumanContactTestCase):
	def send(self, lead, **overrides):
		values = {
			"recipients": "Client One <client.one@example.com>, client.two@example.com",
			"cc": "cc.person@example.com",
			"bcc": "hidden@example.com",
			"subject": "Hello",
			"content": "<p>Hi</p>",
			"doctype": LEAD_DOCTYPE,
			"name": lead.name,
		}
		values.update(overrides)
		# No outgoing account on a test site: stub the transport, keep the Communication insert.
		with (
			patch.object(Communication, "get_outgoing_email_account", return_value=frappe._dict(name="test")),
			patch.object(Communication, "send_email"),
		):
			return human_send_email(**values)

	def test_a_manual_send_records_one_human_email_event(self):
		lead = self.make_lead()
		result = self.send(lead)

		self.assertTrue(frappe.db.exists("Communication", result["communication"]))
		rows = events_for("Communication", result["communication"])
		self.assertEqual(len(rows), 1)
		row = rows[0]
		self.assertEqual(result["event"]["name"], row.name)
		self.assertEqual(row.channel, "Email")
		self.assertEqual(row.provenance, "Human")
		self.assertEqual(row.actor, frappe.session.user)
		self.assertTrue(row.occurred_at)
		self.assertFalse(row.voided)
		self.assertEqual(
			[r["email"] for r in json.loads(row.recipients)],
			["client.one@example.com", "client.two@example.com", "cc.person@example.com"],
		)
		self.assertEqual(result["event"]["reference_doctype"], LEAD_DOCTYPE)
		self.assertEqual(result["event"]["reference_name"], lead.name)

	def test_a_directly_inserted_communication_records_nothing(self):
		lead = self.make_lead()
		comm = frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_type": "Communication",
				"communication_medium": "Email",
				"sent_or_received": "Sent",
				"subject": "Reminder: first coaching call",
				"content": "System reminder",
				"recipients": "client.one@example.com",
				"reference_doctype": LEAD_DOCTYPE,
				"reference_name": lead.name,
			}
		).insert(ignore_permissions=True)

		self.assertIsNone(classify(comm))
		self.assertIsNone(sync_source("Communication", comm.name))
		self.assertEqual(events_for("Communication", comm.name), [])


class TestWhatsApp(HumanContactTestCase):
	def setUp(self):
		if not frappe.db.exists("DocType", WHATSAPP_DOCTYPE):
			self.skipTest("frappe_whatsapp is not installed on this site")

	def message(self, **values):
		doc = frappe.new_doc(WHATSAPP_DOCTYPE)
		doc.update({"to": "+37060000000", "message": "Hi", "content_type": "text", **values})
		doc.flags.ignore_validate = True
		# The send itself talks to Meta in before_insert; only the recording is under test.
		with patch.object(type(doc), "before_insert", create=True):
			doc.insert(ignore_permissions=True)
		return doc

	def test_an_outgoing_human_send_records_one_whatsapp_event(self):
		for values in ({}, {"message_type": "Template", "use_template": 1}):
			doc = self.message(type="Outgoing", **values)
			human_contact.record_human_send(doc)
			human_contact.sync_doc(doc)
			rows = events_for(WHATSAPP_DOCTYPE, doc.name)
			self.assertEqual(len(rows), 1)
			self.assertEqual(rows[0].channel, "WhatsApp")
			self.assertEqual(json.loads(rows[0].recipients), [{"phone": "+37060000000"}])

	def test_inbound_reaction_and_failed_messages_record_nothing(self):
		inbound = self.message(type="Incoming", **{"from": "+37060000000"})
		reaction = self.message(type="Outgoing", content_type="reaction", message="👍")
		failed = self.message(type="Outgoing", status="failed")
		external = self.message(type="Outgoing")
		for doc in (inbound, reaction, failed):
			self.assertIsNone(human_contact.record_human_send(doc))
		# An outgoing message another app wrote has no human origin.
		self.assertIsNone(human_contact.sync_doc(external))
		for doc in (inbound, reaction, failed, external):
			self.assertEqual(events_for(WHATSAPP_DOCTYPE, doc.name), [])

	def test_a_recorded_send_that_later_fails_is_voided(self):
		doc = self.message(type="Outgoing")
		human_contact.record_human_send(doc)
		doc.db_set("status", "failed")
		human_contact.sync_source(WHATSAPP_DOCTYPE, doc.name)
		self.assertEqual(events_for(WHATSAPP_DOCTYPE, doc.name)[0].voided, 1)


class CoachingCallTestCase(HumanContactTestCase):
	def setUp(self):
		from crm.patches.v1_0.reconcile_coaching_call_totals import _install_field as install_status
		from crm.patches.v1_0.seed_first_coaching_call_date import _install_field as install_date

		install_status()
		install_date()

	def log(self, call_status):
		deal = frappe.get_doc(
			{"doctype": DEAL_DOCTYPE, "pipeline_type": PIPELINE_DELIVERING_COACHING, "status": "Active"}
		).insert(ignore_permissions=True)
		log_coaching_call(deal, {"call_status": call_status, "delivery_date": "2026-08-17"})
		return frappe.get_last_doc(
			NOTE_DOCTYPE, filters={"reference_doctype": DEAL_DOCTYPE, "reference_docname": deal.name}
		)


class TestCoachingCall(CoachingCallTestCase):
	def test_a_completed_call_records_one_coaching_call_event_on_its_note(self):
		note = self.log("Completed")
		rows = events_for(NOTE_DOCTYPE, note.name)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].channel, "Coaching Call")
		self.assertEqual(str(rows[0].occurred_at.date()), "2026-08-17")

	def test_any_other_call_status_records_nothing(self):
		for status in ("Missed", "No charge"):
			note = self.log(status)
			self.assertEqual(events_for(NOTE_DOCTYPE, note.name), [])


class TestMeeting(HumanContactTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.installed = frappe.get_meta("Event").has_field(FIELD_MEETING_KEY)

	def setUp(self):
		if not self.installed:
			self.skipTest(f"{FIELD_MEETING_KEY} is not installed on this site")

	def schedule(self, lead, contact, starts_on="2026-09-10 10:00:00"):
		return sync_meeting_event(
			reference_doctype=LEAD_DOCTYPE,
			reference_docname=lead.name,
			flow="txb277",
			subject="Discovery",
			starts_on=starts_on,
			participants=[
				{"reference_doctype": "Contact", "reference_docname": contact.name, "email": "met@example.com"}
			],
		)

	def make_contact(self):
		return frappe.get_doc({"doctype": "Contact", "first_name": "TXB277"}).insert(
			ignore_permissions=True
		)

	def test_scheduling_rescheduling_and_cancelling_record_nothing(self):
		lead = self.make_lead()
		contact = self.make_contact()
		event = self.schedule(lead, contact)
		self.schedule(lead, contact, starts_on="2026-09-11 10:00:00")
		cancel_meeting_event(LEAD_DOCTYPE, lead.name, "txb277")
		self.assertEqual(events_for("Event", event), [])

	def test_complete_records_a_meeting_event_and_reopen_voids_it(self):
		lead = self.make_lead()
		contact = self.make_contact()
		event = self.schedule(lead, contact)

		completed = complete_meeting(event)
		self.assertEqual(completed["channel"], "Meeting")
		self.assertEqual(frappe.db.get_value("Event", event, "status"), "Completed")
		self.assertEqual(
			json.loads(completed["recipients"]), [{"email": "met@example.com", "contact": contact.name}]
		)

		reopened = reopen_meeting(event)
		self.assertEqual(reopened["name"], completed["name"])
		self.assertEqual(reopened["voided"], 1)
		self.assertEqual(frappe.db.get_value("Event", event, "status"), "Open")
		self.assertEqual(len(events_for("Event", event)), 1)

	def test_a_cancelled_meeting_cannot_be_completed(self):
		lead = self.make_lead()
		event = self.schedule(lead, self.make_contact())
		cancel_meeting_event(LEAD_DOCTYPE, lead.name, "txb277")
		with self.assertRaises(frappe.ValidationError):
			complete_meeting(event)
		self.assertEqual(events_for("Event", event), [])


class TestIdempotency(CoachingCallTestCase):
	def test_sync_twice_leaves_one_row_and_the_key_is_unique(self):
		note = self.log("Completed")
		first = sync_source(NOTE_DOCTYPE, note.name)
		second = sync_source(NOTE_DOCTYPE, note.name)
		self.assertEqual(first["name"], second["name"])
		self.assertEqual(len(events_for(NOTE_DOCTYPE, note.name)), 1)

		duplicate = frappe.get_doc(
			{
				"doctype": EVENT_DOCTYPE,
				"source_key": first["source_key"],
				"source_doctype": NOTE_DOCTYPE,
				"source_name": note.name,
				"channel": "Coaching Call",
				"provenance": "Human",
				"occurred_at": first["occurred_at"],
				"recipients": "[]",
			}
		)
		with self.assertRaises(frappe.UniqueValidationError):
			duplicate.insert(ignore_permissions=True)

	def test_a_source_that_stops_qualifying_is_voided_not_deleted(self):
		note = self.log("Completed")
		# The ledger row never blocks deleting its source (ignore_links_on_delete).
		frappe.delete_doc(NOTE_DOCTYPE, note.name, ignore_permissions=True)
		voided = sync_source(NOTE_DOCTYPE, note.name)
		self.assertEqual(voided["voided"], 1)
		self.assertEqual(len(events_for(NOTE_DOCTYPE, note.name)), 1)

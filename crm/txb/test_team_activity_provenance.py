# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-285: completed outgoing calls and logged coaching calls credit their recorded actor once.

Drives the real entry points -- a CRM Call Log saved through its doc events and the Log Coaching
Call action -- and checks the one CRM Human Contact Event each leaves behind: who it credits, that
a re-save never adds a second row, that a disqualifying edit or delete voids it, and which
Contacts a Call is attributed to.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime

from crm.fcrm.doctype.crm_deal.crm_deal import add_contact
from crm.fcrm.doctype.crm_lead.test_crm_lead import (
	create_lead,
	ensure_conversion_result_fields,
	ensure_deal_statuses,
)
from crm.txb.constants import (
	FIELD_CONVERTED_CONTACT,
	FIELD_COACHING_CALL_STATUS,
	OWNER_FIELDS,
	PIPELINE_DELIVERING_COACHING,
)
from crm.txb.contact_attribution import ATTRIBUTION_DOCTYPE
from crm.txb.contact_inactivity import CYCLE_DOCTYPE
from crm.txb.human_contact import EVENT_DOCTYPE, source_key
from crm.txb.pipelines.delivering_coaching import log_coaching_call

CALL_LOG_DOCTYPE = "CRM Call Log"
NOTE_DOCTYPE = "FCRM Note"
DEAL_DOCTYPE = "CRM Deal"
CONTACT_DOCTYPE = "Contact"
B_PHONE = "+370 698 76543"


def events_for(doctype, name):
	return frappe.get_all(
		EVENT_DOCTYPE,
		filters={"source_key": source_key(doctype, name)},
		fields=["name", "channel", "actor", "occurred_at", "voided", "reference_doctype", "reference_name"],
	)


def attributed_contacts(event):
	return sorted(frappe.get_all(ATTRIBUTION_DOCTYPE, filters={"event": event}, pluck="contact"))


def delete(doctype, name):
	# A fresh request: the link lookups cached while the event was inserted must not hide the
	# deleted source from the void that follows.
	frappe.db.value_cache.clear()
	frappe.delete_doc(doctype, name, ignore_permissions=True)


def make_user(email):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "TXB285", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
	return email


class ProvenanceTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_conversion_result_fields()
		ensure_deal_statuses()

	def setUp(self):
		suffix = frappe.generate_hash(length=6)
		self.caller = make_user(f"caller-{suffix}@example.com")
		self.other = make_user(f"other-{suffix}@example.com")
		lead = create_lead(first_name="Ann", email=f"ann-{suffix}@example.com", organization="TXB285 Co")
		self.deal = lead.convert_to_deal()
		self.contact_a = frappe.db.get_value("CRM Lead", lead.name, FIELD_CONVERTED_CONTACT)
		self.contact_b = self.make_contact("Bea", f"bea-{suffix}@example.com", B_PHONE)
		add_contact(self.deal, self.contact_b)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def make_contact(self, first_name, email, phone):
		doc = frappe.new_doc(CONTACT_DOCTYPE)
		doc.first_name = first_name
		doc.last_name = "TXB285"
		doc.append("email_ids", {"email_id": email, "is_primary": 1})
		doc.append("phone_nos", {"phone": phone, "is_primary_phone": 1})
		return doc.insert(ignore_permissions=True).name

	def call(self, **values):
		doc = {
			"doctype": CALL_LOG_DOCTYPE,
			"type": "Outgoing",
			"status": "Completed",
			"caller": self.caller,
			"from": "+370 600 11111",
			"to": "+370 600 00000",
			"start_time": "2026-09-01 10:00:00",
			"end_time": "2026-09-01 10:05:00",
			"reference_doctype": DEAL_DOCTYPE,
			"reference_docname": self.deal,
		}
		doc.update(values)
		return frappe.get_doc(doc).insert(ignore_permissions=True)


class TestCallLogEvent(ProvenanceTestCase):
	# -- ac-1 -----------------------------------------------------------------------------------

	def test_a_completed_outgoing_call_credits_its_caller_once(self):
		deal_owner = frappe.db.get_value(DEAL_DOCTYPE, self.deal, OWNER_FIELDS[DEAL_DOCTYPE])
		self.assertNotEqual(deal_owner, self.caller)

		log = self.call()

		rows = events_for(CALL_LOG_DOCTYPE, log.name)
		self.assertEqual(len(rows), 1)
		row = rows[0]
		self.assertEqual(row.channel, "Call")
		self.assertEqual(row.actor, self.caller)
		self.assertFalse(row.voided)
		self.assertEqual(str(row.occurred_at), "2026-09-01 10:05:00")
		self.assertEqual((row.reference_doctype, row.reference_name), (DEAL_DOCTYPE, self.deal))

	def test_occurred_at_and_actor_fall_back_in_order(self):
		started = self.call(caller=None, end_time=None)
		row = events_for(CALL_LOG_DOCTYPE, started.name)[0]
		self.assertEqual(row.actor, started.owner)
		self.assertEqual(str(row.occurred_at), "2026-09-01 10:00:00")

		bare = self.call(start_time=None, end_time=None)
		row = events_for(CALL_LOG_DOCTYPE, bare.name)[0]
		self.assertEqual(row.occurred_at, get_datetime(bare.creation))

	# -- ac-2 -----------------------------------------------------------------------------------

	def test_incoming_or_unfinished_calls_record_nothing(self):
		logs = [self.call(type="Incoming", receiver=self.caller, caller=None)]
		logs += [self.call(status=status) for status in ("No Answer", "Ringing", "Failed")]
		for log in logs:
			self.assertEqual(events_for(CALL_LOG_DOCTYPE, log.name), [], log.status)

	def test_resaving_keeps_one_row_with_the_original_actor(self):
		log = self.call()
		first = events_for(CALL_LOG_DOCTYPE, log.name)[0]

		log.caller = self.other
		log.save(ignore_permissions=True)
		log.save(ignore_permissions=True)

		rows = events_for(CALL_LOG_DOCTYPE, log.name)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].name, first.name)
		self.assertEqual(rows[0].actor, self.caller)

	# -- ac-3 -----------------------------------------------------------------------------------

	def test_a_disqualified_call_voids_and_requalifies_the_same_row(self):
		log = self.call()
		event = events_for(CALL_LOG_DOCTYPE, log.name)[0].name

		log.status = "Failed"
		log.save(ignore_permissions=True)
		rows = events_for(CALL_LOG_DOCTYPE, log.name)
		self.assertEqual([(r.name, r.voided) for r in rows], [(event, 1)])
		self.assertEqual(attributed_contacts(event), [])

		log.status = "Completed"
		log.save(ignore_permissions=True)
		rows = events_for(CALL_LOG_DOCTYPE, log.name)
		self.assertEqual([(r.name, r.voided) for r in rows], [(event, 0)])

		delete(CALL_LOG_DOCTYPE, log.name)
		rows = events_for(CALL_LOG_DOCTYPE, log.name)
		self.assertEqual([(r.name, r.voided) for r in rows], [(event, 1)])

	# -- ac-5 -----------------------------------------------------------------------------------

	def test_a_shared_deal_is_not_evidence_but_a_matching_phone_is(self):
		unmatched = events_for(CALL_LOG_DOCTYPE, self.call(to="+370 600 00000").name)[0]
		self.assertEqual(attributed_contacts(unmatched.name), [])

		matched = events_for(CALL_LOG_DOCTYPE, self.call(to="8 698 76543").name)[0]
		self.assertEqual(attributed_contacts(matched.name), [self.contact_b])

	def test_a_call_logged_on_the_contact_is_attributed_directly(self):
		log = self.call(reference_doctype=CONTACT_DOCTYPE, reference_docname=self.contact_a, to=None)
		row = events_for(CALL_LOG_DOCTYPE, log.name)[0]
		self.assertEqual(attributed_contacts(row.name), [self.contact_a])

	# -- ac-6 -----------------------------------------------------------------------------------

	def test_an_older_call_records_nothing_until_it_is_next_saved(self):
		cycles = frappe.db.count(CYCLE_DOCTYPE, {"contact": self.contact_a})
		# Logged before the hook existed: written straight to the table, so no doc event ran.
		old = frappe.get_doc(
			{
				"doctype": CALL_LOG_DOCTYPE,
				"name": frappe.generate_hash(length=10),
				"id": frappe.generate_hash(length=12),
				"type": "Outgoing",
				"status": "Completed",
				"caller": self.caller,
				"from": "+370 600 11111",
				"to": "-",
				"end_time": "2026-01-05 10:05:00",
				"reference_doctype": CONTACT_DOCTYPE,
				"reference_docname": self.contact_a,
				"owner": "Administrator",
				"creation": "2026-01-05 10:00:00",
				"modified": "2026-01-05 10:00:00",
			}
		)
		old.db_insert()

		self.assertEqual(events_for(CALL_LOG_DOCTYPE, old.name), [])
		self.assertEqual(frappe.db.count(CYCLE_DOCTYPE, {"contact": self.contact_a}), cycles)

		frappe.get_doc(CALL_LOG_DOCTYPE, old.name).save(ignore_permissions=True)
		rows = events_for(CALL_LOG_DOCTYPE, old.name)
		self.assertEqual([(r.channel, r.actor) for r in rows], [("Call", self.caller)])

	def test_owners_and_other_channels_are_left_alone(self):
		owners = {
			DEAL_DOCTYPE: frappe.db.get_value(DEAL_DOCTYPE, self.deal, OWNER_FIELDS[DEAL_DOCTYPE]),
		}
		contact_owner = OWNER_FIELDS[CONTACT_DOCTYPE]
		if frappe.get_meta(CONTACT_DOCTYPE).has_field(contact_owner):
			owners[CONTACT_DOCTYPE] = frappe.db.get_value(CONTACT_DOCTYPE, self.contact_b, contact_owner)

		email = frappe.get_doc(
			{
				"doctype": EVENT_DOCTYPE,
				"source_doctype": "Communication",
				"source_name": "TXB285-email",
				"source_key": source_key("Communication", "TXB285-email"),
				"channel": "Email",
				"provenance": "Human",
				"actor": "Administrator",
				"occurred_at": "2026-08-01 10:00:00",
				"reference_doctype": DEAL_DOCTYPE,
				"reference_name": self.deal,
				"recipients": "[]",
			}
		)
		email.flags.ignore_links = True
		email.insert(ignore_permissions=True)
		before = frappe.db.get_value(EVENT_DOCTYPE, email.name, ["voided", "actor", "modified"], as_dict=True)

		self.call(to=B_PHONE)

		self.assertEqual(
			frappe.db.get_value(EVENT_DOCTYPE, email.name, ["voided", "actor", "modified"], as_dict=True),
			before,
		)
		self.assertEqual(
			frappe.db.get_value(DEAL_DOCTYPE, self.deal, OWNER_FIELDS[DEAL_DOCTYPE]), owners[DEAL_DOCTYPE]
		)
		if CONTACT_DOCTYPE in owners:
			self.assertEqual(
				frappe.db.get_value(CONTACT_DOCTYPE, self.contact_b, contact_owner), owners[CONTACT_DOCTYPE]
			)


class TestCoachingCallEvent(ProvenanceTestCase):
	def setUp(self):
		super().setUp()
		from crm.patches.v1_0.reconcile_coaching_call_totals import _install_field as install_status
		from crm.patches.v1_0.seed_first_coaching_call_date import _install_field as install_date

		install_status()
		install_date()
		self.coaching_deal = frappe.get_doc(
			{"doctype": DEAL_DOCTYPE, "pipeline_type": PIPELINE_DELIVERING_COACHING, "status": "Active"}
		).insert(ignore_permissions=True)

	def log(self):
		log_coaching_call(
			self.coaching_deal,
			{"call_status": "Completed", "delivery_date": "2026-08-17", "send_recap": 0},
		)
		return frappe.get_last_doc(
			NOTE_DOCTYPE,
			filters={"reference_doctype": DEAL_DOCTYPE, "reference_docname": self.coaching_deal.name},
		)

	# -- ac-4 -----------------------------------------------------------------------------------

	def test_a_logged_coaching_call_records_one_event_for_the_coach(self):
		note = self.log()

		rows = events_for(NOTE_DOCTYPE, note.name)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].channel, "Coaching Call")
		self.assertEqual(rows[0].actor, note.owner)
		self.assertFalse(rows[0].voided)

	def test_the_same_note_inserted_by_a_system_path_records_nothing(self):
		note = frappe.get_doc(
			{
				"doctype": NOTE_DOCTYPE,
				"title": "Coaching Call #1 - 2026-08-17",
				"content": "Date: 2026-08-17<br>Call Status: Completed",
				"reference_doctype": DEAL_DOCTYPE,
				"reference_docname": self.coaching_deal.name,
				FIELD_COACHING_CALL_STATUS: "Completed",
			}
		).insert(ignore_permissions=True)
		note.save(ignore_permissions=True)

		self.assertEqual(events_for(NOTE_DOCTYPE, note.name), [])

	# -- ac-3 -----------------------------------------------------------------------------------

	def test_a_disqualified_note_voids_and_requalifies_the_same_row(self):
		note = self.log()
		event = events_for(NOTE_DOCTYPE, note.name)[0].name

		note.reload()
		note.content = note.content.replace("Call Status: Completed", "Call Status: Missed")
		note.save(ignore_permissions=True)
		rows = events_for(NOTE_DOCTYPE, note.name)
		self.assertEqual([(r.name, r.voided) for r in rows], [(event, 1)])

		note.content = note.content.replace("Call Status: Missed", "Call Status: Completed")
		note.save(ignore_permissions=True)
		rows = events_for(NOTE_DOCTYPE, note.name)
		self.assertEqual([(r.name, r.voided) for r in rows], [(event, 0)])

		delete(NOTE_DOCTYPE, note.name)
		rows = events_for(NOTE_DOCTYPE, note.name)
		self.assertEqual([(r.name, r.voided) for r in rows], [(event, 1)])

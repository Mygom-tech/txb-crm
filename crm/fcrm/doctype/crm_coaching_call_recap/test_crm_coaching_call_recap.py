# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""Tests for the Coaching Call recap ledger written by Log Coaching Call (TXB-273).

Exercised through `execute_action`, the endpoint the form calls, because what is under test is
that the Note, the recap row and the response land together -- or, for a refused or replayed
submission, that nothing more is written. Runs under `bench run-tests` and under a plain
`python -m pytest`: `setUpModule` connects to the local site only when no connection exists.
"""

import os
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.txb.api.actions import execute_action
from crm.txb.coaching_call_recap import (
	RECAP_DOCTYPE,
	RECAP_RECIPIENT_MISSING,
	STATUS_OPTED_OUT,
	STATUS_QUEUED,
	RecapRecipientMissing,
)
from crm.txb.constants import PIPELINE_DELIVERING_COACHING
from crm.txb.pipelines.common import DEAL_DOCTYPE, NOTE_DOCTYPE, TASK_DOCTYPE

LITHUANIAN = "ąčęėįšųūž"

_connected_here = False


def _sites_path() -> str:
	"""The bench `sites` directory: FRAPPE_SITES_PATH, else the first one above this file."""
	if os.environ.get("FRAPPE_SITES_PATH"):
		return os.environ["FRAPPE_SITES_PATH"]
	for parent in Path(__file__).resolve().parents:
		if (parent / "sites" / "apps.txt").exists():
			return str(parent / "sites")
	return "."


def setUpModule():
	global _connected_here
	if not getattr(frappe.local, "db", None):
		frappe.init(site=os.environ.get("FRAPPE_SITE", "localhost"), sites_path=_sites_path())
		frappe.connect()
		_connected_here = True
	# An un-migrated site has no table for the new doctype yet; sync it from this checkout.
	if not frappe.db.table_exists(RECAP_DOCTYPE):
		frappe.reload_doc("fcrm", "doctype", "crm_coaching_call_recap")


def tearDownModule():
	if _connected_here:
		frappe.db.rollback()
		frappe.destroy()


class TestCoachingCallRecap(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.flags.txb_action = None
		frappe.db.rollback()

	def make_contact(self, email=None):
		values = {"doctype": "Contact", "first_name": frappe.generate_hash("Client", 8)}
		if email:
			values["email_ids"] = [{"email_id": email, "is_primary": 1}]
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def make_deal(self, contact=None):
		values = {
			"doctype": DEAL_DOCTYPE,
			"pipeline_type": PIPELINE_DELIVERING_COACHING,
			"status": "Active",
		}
		if contact:
			values["contacts"] = [{"contact": contact.name, "is_primary": 1}]
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def make_deal_with_email(self):
		email = f"{frappe.generate_hash(length=10)}@example.com"
		contact = self.make_contact(email)
		return self.make_deal(contact), contact, email

	def log_call(self, deal, **data):
		values = {
			"call_status": "Completed",
			"delivery_date": "2026-10-05",
			"topic": "Leadership styles",
			"call_notes": "Reviewed DISC results",
			"is_last_call": 0,
			"next_call_date": "2026-10-12 10:00:00",
		}
		values.update(data)
		return execute_action(deal.name, "log_coaching_call", values)

	def count(self, doctype, deal, **filters):
		if doctype == RECAP_DOCTYPE:
			return frappe.db.count(RECAP_DOCTYPE, {"deal": deal.name, **filters})
		return frappe.db.count(
			doctype, {"reference_doctype": DEAL_DOCTYPE, "reference_docname": deal.name, **filters}
		)

	def next_call_tasks(self, deal):
		return self.count(TASK_DOCTYPE, deal, title=["like", "Next Coaching Call%"])

	def recap(self, name):
		return frappe.get_doc(RECAP_DOCTYPE, name)

	# ac-1
	def test_an_omitted_or_ticked_send_recap_queues_one_recap_to_the_primary_email(self):
		ticked = ({}, {"send_recap": 1}, {"send_recap": None}, {"send_recap": "true"}, {"send_recap": "Yes"})
		for extra in ticked:
			with self.subTest(extra=extra):
				deal, contact, email = self.make_deal_with_email()

				result = self.log_call(deal, **extra)

				self.assertEqual(self.count(NOTE_DOCTYPE, deal), 1)
				self.assertEqual(self.count(RECAP_DOCTYPE, deal), 1)
				recap = self.recap(result["recap"]["name"])
				self.assertEqual(result["recap"], {"name": recap.name, "status": STATUS_QUEUED})
				self.assertEqual(result["deal"], deal.name)
				self.assertEqual(recap.status, STATUS_QUEUED)
				self.assertEqual(recap.consent, 1)
				self.assertEqual(recap.recipient_email, email)
				self.assertEqual(recap.recipient_contact, contact.name)
				self.assertEqual(recap.note, result["note"])
				self.assertTrue(recap.submission_id)

	# ac-2
	def test_an_unticked_send_recap_records_an_opt_out_without_a_recipient(self):
		for value in (0, "0", "false", "no"):
			with self.subTest(send_recap=value):
				deal = self.make_deal()

				result = self.log_call(deal, send_recap=value)

				self.assertEqual(self.count(NOTE_DOCTYPE, deal), 1)
				recap = self.recap(result["recap"]["name"])
				self.assertEqual(result["recap"]["status"], STATUS_OPTED_OUT)
				self.assertEqual(recap.status, STATUS_OPTED_OUT)
				self.assertEqual(recap.consent, 0)
				self.assertEqual(recap.consent_by, "Administrator")
				self.assertTrue(recap.consent_at)
				self.assertFalse(recap.recipient_email)
				self.assertEqual(recap.note, result["note"])

	def test_an_unrecognized_send_recap_is_refused_rather_than_read_as_an_opt_out(self):
		deal, _contact, _email = self.make_deal_with_email()

		with self.assertRaises(frappe.ValidationError):
			self.log_call(deal, send_recap="maybe")

		self.assertEqual(self.count(NOTE_DOCTYPE, deal), 0)
		self.assertEqual(self.count(RECAP_DOCTYPE, deal), 0)

	# ac-3
	def test_a_ticked_send_recap_without_a_usable_email_is_refused_and_writes_nothing(self):
		fields = ["custom_next_call_date", "custom_last_coaching_call", "total_completed_calls", "modified"]
		for contact in (None, self.make_contact()):
			with self.subTest(contact=contact and contact.name):
				deal = self.make_deal(contact)
				before = frappe.db.get_value(DEAL_DOCTYPE, deal.name, fields, as_dict=True)
				tasks_before = self.count(TASK_DOCTYPE, deal)

				with self.assertRaises(RecapRecipientMissing) as ctx:
					self.log_call(deal, send_recap=1)

				self.assertIsInstance(ctx.exception, frappe.ValidationError)
				self.assertIn(RECAP_RECIPIENT_MISSING, str(ctx.exception))
				self.assertEqual(self.count(NOTE_DOCTYPE, deal), 0)
				self.assertEqual(self.count(RECAP_DOCTYPE, deal), 0)
				self.assertEqual(self.count(TASK_DOCTYPE, deal), tasks_before)
				self.assertEqual(
					frappe.db.get_value(DEAL_DOCTYPE, deal.name, fields, as_dict=True), before
				)

	# ac-4
	def test_a_replayed_submission_returns_the_original_and_writes_nothing_more(self):
		deal, _contact, _email = self.make_deal_with_email()
		submission_id = frappe.generate_hash(length=20)

		first = self.log_call(deal, submission_id=submission_id)
		modified = frappe.db.get_value(DEAL_DOCTYPE, deal.name, "modified")
		second = self.log_call(deal, submission_id=submission_id, call_notes="Edited resubmission")

		self.assertEqual(second["note"], first["note"])
		self.assertEqual(second["recap"], first["recap"])
		self.assertNotIn("_txb_skip_save", second)
		# The replay does not save the deal again.
		self.assertEqual(frappe.db.get_value(DEAL_DOCTYPE, deal.name, "modified"), modified)
		self.assertEqual(self.count(NOTE_DOCTYPE, deal), 1)
		self.assertEqual(self.count(RECAP_DOCTYPE, deal), 1)
		self.assertEqual(self.next_call_tasks(deal), 1)
		self.assertEqual(self.recap(first["recap"]["name"]).submission_id, submission_id)

	# ac-5
	def test_the_snapshot_keeps_lithuanian_text_and_escapes_coach_markup(self):
		deal, _contact, _email = self.make_deal_with_email()

		result = self.log_call(
			deal,
			topic=f"Lyderystė {LITHUANIAN}",
			call_notes=f"Aptarėme {LITHUANIAN}\n<script>alert(1)</script>",
		)

		raw = frappe.db.get_value(RECAP_DOCTYPE, result["recap"]["name"], "content_snapshot")
		self.assertIn(LITHUANIAN, raw)
		snapshot = frappe.parse_json(raw)
		self.assertEqual(
			snapshot["call_notes_html"],
			f"Aptarėme {LITHUANIAN}<br>&lt;script&gt;alert(1)&lt;/script&gt;",
		)
		self.assertNotIn("<script>", snapshot["call_notes_html"])
		self.assertEqual(snapshot["topic"], f"Lyderystė {LITHUANIAN}")
		self.assertEqual(snapshot["subject"], frappe.db.get_value(NOTE_DOCTYPE, result["note"], "title"))
		self.assertEqual(snapshot["call_status"], "Completed")
		self.assertEqual(snapshot["delivery_date"], "2026-10-05")
		self.assertEqual(snapshot["next_call_date"], "2026-10-12")
		self.assertEqual(snapshot["is_last_call"], 0)

	# ac-6
	def test_a_coaching_call_note_written_directly_records_no_recap(self):
		deal, _contact, _email = self.make_deal_with_email()

		note = frappe.get_doc(
			{
				"doctype": NOTE_DOCTYPE,
				"reference_doctype": DEAL_DOCTYPE,
				"reference_docname": deal.name,
				"title": "Coaching Call #1 - 2026-10-05 - Direct",
				"content": "Call Status: Completed",
			}
		).insert(ignore_permissions=True)
		note.content = "Call Status: Completed<br>Edited"
		note.save(ignore_permissions=True)

		self.assertEqual(self.count(RECAP_DOCTYPE, deal), 0)

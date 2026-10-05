# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-274: queued Coaching Call recaps are sent once, after commit, and can be recovered.

Pins the delivery worker (one send per queued row, ending sent or failed), the escaped
Lithuanian-safe text-only email, the Retry and Send revised copy commands and the Deal Activity
item. `frappe.sendmail` and `frappe.enqueue` are mocked at their boundaries. Runs under
`bench run-tests` and under a plain `python -m pytest`: `setUpModule` connects to the local site
only when no connection exists.
"""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import frappe

from crm.api.activities import _coaching_recap_events, get_deal_activities
from crm.txb.api.coaching_call_recap import retry_recap, send_revised_copy
from crm.txb.coaching_call_recap import (
	RECAP_DOCTYPE,
	STATUS_OPTED_OUT,
	STATUS_QUEUED,
	build_snapshot,
)
from crm.txb.coaching_call_recap_dispatch import (
	RECIPIENT_MISSING,
	STATUS_FAILED,
	STATUS_SENT,
	enqueue_recap,
	process_recap,
)
from crm.txb.coaching_call_recap_email import render_recap_email
from crm.txb.constants import PIPELINE_DELIVERING_COACHING
from crm.txb.pipelines.common import DEAL_DOCTYPE

LITHUANIAN = "ąčęėįšųūž"
WORKER = "crm.txb.coaching_call_recap_dispatch.process_recap"
NO_ACCESS_USER = "txb-recap-no-access@example.com"

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


def tearDownModule():
	if _connected_here:
		frappe.db.rollback()
		frappe.destroy()


class TestCoachingCallRecapDispatch(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.enqueue = MagicMock()
		self.sendmail = MagicMock()
		for patcher in (
			patch("frappe.enqueue", self.enqueue),
			patch("frappe.sendmail", self.sendmail),
			patch("frappe.log_error"),
			# The worker commits its outcome; keep every test inside its rollback.
			patch.object(frappe.db, "commit"),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def make_deal(self):
		return frappe.get_doc(
			{"doctype": DEAL_DOCTYPE, "pipeline_type": PIPELINE_DELIVERING_COACHING, "status": "Active"}
		).insert(ignore_permissions=True)

	def make_recap(self, deal, status=STATUS_QUEUED, **data):
		consent = status != STATUS_OPTED_OUT
		values = {
			"call_status": "Completed",
			"delivery_date": "2026-10-05",
			"topic": "Leadership styles",
			"call_notes": "Reviewed DISC results",
		}
		values.update(data)
		recap = frappe.get_doc(
			{
				"doctype": RECAP_DOCTYPE,
				"submission_id": frappe.generate_hash(length=20),
				"deal": deal.name,
				"consent": 1 if consent else 0,
				"consent_by": "Administrator",
				"recipient_email": f"{frappe.generate_hash(length=10)}@example.com" if consent else None,
				"content_snapshot": frappe.as_json(
					build_snapshot("Coaching Call #1", values), ensure_ascii=False
				),
				"status": STATUS_QUEUED if consent else STATUS_OPTED_OUT,
				"attempts": 0,
				"created_by": "Administrator",
			}
		).insert(ignore_permissions=True)
		if status not in (STATUS_QUEUED, STATUS_OPTED_OUT):
			recap.db_set("status", status)
		return recap

	def recap(self, name):
		return frappe.get_doc(RECAP_DOCTYPE, name)

	def jobs_for(self, name):
		return [c for c in self.enqueue.call_args_list if c.kwargs.get("recap") == name]

	def as_user_without_deal_write(self):
		if not frappe.db.exists("User", NO_ACCESS_USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": NO_ACCESS_USER,
					"first_name": "No access",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)
		frappe.set_user(NO_ACCESS_USER)

	# ac-1
	def test_a_queued_recap_is_sent_once_and_a_settled_one_never_again(self):
		recap = self.make_recap(self.make_deal())

		process_recap(recap.name)
		process_recap(recap.name)

		self.sendmail.assert_called_once()
		self.assertEqual(self.sendmail.call_args.kwargs["recipients"], [recap.recipient_email])
		row = self.recap(recap.name)
		self.assertEqual(row.status, STATUS_SENT)
		self.assertEqual(row.attempts, 1)
		self.assertIsNotNone(row.sent_at)

		failed = self.make_recap(self.make_deal(), status=STATUS_FAILED)
		process_recap(failed.name)
		self.sendmail.assert_called_once()
		self.assertEqual(self.recap(failed.name).status, STATUS_FAILED)

	# ac-2
	def test_enqueue_recap_queues_one_deduplicated_job_per_recap_after_commit(self):
		deal = self.make_deal()
		first, second = self.make_recap(deal), self.make_recap(deal)
		self.enqueue.reset_mock()

		enqueue_recap(first.name)
		enqueue_recap(second.name)

		job_ids = []
		for call in self.enqueue.call_args_list:
			self.assertEqual(call.args[0], WORKER)
			self.assertIs(call.kwargs["enqueue_after_commit"], True)
			self.assertIs(call.kwargs["deduplicate"], True)
			job_ids.append(call.kwargs["job_id"])
		self.assertEqual(len(set(job_ids)), 2)
		self.assertIn(first.name, job_ids[0])
		self.assertIn(second.name, job_ids[1])
		self.sendmail.assert_not_called()

	def test_inserting_a_queued_recap_queues_its_send_and_an_opt_out_queues_nothing(self):
		deal = self.make_deal()
		queued = self.make_recap(deal)
		opted_out = self.make_recap(deal, status=STATUS_OPTED_OUT)

		self.assertEqual(len(self.jobs_for(queued.name)), 1)
		self.assertTrue(self.jobs_for(queued.name)[0].kwargs["enqueue_after_commit"])
		self.assertEqual(self.jobs_for(opted_out.name), [])
		self.sendmail.assert_not_called()

	# ac-3
	def test_a_send_error_fails_the_recap_with_a_sanitized_error(self):
		recap = self.make_recap(self.make_deal())
		token = "-".join(("xoxb", "123456789012", "abcdefghijklmnopQRSTUVWX"))
		self.sendmail.side_effect = Exception(f"SMTP refused\n\n  using {token}")

		process_recap(recap.name)

		row = self.recap(recap.name)
		self.assertEqual(row.status, STATUS_FAILED)
		self.assertEqual(row.attempts, 1)
		self.assertIsNone(row.sent_at)
		self.assertIn("SMTP refused", row.last_error)
		self.assertNotIn(token, row.last_error)
		self.assertNotIn("\n", row.last_error)

	def test_a_recap_without_a_recipient_fails_without_sending(self):
		recap = self.make_recap(self.make_deal())
		frappe.db.set_value(RECAP_DOCTYPE, recap.name, "recipient_email", None)

		process_recap(recap.name)

		self.sendmail.assert_not_called()
		row = self.recap(recap.name)
		self.assertEqual(row.status, STATUS_FAILED)
		self.assertIn(RECIPIENT_MISSING, row.last_error)

	# ac-4
	def test_the_email_keeps_lithuanian_text_escapes_coach_markup_and_has_no_attachments(self):
		recap = self.make_recap(
			self.make_deal(),
			topic=f"<b>{LITHUANIAN}</b>",
			call_notes=f"Pastabos {LITHUANIAN}\n<script>alert('x')</script><img src=x>",
		)

		process_recap(recap.name)

		kwargs = self.sendmail.call_args.kwargs
		message = kwargs["message"]
		self.assertIn(LITHUANIAN, message)
		self.assertNotIn("<script>", message)
		self.assertNotIn("<img", message)
		self.assertNotIn("<b>", message)
		self.assertIn("&lt;script&gt;", message)
		self.assertIn("&lt;b&gt;", message)
		self.assertIn("<br>", message)
		self.assertEqual(kwargs["attachments"], [])
		self.assertEqual(kwargs["inline_images"], [])

	def test_the_render_reads_only_the_snapshot_and_escapes_it_exactly_once(self):
		snapshot = json.loads(
			frappe.as_json(
				build_snapshot(
					f"Coaching Call #2 {LITHUANIAN}\nBcc: x@example.com",
					{"call_status": "Completed", "call_notes": "Tom & Jerry <3", "is_last_call": 1},
				)
			)
		)

		subject, message = render_recap_email(snapshot)

		self.assertEqual(subject, f"Coaching Call #2 {LITHUANIAN} Bcc: x@example.com")
		self.assertIn("Tom &amp; Jerry &lt;3", message)
		self.assertNotIn("&amp;amp;", message)

	# ac-5
	def test_retry_requeues_only_a_failed_recap(self):
		deal = self.make_deal()
		failed = self.make_recap(deal, status=STATUS_FAILED)
		frappe.db.set_value(RECAP_DOCTYPE, failed.name, "last_error", "SMTP refused")
		self.enqueue.reset_mock()

		self.assertEqual(retry_recap(failed.name), {"name": failed.name, "status": STATUS_QUEUED})
		self.assertEqual(self.recap(failed.name).status, STATUS_QUEUED)
		self.assertIsNone(self.recap(failed.name).last_error)
		self.assertEqual(len(self.jobs_for(failed.name)), 1)
		self.sendmail.assert_not_called()

		for status in (STATUS_QUEUED, STATUS_SENT, STATUS_OPTED_OUT):
			other = self.make_recap(deal, status=status)
			with self.assertRaises(frappe.ValidationError):
				retry_recap(other.name)
			self.assertEqual(self.recap(other.name).status, status)

	def test_send_revised_copy_needs_a_sent_recap_and_confirmation(self):
		deal = self.make_deal()
		sent = self.make_recap(deal, status=STATUS_SENT)

		with self.assertRaises(frappe.ValidationError):
			send_revised_copy(sent.name, confirm=0)
		for status in (STATUS_QUEUED, STATUS_FAILED, STATUS_OPTED_OUT):
			with self.assertRaises(frappe.ValidationError):
				send_revised_copy(self.make_recap(deal, status=status).name, confirm=1)
		self.enqueue.reset_mock()

		result = send_revised_copy(sent.name, confirm=1)

		self.assertNotEqual(result["name"], sent.name)
		self.assertEqual(result["status"], STATUS_QUEUED)
		self.assertEqual(result["revision_of"], sent.name)
		copy = self.recap(result["name"])
		self.assertEqual(copy.status, STATUS_QUEUED)
		self.assertEqual(copy.revision_of, sent.name)
		self.assertEqual(copy.deal, deal.name)
		self.assertEqual(copy.recipient_email, sent.recipient_email)
		self.assertEqual(json.loads(copy.content_snapshot), json.loads(sent.content_snapshot))
		self.assertEqual(self.recap(sent.name).status, STATUS_SENT)
		self.assertEqual(len(self.jobs_for(copy.name)), 1)
		self.sendmail.assert_not_called()

	def test_both_commands_need_write_access_to_the_deal(self):
		deal = self.make_deal()
		failed = self.make_recap(deal, status=STATUS_FAILED)
		sent = self.make_recap(deal, status=STATUS_SENT)
		self.as_user_without_deal_write()

		with self.assertRaises(frappe.PermissionError):
			retry_recap(failed.name)
		with self.assertRaises(frappe.PermissionError):
			send_revised_copy(sent.name, confirm=1)
		frappe.set_user("Administrator")
		self.assertEqual(self.recap(failed.name).status, STATUS_FAILED)
		self.assertEqual(frappe.db.count(RECAP_DOCTYPE, {"revision_of": sent.name}), 0)

	# ac-6
	def test_the_deal_activity_shows_one_recap_item_per_row_with_its_commands(self):
		deal = self.make_deal()
		statuses = {
			self.make_recap(deal, status=status).name: status
			for status in (STATUS_QUEUED, STATUS_SENT, STATUS_FAILED, STATUS_OPTED_OUT)
		}

		items = [a for a in get_deal_activities(deal.name)[0] if a["activity_type"] == "coaching_recap"]

		self.assertEqual(sorted(a["recap"] for a in items), sorted(statuses))
		for item in items:
			self.assertEqual(item["status"], statuses[item["recap"]])
			self.assertEqual(item["can_retry"], item["status"] == STATUS_FAILED)
			self.assertEqual(item["can_send_revised"], item["status"] == STATUS_SENT)

		self.as_user_without_deal_write()
		for item in _coaching_recap_events(deal.name):
			self.assertFalse(item["can_retry"])
			self.assertFalse(item["can_send_revised"])

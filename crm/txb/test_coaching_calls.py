# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""Tests for the canonical completed coaching call count (TXB-247).

Two layers. The classifier is pure text work -- which notes carry an explicit status and
which stay unclassified -- and is exercised without a database. The reconciliation layer
needs real documents, because what is under test is precisely that the deal's stored total
follows its notes through insert, edit, delete and a move between deals.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.txb.coaching_calls import (
	count_completed_calls,
	is_coaching_call_note,
	note_status,
	reconcile_deal,
)
from crm.txb.constants import (
	FIELD_COACHING_CALL_STATUS,
	PIPELINE_DELIVERING_COACHING,
	PIPELINE_WORKSHOP,
)

DEAL_DOCTYPE = "CRM Deal"
NOTE_DOCTYPE = "FCRM Note"

COMPLETED_BODY = "Date: 2026-08-17<br>Call Status: Completed<br>Topic: Leadership styles"
MISSED_BODY = "Date: 2026-08-17<br>Call Status: Missed<br>Topic: Leadership styles"


class TestCoachingCallClassification(FrappeTestCase):
	"""Only an exact, explicit status counts; everything else stays unclassified."""

	def test_an_explicit_completed_status_is_read_from_the_note_body(self):
		self.assertEqual(note_status("Coaching Call #1 - 2026-08-17", COMPLETED_BODY), "Completed")

	def test_the_other_supported_statuses_are_read_but_are_not_completed(self):
		self.assertEqual(note_status("Coaching Call #2 - 2026-08-17", MISSED_BODY), "Missed")
		self.assertEqual(
			note_status("Coaching Call #3 - 2026-08-17", "Call Status: No charge"), "No charge"
		)

	def test_a_coaching_note_without_a_status_marker_stays_unclassified(self):
		self.assertIsNone(note_status("Coaching Call #4 - 2026-08-17", "Topic: Leadership styles"))

	def test_a_qualified_status_is_not_read_as_completed(self):
		self.assertIsNone(note_status("Coaching Call #5 - 2026-08-17", "Call Status: Not completed"))
		self.assertIsNone(
			note_status("Coaching Call #6 - 2026-08-17", "Call Status: Completed early")
		)

	def test_an_ordinary_note_is_never_classified_whatever_its_body_says(self):
		self.assertIsNone(note_status("Put on Hold - 2026-08-17", COMPLETED_BODY))
		self.assertFalse(is_coaching_call_note("Contract Cleared - 2026-08-17"))

	def test_html_is_normalized_before_the_status_is_read(self):
		body = "<p>Date: 2026-08-17</p><p><b>Call&nbsp;Status:</b>&#32;Completed</p>"
		self.assertEqual(note_status("<b>Coaching Call</b> #7 - 2026-08-17", body), "Completed")

	def test_a_body_status_wins_over_stale_stored_metadata(self):
		self.assertEqual(
			note_status("Coaching Call #8 - 2026-08-17", MISSED_BODY, "Completed"), "Missed"
		)

	def test_stored_metadata_classifies_a_note_whose_body_lost_its_marker(self):
		self.assertEqual(note_status("Coaching Call #9 - 2026-08-17", "Topic: X", "Completed"), "Completed")

	def test_an_unsupported_stored_value_is_ignored(self):
		self.assertIsNone(note_status("Coaching Call #10 - 2026-08-17", "Topic: X", "Rescheduled"))


class TestCoachingCallReconciliation(FrappeTestCase):
	"""The stored total follows the deal's current linked notes through every note event."""

	def tearDown(self):
		frappe.db.rollback()

	def make_deal(self, pipeline=PIPELINE_DELIVERING_COACHING, total=None):
		values = {"doctype": DEAL_DOCTYPE, "pipeline_type": pipeline, "status": "Active"}
		if total is not None:
			values["total_completed_calls"] = total
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def make_note(self, deal, title, content):
		return frappe.get_doc(
			{
				"doctype": NOTE_DOCTYPE,
				"reference_doctype": DEAL_DOCTYPE,
				"reference_docname": deal.name,
				"title": title,
				"content": content,
			}
		).insert(ignore_permissions=True)

	def stored_total(self, deal):
		return frappe.db.get_value(DEAL_DOCTYPE, deal.name, "total_completed_calls")

	def test_only_completed_coaching_notes_are_counted(self):
		deal = self.make_deal()
		self.make_note(deal, "Coaching Call #1 - 2026-08-17", COMPLETED_BODY)
		self.make_note(deal, "Coaching Call #2 - 2026-08-18", MISSED_BODY)
		self.make_note(deal, "Coaching Call #3 - 2026-08-19", "Call Status: No charge")
		self.make_note(deal, "Coaching Call #4 - 2026-08-20", "Topic: no status here")
		self.make_note(deal, "Put on Hold - 2026-08-21", COMPLETED_BODY)

		self.assertEqual(count_completed_calls(deal.name), 1)
		self.assertEqual(self.stored_total(deal), 1)

	def test_inserting_a_completed_note_recounts_the_deal(self):
		deal = self.make_deal(total=0)
		self.make_note(deal, "Coaching Call #1 - 2026-08-17", COMPLETED_BODY)

		self.assertEqual(self.stored_total(deal), 1)

	def test_editing_a_note_status_recounts_the_deal(self):
		deal = self.make_deal()
		note = self.make_note(deal, "Coaching Call #1 - 2026-08-17", COMPLETED_BODY)
		self.assertEqual(self.stored_total(deal), 1)

		note.content = MISSED_BODY
		note.save(ignore_permissions=True)

		self.assertEqual(self.stored_total(deal), 0)

	def test_deleting_a_note_recounts_the_deal(self):
		deal = self.make_deal()
		note = self.make_note(deal, "Coaching Call #1 - 2026-08-17", COMPLETED_BODY)
		self.assertEqual(self.stored_total(deal), 1)

		note.delete(ignore_permissions=True)

		self.assertEqual(self.stored_total(deal), 0)

	def test_moving_a_note_between_deals_recounts_both(self):
		source = self.make_deal()
		target = self.make_deal()
		note = self.make_note(source, "Coaching Call #1 - 2026-08-17", COMPLETED_BODY)
		self.assertEqual(self.stored_total(source), 1)

		note.reference_docname = target.name
		note.save(ignore_permissions=True)

		self.assertEqual(self.stored_total(source), 0)
		self.assertEqual(self.stored_total(target), 1)

	def test_a_stale_total_is_repaired_by_a_recount(self):
		deal = self.make_deal(total=7)
		self.assertEqual(reconcile_deal(deal.name), 0)
		self.assertEqual(self.stored_total(deal), 0)

	def test_another_pipeline_is_left_alone(self):
		deal = self.make_deal(pipeline=PIPELINE_WORKSHOP, total=4)
		self.make_note(deal, "Coaching Call #1 - 2026-08-17", COMPLETED_BODY)

		self.assertIsNone(reconcile_deal(deal.name))
		self.assertEqual(self.stored_total(deal), 4)

	def test_a_call_log_cannot_overwrite_a_coaching_total(self):
		from crm.txb.doc_events.call_log import update_deal_call_count

		deal = self.make_deal()
		self.make_note(deal, "Coaching Call #1 - 2026-08-17", COMPLETED_BODY)

		call_log = frappe.get_doc(
			{
				"doctype": "CRM Call Log",
				"id": frappe.generate_hash(length=12),
				"telephony_medium": "Manual",
				"type": "Outgoing",
				"status": "Completed",
				"from": "-",
				"to": "-",
				"reference_doctype": DEAL_DOCTYPE,
				"reference_docname": deal.name,
			}
		).insert(ignore_permissions=True)
		update_deal_call_count(call_log)

		self.assertEqual(self.stored_total(deal), 1)

	def test_a_logged_coaching_call_seeds_the_notes_status_metadata(self):
		from crm.txb.coaching_calls import status_field_installed
		from crm.txb.pipelines.delivering_coaching import log_coaching_call

		# The field arrives with the reconcile patch; the count itself never depends on it.
		if not status_field_installed():
			self.skipTest(f"{FIELD_COACHING_CALL_STATUS} is not installed on this site")

		deal = self.make_deal(total=3)
		log_coaching_call(
			deal,
			{
				"call_status": "Completed",
				"delivery_date": "2026-08-17",
				"topic": "Leadership styles",
				"is_last_call": 1,
			},
		)

		note = frappe.get_last_doc(
			NOTE_DOCTYPE,
			filters={"reference_doctype": DEAL_DOCTYPE, "reference_docname": deal.name},
		)
		self.assertEqual(note.get(FIELD_COACHING_CALL_STATUS), "Completed")
		# The stale stored total is replaced by the recount, not advanced past it.
		self.assertEqual(deal.total_completed_calls, 1)

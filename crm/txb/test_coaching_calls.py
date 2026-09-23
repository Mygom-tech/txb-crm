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

	def setUp(self):
		# Both note metadata columns, so the note lifecycle is exercised against the schema a
		# migrated site actually has rather than skipping around a half-installed one.
		from crm.patches.v1_0.reconcile_coaching_call_totals import _install_field as install_status
		from crm.patches.v1_0.seed_first_coaching_call_date import _install_field as install_date

		install_status()
		install_date()

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


class TestFirstCoachingCallDate(FrappeTestCase):
	"""The first canonical Coaching Call Note seeds an empty First Coaching Call Date, once (TXB-224)."""

	FIRST = "custom_first_call_date"

	def setUp(self):
		# Both metadata prerequisites, not just the Delivery Date: seeding also classifies a note
		# by its call status, and a half-installed schema is exactly the skew TXB-261 survives.
		from crm.patches.v1_0.reconcile_coaching_call_totals import _install_field as install_status
		from crm.patches.v1_0.seed_first_coaching_call_date import _install_field as install_date

		install_status()
		install_date()

	def tearDown(self):
		frappe.db.rollback()

	def make_deal(self, pipeline=PIPELINE_DELIVERING_COACHING, **values):
		return frappe.get_doc(
			{"doctype": DEAL_DOCTYPE, "pipeline_type": pipeline, "status": "Active", **values}
		).insert(ignore_permissions=True)

	def make_call(self, deal, delivery_date, status="Completed", title=None):
		from crm.txb.constants import FIELD_COACHING_CALL_DELIVERY_DATE

		return frappe.get_doc(
			{
				"doctype": NOTE_DOCTYPE,
				"reference_doctype": DEAL_DOCTYPE,
				"reference_docname": deal.name,
				"title": title or f"Coaching Call #1 - {delivery_date}",
				"content": f"Date: {delivery_date}<br>Call Status: {status}",
				FIELD_COACHING_CALL_STATUS: status,
				FIELD_COACHING_CALL_DELIVERY_DATE: delivery_date,
			}
		).insert(ignore_permissions=True)

	def stored_first(self, deal):
		return frappe.db.get_value(DEAL_DOCTYPE, deal.name, self.FIRST)

	def stored_last(self, deal):
		return frappe.db.get_value(DEAL_DOCTYPE, deal.name, "custom_last_coaching_call_date")

	def test_title_dates_are_read_strictly(self):
		from datetime import date

		from crm.txb.coaching_calls import title_delivery_date

		self.assertEqual(title_delivery_date("Coaching Call #3 - 2026-02-14 - Goals"), date(2026, 2, 14))
		self.assertIsNone(title_delivery_date("Coaching Call #3"))
		self.assertIsNone(title_delivery_date("Coaching Call - 2026-02-14 - moved 2026-02-20"))
		self.assertIsNone(title_delivery_date("Coaching Call - 2026-02-30"))

	def test_the_first_call_seeds_an_empty_date_at_local_midnight(self):
		deal = self.make_deal()
		self.make_call(deal, "2026-08-17")
		self.assertEqual(str(self.stored_first(deal)), "2026-08-17 00:00:00")

	def test_a_rolled_back_insert_leaves_the_deal_unchanged(self):
		deal = self.make_deal()
		frappe.db.savepoint("txb224")
		self.make_call(deal, "2026-08-17")
		frappe.db.rollback(save_point="txb224")
		self.assertIsNone(self.stored_first(deal))

	def test_a_note_without_canonical_metadata_seeds_nothing(self):
		deal = self.make_deal()
		frappe.get_doc(
			{
				"doctype": NOTE_DOCTYPE,
				"reference_doctype": DEAL_DOCTYPE,
				"reference_docname": deal.name,
				"title": "Coaching Call #1 - 2026-08-17",
				"content": "Call Status: Completed",
			}
		).insert(ignore_permissions=True)
		self.assertIsNone(self.stored_first(deal))

	def test_a_manual_date_is_never_overwritten(self):
		deal = self.make_deal(**{self.FIRST: "2026-07-01 09:00:00"})
		self.make_call(deal, "2026-08-17")
		self.assertEqual(str(self.stored_first(deal)), "2026-07-01 09:00:00")

	def test_a_cleared_date_is_not_repopulated_by_later_calls(self):
		deal = self.make_deal()
		first = self.make_call(deal, "2026-08-17")
		frappe.db.set_value(DEAL_DOCTYPE, deal.name, self.FIRST, None)

		self.make_call(deal, "2026-08-10", title="Coaching Call #2 - 2026-08-10")
		first.content = "Date: 2026-08-17<br>Call Status: Missed"
		first.save(ignore_permissions=True)
		self.assertIsNone(self.stored_first(deal))

	def test_edits_deletes_and_moves_do_not_resynchronize(self):
		deal = self.make_deal()
		other = self.make_deal()
		note = self.make_call(deal, "2026-08-17")
		note.reference_docname = other.name
		note.save(ignore_permissions=True)
		self.assertEqual(str(self.stored_first(deal)), "2026-08-17 00:00:00")
		self.assertIsNone(self.stored_first(other))

		note.delete(ignore_permissions=True)
		self.assertEqual(str(self.stored_first(deal)), "2026-08-17 00:00:00")

	def test_another_pipeline_is_left_alone(self):
		deal = self.make_deal(pipeline=PIPELINE_WORKSHOP)
		self.make_call(deal, "2026-08-17")
		self.assertIsNone(self.stored_first(deal))

	def test_log_coaching_call_keeps_the_seed_on_the_in_memory_deal(self):
		from crm.txb.pipelines.delivering_coaching import log_coaching_call

		deal = self.make_deal()
		log_coaching_call(deal, {"call_status": "Completed", "delivery_date": "2026-08-17"})
		deal.save(ignore_permissions=True)
		deal.reload()
		self.assertEqual(str(deal.get(self.FIRST)), "2026-08-17 00:00:00")

	def test_the_migration_backfills_the_earliest_date_only_where_empty(self):
		from crm.patches.v1_0 import seed_first_coaching_call_date as patch

		empty = self.make_deal()
		manual = self.make_deal(**{self.FIRST: "2026-01-05 10:00:00"})
		workshop = self.make_deal(pipeline=PIPELINE_WORKSHOP)
		for deal in (empty, manual, workshop):
			for title in ("Coaching Call #119 - 2026-03-09", "Coaching Call #118 - 2026-03-02"):
				frappe.get_doc(
					{
						"doctype": NOTE_DOCTYPE,
						"reference_doctype": DEAL_DOCTYPE,
						"reference_docname": deal.name,
						"title": title,
						"content": "Call Status: Completed",
					}
				).insert(ignore_permissions=True)
		frappe.db.set_value(DEAL_DOCTYPE, empty.name, self.FIRST, None, update_modified=False)
		modified = frappe.db.get_value(DEAL_DOCTYPE, empty.name, "modified")

		patch.execute()
		patch.execute()

		self.assertEqual(str(self.stored_first(empty)), "2026-03-02 00:00:00")
		self.assertEqual(frappe.db.get_value(DEAL_DOCTYPE, empty.name, "modified"), modified)
		self.assertEqual(str(self.stored_first(manual)), "2026-01-05 10:00:00")
		self.assertIsNone(self.stored_first(workshop))

	# TXB-261: what a coach actually sees and does -- the official action, the canonical field
	# on a reloaded Opportunity, and the layout that renders it.

	def log_call(self, deal, **data):
		"""Run the real Log Coaching Call action and persist the deal exactly as the API does."""
		from crm.txb.pipelines.delivering_coaching import log_coaching_call

		log_coaching_call(deal, {"call_status": "Completed", **data})
		deal.save(ignore_permissions=True)
		deal.reload()
		return deal

	def test_the_official_action_persists_the_date_in_the_canonical_field(self):
		deal = self.log_call(self.make_deal(), delivery_date="2026-08-17", is_last_call=1)

		# Read back from the database, not from the handler's in-memory deal: the canonical
		# field is what the reloaded Opportunity renders, on desktop and on mobile.
		self.assertEqual(str(self.stored_first(deal)), "2026-08-17 00:00:00")
		self.assertEqual(str(deal.get(self.FIRST)), "2026-08-17 00:00:00")
		# Last Coaching Call Date is a manual field: the first call leaves it empty (TXB-262).
		self.assertIsNone(deal.custom_last_coaching_call_date)
		self.assertIsNone(self.stored_last(deal))

	def test_the_official_action_seeds_while_note_metadata_is_missing(self):
		from unittest.mock import patch

		# Mid-migration skew: the note's Delivery Date column is not installed yet, so the
		# document-event path stands down. The action must still seed rather than no-op.
		with patch("crm.txb.coaching_calls.delivery_date_field_installed", return_value=False):
			deal = self.log_call(self.make_deal(), delivery_date="2026-08-17", is_last_call=1)

		self.assertEqual(str(self.stored_first(deal)), "2026-08-17 00:00:00")

	def test_later_calls_move_neither_date(self):
		deal = self.log_call(self.make_deal(), delivery_date="2026-08-17", next_call_date="2026-08-24")
		self.log_call(deal, delivery_date="2026-08-24", is_last_call=1)

		self.assertEqual(str(self.stored_first(deal)), "2026-08-17 00:00:00")
		self.assertIsNone(self.stored_last(deal))

	def test_the_action_never_touches_a_populated_last_date(self):
		# A Last date someone typed in by hand survives the first call and every later one,
		# byte for byte, whatever Delivery Date the coach submits (TXB-262).
		deal = self.make_deal(custom_last_coaching_call_date="2026-01-05 10:00:00")
		before = self.stored_last(deal)

		self.log_call(deal, delivery_date="2026-08-17", next_call_date="2026-08-24")
		self.assertEqual(self.stored_last(deal), before)

		self.log_call(deal, delivery_date="2026-08-24", is_last_call=1)
		self.assertEqual(self.stored_last(deal), before)
		self.assertEqual(deal.custom_last_coaching_call_date, before)
		# The last-call flag is a separate field and still answers the coach's tick.
		self.assertEqual(deal.custom_last_coaching_call, "Yes")
		self.assertEqual(str(self.stored_first(deal)), "2026-08-17 00:00:00")

	def test_a_manually_cleared_first_date_is_not_repopulated_by_the_action(self):
		deal = self.log_call(self.make_deal(), delivery_date="2026-08-17", next_call_date="2026-08-24")

		deal.set(self.FIRST, None)
		deal.save(ignore_permissions=True)

		self.log_call(deal, delivery_date="2026-08-24", is_last_call=1)
		self.assertIsNone(self.stored_first(deal))
		self.assertIsNone(self.stored_last(deal))

	def test_a_rejected_call_changes_neither_date(self):
		from crm.txb.pipelines.delivering_coaching import validate_log_coaching_call

		deal = self.make_deal()
		# The date is mandatory until "this is the last call" is ticked; a submission that
		# fails validation never reaches the handler, so nothing is written at all.
		with self.assertRaises(frappe.MandatoryError):
			validate_log_coaching_call(deal, {"call_status": "Completed", "delivery_date": "2026-08-17"})

		deal.reload()
		self.assertIsNone(self.stored_first(deal))
		self.assertIsNone(self.stored_last(deal))

	def test_the_migration_renders_the_canonical_field_in_the_deal_layout(self):
		import json

		from crm.patches.v1_0 import repair_first_coaching_call_date_field as patch

		layout = self.deal_layout()
		original = layout.layout
		self.addCleanup(self.restore_layout, layout.name, original)

		layout.layout = json.dumps(
			[
				{
					"label": "Delivery Sheet",
					"columns": [
						{"fields": ["custom_delivery_coach", patch.LEGACY_FIRST_FIELD, "custom_last_coaching_call_date"]}
					],
				}
			]
		)
		layout.save()

		patch.execute()
		patch.execute()

		fields = json.loads(frappe.db.get_value("CRM Fields Layout", layout.name, "layout"))[0][
			"columns"
		][0]["fields"]
		# Replaced in place: the canonical field holds the legacy field's exact position, and
		# the fields around it -- including Last Coaching Call Date -- are untouched.
		self.assertEqual(
			fields, ["custom_delivery_coach", self.FIRST, "custom_last_coaching_call_date"]
		)

	def test_the_migration_copies_a_legacy_date_only_into_an_empty_canonical_field(self):
		from crm.patches.v1_0 import repair_first_coaching_call_date_field as patch

		legacy = patch.LEGACY_FIRST_FIELD
		if not frappe.get_meta(DEAL_DOCTYPE).has_field(legacy):
			self.skipTest(f"{legacy} is not installed on this site")

		empty = self.make_deal(**{legacy: "2026-02-03 00:00:00"})
		manual = self.make_deal(**{legacy: "2026-02-03 00:00:00", self.FIRST: "2026-01-05 10:00:00"})

		patch.execute()
		patch.execute()

		self.assertEqual(str(self.stored_first(empty)), "2026-02-03 00:00:00")
		# A canonical value already there -- seeded or typed by a coach -- always wins.
		self.assertEqual(str(self.stored_first(manual)), "2026-01-05 10:00:00")
		# The legacy field itself stays installed and populated.
		self.assertIsNotNone(frappe.db.get_value(DEAL_DOCTYPE, empty.name, legacy))

	def deal_layout(self):
		"""The stored Opportunity Data Fields layout, created on a site that has none."""
		name = "CRM Deal-Data Fields"
		if frappe.db.exists("CRM Fields Layout", name):
			return frappe.get_doc("CRM Fields Layout", name)
		return frappe.get_doc(
			{"doctype": "CRM Fields Layout", "dt": DEAL_DOCTYPE, "type": "Data Fields", "layout": "[]"}
		).insert(ignore_permissions=True)

	def restore_layout(self, name, layout):
		if frappe.db.exists("CRM Fields Layout", name):
			frappe.db.set_value("CRM Fields Layout", name, "layout", layout, update_modified=False)

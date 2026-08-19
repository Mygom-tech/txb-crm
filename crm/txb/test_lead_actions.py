"""Regression coverage for guarded Lead action endpoints."""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.txb.lead_actions import (
	dial_results,
	log_a_dial,
	require_dial_notes,
	require_dial_result,
)


class TestDialResults(FrappeTestCase):
	def test_only_final_manual_dial_results_are_allowed(self):
		self.assertEqual(
			dial_results(),
			("Completed", "Failed", "Busy", "No Answer", "Canceled"),
		)

	def test_selected_result_is_preserved(self):
		self.assertEqual(require_dial_result("Busy"), "Busy")

	def test_missing_or_unsupported_result_is_rejected(self):
		for result in (None, "", "Ringing", "Answered"):
			with self.assertRaises(frappe.ValidationError):
				require_dial_result(result)

	def test_notes_are_required_before_any_artifact_is_created(self):
		for notes in (None, "", "   "):
			with self.assertRaises(frappe.ValidationError):
				require_dial_notes(notes)
		self.assertEqual(require_dial_notes("  Call back tomorrow  "), "Call back tomorrow")

		with (
			patch.object(frappe, "has_permission"),
			patch.object(frappe, "get_doc") as get_doc,
			patch.object(frappe, "new_doc") as new_doc,
		):
			with self.assertRaises(frappe.ValidationError):
				log_a_dial(
					"CRM-LEAD-0001",
					dialed_at="2026-08-19 10:00:00",
					dial_result="Busy",
					notes="   ",
				)
		get_doc.assert_not_called()
		new_doc.assert_not_called()

	def test_log_a_dial_persists_the_selected_result_and_moves_the_lead(self):
		lead = MagicMock()
		lead.name = "CRM-LEAD-0001"
		lead.mobile_no = "+37060000000"
		lead.phone = None
		lead.status = "New"
		call_log = MagicMock()
		call_log.name = "CRM-CALL-LOG-0001"
		note = MagicMock()
		note.name = "FCRM-NOTE-0001"

		with (
			patch.object(frappe, "has_permission"),
			patch.object(frappe, "get_doc", return_value=lead),
			patch.object(frappe, "new_doc", side_effect=[call_log, note]),
			patch.object(frappe.db, "get_value", return_value=None),
			patch.object(frappe, "generate_hash", return_value="fixedhash"),
		):
			result = log_a_dial(
				lead.name,
				dialed_at="2026-08-19 10:00:00",
				dial_result="Busy",
				notes="Customer asked to call tomorrow.",
			)

		inserted = call_log.update.call_args.args[0]
		note_values = note.update.call_args.args[0]
		self.assertEqual(inserted["status"], "Busy")
		self.assertEqual(note_values["reference_doctype"], "CRM Lead")
		self.assertEqual(note_values["reference_docname"], lead.name)
		self.assertIn("2026-08-19 10:00:00", note_values["content"])
		self.assertIn("Busy", note_values["content"])
		self.assertIn("Customer asked to call tomorrow.", note_values["content"])
		note.insert.assert_called_once_with(ignore_permissions=True)
		self.assertEqual(call_log.note, note.name)
		self.assertEqual(lead.status, "Contact attempted")
		call_log.insert.assert_called_once_with(ignore_permissions=True)
		lead.save.assert_called_once_with()
		self.assertEqual(result["call_log"], call_log.name)
		self.assertEqual(result["note"], note.name)

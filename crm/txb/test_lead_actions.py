"""Regression coverage for guarded Lead action endpoints."""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.txb.lead_actions import dial_results, log_a_dial, require_dial_result


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

	def test_log_a_dial_persists_the_selected_result_and_moves_the_lead(self):
		lead = MagicMock()
		lead.name = "CRM-LEAD-0001"
		lead.mobile_no = "+37060000000"
		lead.phone = None
		lead.status = "New"
		call_log = MagicMock()
		call_log.name = "CRM-CALL-LOG-0001"

		with (
			patch.object(frappe, "has_permission"),
			patch.object(frappe, "get_doc", return_value=lead),
			patch.object(frappe, "new_doc", return_value=call_log),
			patch.object(frappe.db, "get_value", return_value=None),
			patch.object(frappe, "generate_hash", return_value="fixedhash"),
		):
			result = log_a_dial(
				lead.name,
				dialed_at="2026-08-19 10:00:00",
				dial_result="Busy",
			)

		inserted = call_log.update.call_args.args[0]
		self.assertEqual(inserted["status"], "Busy")
		self.assertEqual(lead.status, "Contact attempted")
		call_log.insert.assert_called_once_with(ignore_permissions=True)
		lead.save.assert_called_once_with()
		self.assertEqual(result["call_log"], call_log.name)
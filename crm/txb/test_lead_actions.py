"""Regression coverage for guarded Lead action endpoints."""

from unittest.mock import MagicMock, patch

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.tests.utils import FrappeTestCase

from crm.txb.call_log_status import (
	STATUS_OPTIONS_PROPERTY_SETTER,
	reconcile_call_log_status_options,
)
from crm.txb.constants import CALL_LOG_STATUS_OPTIONS, DIAL_RESULTS
from crm.txb.lead_actions import (
	dial_results,
	log_a_dial,
	require_dial_notes,
	require_dial_result,
)

# The foreign option set a runtime Property Setter had drifted CRM Call Log `status` onto: none of
# these are approved dial results, and it omits "No Answer", so Frappe's Select validation refused
# the canonical dial result before the Lead could move. Used to reproduce the regression.
DRIFTED_STATUS_OPTIONS = "Completed\nMissed\nNo Charge"


def _make_drift_property_setter():
	"""Recreate the runtime override that rejects the approved "No Answer" dial result."""
	make_property_setter(
		"CRM Call Log",
		"status",
		"options",
		DRIFTED_STATUS_OPTIONS,
		"Text",
		validate_fields_for_doctype=False,
	)


def _delete_status_property_setter():
	if frappe.db.exists("Property Setter", STATUS_OPTIONS_PROPERTY_SETTER):
		frappe.delete_doc(
			"Property Setter",
			STATUS_OPTIONS_PROPERTY_SETTER,
			force=True,
			ignore_permissions=True,
		)
		frappe.clear_cache(doctype="CRM Call Log")


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


class TestCallLogStatusReconciliation(FrappeTestCase):
	"""The runtime CRM Call Log `status` options must accept every approved dial outcome.

	These exercise real DocType metadata: a drifted Property Setter is created, then the
	reconciliation is run, and the effective `frappe.get_meta` options are asserted -- so the fix is
	proven against the same validation path `log_a_dial` relies on, not a stand-in.
	"""

	def tearDown(self):
		_delete_status_property_setter()

	def _effective_status_options(self):
		return frappe.get_meta("CRM Call Log").get_field("status").options

	def test_drifted_override_is_removed_and_canonical_options_restored(self):
		_make_drift_property_setter()
		self.assertTrue(frappe.db.exists("Property Setter", STATUS_OPTIONS_PROPERTY_SETTER))
		# The drift is genuinely in effect: the approved dial results are unavailable.
		self.assertNotIn("No Answer", self._effective_status_options())

		reconcile_call_log_status_options()

		self.assertFalse(frappe.db.exists("Property Setter", STATUS_OPTIONS_PROPERTY_SETTER))
		options = self._effective_status_options()
		self.assertIn("No Answer", options)
		for result in DIAL_RESULTS:
			self.assertIn(result, options, result)

	def test_reconcile_is_a_no_op_without_an_override(self):
		_delete_status_property_setter()
		# No Property Setter to remove: the call must return quietly and leave options canonical.
		reconcile_call_log_status_options()
		self.assertIn("No Answer", self._effective_status_options())

	def test_reconcile_reruns_cleanly_after_repair(self):
		_make_drift_property_setter()
		reconcile_call_log_status_options()
		# A second pass has nothing left to remove and must keep the options correct.
		reconcile_call_log_status_options()
		self.assertFalse(frappe.db.exists("Property Setter", STATUS_OPTIONS_PROPERTY_SETTER))
		self.assertIn("No Answer", self._effective_status_options())

	def test_a_canonical_override_is_left_untouched(self):
		make_property_setter(
			"CRM Call Log",
			"status",
			"options",
			"\n".join(CALL_LOG_STATUS_OPTIONS),
			"Text",
			validate_fields_for_doctype=False,
		)
		reconcile_call_log_status_options()
		# It already agrees with the committed schema, so there is nothing to reconcile.
		self.assertTrue(frappe.db.exists("Property Setter", STATUS_OPTIONS_PROPERTY_SETTER))
		self.assertIn("No Answer", self._effective_status_options())


class TestLogADialWithRealDocuments(FrappeTestCase):
	"""End-to-end coverage against real CRM Lead and CRM Call Log documents.

	Unlike the mocked cases above, these insert and validate genuine documents so the atomic move
	is proven through Frappe's own Select validation and transaction, and a failure is proven to
	leave no Call Log, note, or moved Lead behind.
	"""

	def setUp(self):
		for status in ("New", "Contact attempted"):
			if not frappe.db.exists("CRM Lead Status", status):
				frappe.get_doc(
					{
						"doctype": "CRM Lead Status",
						"lead_status": status,
						"color": "gray",
						"type": "Ongoing",
						"position": 99,
					}
				).insert(ignore_permissions=True)

	def tearDown(self):
		_delete_status_property_setter()

	def _make_lead(self):
		return frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": "Dial", "status": "New"}
		).insert(ignore_permissions=True)

	def _linked_call_logs(self, lead):
		return frappe.get_all(
			"CRM Call Log",
			filters={"reference_doctype": "CRM Lead", "reference_docname": lead.name},
		)

	def _linked_notes(self, lead):
		return frappe.get_all(
			"FCRM Note",
			filters={"reference_doctype": "CRM Lead", "reference_docname": lead.name},
		)

	def test_no_answer_creates_one_call_log_and_moves_the_lead(self):
		# The effective metadata must accept every approved outcome first.
		reconcile_call_log_status_options()
		lead = self._make_lead()

		result = log_a_dial(
			lead.name,
			dialed_at="2026-08-19 10:00:00",
			dial_result="No Answer",
			notes="No answer, will retry tomorrow.",
		)

		logs = self._linked_call_logs(lead)
		self.assertEqual(len(logs), 1)
		call = frappe.get_doc("CRM Call Log", result["call_log"])
		self.assertEqual(call.status, "No Answer")
		self.assertEqual(call.type, "Outgoing")
		self.assertEqual(call.reference_docname, lead.name)
		self.assertEqual(
			frappe.db.get_value("CRM Lead", lead.name, "status"), "Contact attempted"
		)
		self.assertTrue(frappe.db.exists("FCRM Note", result["note"]))

	def test_metadata_rejection_rolls_back_every_artifact(self):
		# Reproduce the live drift so the real Select validation refuses "No Answer".
		_make_drift_property_setter()
		lead = self._make_lead()

		frappe.db.savepoint("before_dial")
		with self.assertRaises(frappe.ValidationError):
			log_a_dial(
				lead.name,
				dialed_at="2026-08-19 10:00:00",
				dial_result="No Answer",
				notes="No answer, will retry tomorrow.",
			)
		# Mirror the request boundary's rollback: the whole attempt is one transaction.
		frappe.db.rollback(save_point="before_dial")

		self.assertEqual(frappe.db.get_value("CRM Lead", lead.name, "status"), "New")
		self.assertEqual(self._linked_call_logs(lead), [])
		self.assertEqual(self._linked_notes(lead), [])

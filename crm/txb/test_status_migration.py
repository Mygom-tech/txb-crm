"""TXB-210: the legacy-status migration folds Qualified/No Answer without losing history.

The patch commits; each test mocks `frappe.db.commit` to a no-op so FrappeTestCase's automatic
rollback still cleans up the rows the test created.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.patches.v1_0.migrate_qualified_and_no_answer_lead_statuses import execute

LEGACY = {"Qualified": "Contacted", "No Answer": "Contact attempted"}


class TestMigrateQualifiedAndNoAnswerLeadStatuses(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def _ensure_status(self, name, status_type="Ongoing", position=99):
		if not frappe.db.exists("CRM Lead Status", name):
			frappe.get_doc(
				{
					"doctype": "CRM Lead Status",
					"lead_status": name,
					"color": "gray",
					"type": status_type,
					"position": position,
				}
			).insert(ignore_permissions=True)

	def _make_lead(self, status):
		return frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": "Legacy", "status": status}
		).insert(ignore_permissions=True)

	def _insert_status_log(self, from_status, to_status):
		"""Insert a raw status-history row, the shape the patch rewrites with direct SQL."""
		name = frappe.generate_hash(length=10)
		frappe.db.sql(
			"""
			INSERT INTO `tabCRM Status Change Log`
			(name, `from`, `to`, parent, parentfield, parenttype,
			 creation, modified, owner, modified_by, docstatus, idx)
			VALUES (%s, %s, %s, '', 'status_change_log', 'CRM Lead',
			 NOW(), NOW(), 'Administrator', 'Administrator', 0, 1)
			""",
			(name, from_status, to_status),
		)
		return name

	def _run(self):
		with patch.object(frappe.db, "commit"):
			execute()

	def test_it_folds_both_legacy_statuses_and_retires_them(self):
		for legacy in LEGACY:
			self._ensure_status(legacy)
		# Canonical targets are seeded by install.py; ensure them for a bare test site too.
		for canonical in LEGACY.values():
			self._ensure_status(canonical)

		qualified_lead = self._make_lead("Qualified")
		no_answer_lead = self._make_lead("No Answer")
		log_from = self._insert_status_log("Qualified", "New")
		log_to = self._insert_status_log("New", "No Answer")

		self._run()

		# Leads are repointed onto their canonical targets, not lost.
		self.assertEqual(
			frappe.db.get_value("CRM Lead", qualified_lead.name, "status"), "Contacted"
		)
		self.assertEqual(
			frappe.db.get_value("CRM Lead", no_answer_lead.name, "status"), "Contact attempted"
		)

		# Both sides of the status history are rewritten so the timeline still reads consistently.
		self.assertEqual(
			frappe.db.get_value("CRM Status Change Log", log_from, "from"), "Contacted"
		)
		self.assertEqual(
			frappe.db.get_value("CRM Status Change Log", log_to, "to"), "Contact attempted"
		)

		# The obsolete rows are retired; the canonical statuses remain.
		self.assertFalse(frappe.db.exists("CRM Lead Status", "Qualified"))
		self.assertFalse(frappe.db.exists("CRM Lead Status", "No Answer"))
		self.assertTrue(frappe.db.exists("CRM Lead Status", "Contacted"))
		self.assertTrue(frappe.db.exists("CRM Lead Status", "Contact attempted"))

	def test_the_independent_no_answer_call_log_result_is_untouched(self):
		"""The patch never writes CRM Call Log, so the No Answer manual-call result stays valid."""
		from crm.txb.constants import DIAL_RESULTS

		for legacy in LEGACY:
			self._ensure_status(legacy)
		self._run()

		self.assertIn("No Answer", DIAL_RESULTS)
		# "No Answer" is a CRM Call Log status option, not a CRM Lead Status row -- retiring the
		# Lead status cannot remove it.
		call_log_statuses = frappe.get_meta("CRM Call Log").get_field("status").options
		self.assertIn("No Answer", call_log_statuses)

	def test_rerun_is_a_no_op(self):
		for legacy in LEGACY:
			if frappe.db.exists("CRM Lead Status", legacy):
				frappe.delete_doc(
					"CRM Lead Status",
					legacy,
					force=True,
					delete_permanently=True,
					ignore_permissions=True,
				)

		# Both legacy statuses already gone: execute() must return without error or effect.
		self._run()

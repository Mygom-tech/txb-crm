# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-106: asking an Admin for a record, without taking it.

A Claim Request raises exactly one CRM Task and changes nothing else. These tests pin the
two properties that make it safe -- the owner is untouched, and a requester cannot spam
the approver with duplicates for the same record.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.txb.api.ownership import request_claim
from crm.txb.constants import ADMIN_ROLE

SALESMAN = "txb-claim-sales@example.com"
OTHER_SALESMAN = "txb-claim-sales2@example.com"
ADMIN = "txb-claim-admin@example.com"
APPROVER = "txb-claim-approver@example.com"


def ensure_user(email: str, roles: list[str]):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)

	user = frappe.get_doc("User", email)
	user.add_roles(*roles)
	return user


class TestClaimRequest(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Sales Manager, not just Sales User: a claim requester must be able to READ the record
		# they are asking for -- has_deal_permission scopes reads to owned or assigned records,
		# and the whole premise of a claim is that you can see a deal you do not own.
		ensure_user(SALESMAN, ["Sales User", "Sales Manager"])
		ensure_user(OTHER_SALESMAN, ["Sales User", "Sales Manager"])
		ensure_user(ADMIN, ["Sales User", ADMIN_ROLE])
		ensure_user(APPROVER, ["Sales User", ADMIN_ROLE])
		frappe.db.commit()  # nosemgrep -- roles must outlive per-test rollback

	def setUp(self):
		frappe.db.set_single_value("FCRM Settings", "custom_claim_approver", APPROVER)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def make_deal(self, **kwargs):
		return frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"pipeline_type": "Individual Session",
				"status": "Submitted",
				"deal_owner": OTHER_SALESMAN,
				**kwargs,
			}
		).insert(ignore_permissions=True)

	def claim(self, deal, requested_owner=SALESMAN, reason="I ran the discovery call"):
		return request_claim(
			doctype="CRM Deal",
			name=deal.name,
			requested_owner=requested_owner,
			reason=reason,
		)

	def test_a_request_creates_one_task_for_the_approver(self):
		deal = self.make_deal()

		frappe.set_user(SALESMAN)
		result = self.claim(deal)

		self.assertTrue(result["created"])
		task = frappe.get_doc("CRM Task", result["task"])
		self.assertEqual(task.assigned_to, APPROVER)
		self.assertEqual(task.reference_doctype, "CRM Deal")
		self.assertEqual(task.reference_docname, deal.name)
		self.assertEqual(task.custom_claim_requested_by, SALESMAN)
		self.assertEqual(task.custom_claim_requested_owner, SALESMAN)

	def test_the_task_carries_the_context_an_admin_needs(self):
		deal = self.make_deal()

		frappe.set_user(SALESMAN)
		task = frappe.get_doc("CRM Task", self.claim(deal)["task"])

		for expected in (SALESMAN, deal.name, "Individual Session", "Submitted", "I ran the discovery call"):
			self.assertIn(expected, task.description, expected)

	def test_an_unassigned_record_says_so(self):
		deal = self.make_deal(deal_owner="")

		frappe.set_user(SALESMAN)
		task = frappe.get_doc("CRM Task", self.claim(deal)["task"])

		self.assertIn("Unassigned", task.description)

	def test_the_owner_is_not_changed(self):
		deal = self.make_deal()

		frappe.set_user(SALESMAN)
		self.claim(deal)

		self.assertEqual(
			frappe.db.get_value("CRM Deal", deal.name, "deal_owner"), OTHER_SALESMAN
		)

	def test_a_second_request_from_the_same_person_returns_the_open_one(self):
		deal = self.make_deal()

		frappe.set_user(SALESMAN)
		first = self.claim(deal)
		second = self.claim(deal)

		self.assertFalse(second["created"])
		self.assertEqual(second["task"], first["task"])

	def test_a_different_requester_gets_their_own_task(self):
		deal = self.make_deal()

		frappe.set_user(SALESMAN)
		first = self.claim(deal)

		frappe.set_user(OTHER_SALESMAN)
		second = self.claim(deal, requested_owner=OTHER_SALESMAN)

		self.assertTrue(second["created"])
		self.assertNotEqual(second["task"], first["task"])

	def test_a_closed_request_does_not_block_a_new_one(self):
		deal = self.make_deal()

		frappe.set_user(SALESMAN)
		first = self.claim(deal)

		frappe.set_user("Administrator")
		frappe.db.set_value("CRM Task", first["task"], "status", "Done")

		frappe.set_user(SALESMAN)
		second = self.claim(deal)

		self.assertTrue(second["created"])
		self.assertNotEqual(second["task"], first["task"])

	def test_an_admin_is_refused(self):
		deal = self.make_deal()

		frappe.set_user(ADMIN)
		with self.assertRaises(frappe.ValidationError):
			self.claim(deal)

	def test_an_empty_reason_is_refused(self):
		deal = self.make_deal()

		frappe.set_user(SALESMAN)
		with self.assertRaises(frappe.ValidationError):
			self.claim(deal, reason="   ")

	def test_an_unsupported_doctype_is_refused(self):
		frappe.set_user(SALESMAN)
		with self.assertRaises(frappe.ValidationError):
			request_claim(
				doctype="CRM Organization",
				name="whatever",
				requested_owner=SALESMAN,
				reason="because",
			)

	def test_a_blank_setting_falls_back_to_an_admin(self):
		frappe.db.set_single_value("FCRM Settings", "custom_claim_approver", "")
		deal = self.make_deal()

		frappe.set_user(SALESMAN)
		task = frappe.get_doc("CRM Task", self.claim(deal)["task"])

		self.assertIn(ADMIN_ROLE, frappe.get_roles(task.assigned_to))

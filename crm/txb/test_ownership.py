# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-106: who owns a record, and who may change that.

Ownership decides commission, so it is enforced on the document lifecycle rather than in
any one screen -- `before_insert` decides the initial owner, `validate` refuses later
changes. These tests exercise the hooks directly, since the point is that every write path
reaches them.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.txb.constants import ADMIN_ROLE

SALESMAN = "txb-owner-sales@example.com"
OTHER_SALESMAN = "txb-owner-sales2@example.com"
ADMIN = "txb-owner-admin@example.com"


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


class OwnershipTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(SALESMAN, ["Sales User"])
		ensure_user(OTHER_SALESMAN, ["Sales User"])
		ensure_user(ADMIN, ["Sales User", ADMIN_ROLE])
		frappe.db.commit()  # nosemgrep -- roles must outlive per-test rollback

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def make_lead(self, **kwargs):
		# email and last_name are both reqd on this site via Property Setter, so the
		# fixture supplies them; either missing fails in _validate_mandatory before the
		# ownership hook is reached.
		return frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Owner",
				"last_name": "Test",
				"email": "txb-owner-lead@example.com",
				**kwargs,
			}
		).insert(ignore_permissions=True)

	def make_deal(self, **kwargs):
		# pipeline_type is always explicit: the Select has no blank option and no default,
		# so Frappe's _set_defaults would otherwise silently fill "Individual Session".
		return frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"pipeline_type": "Individual Session",
				"status": "Submitted",
				**kwargs,
			}
		).insert(ignore_permissions=True)

	def make_contact(self, **kwargs):
		return frappe.get_doc(
			{"doctype": "Contact", "first_name": "Owner", "last_name": "Test", **kwargs}
		).insert(ignore_permissions=True)


class TestOwnerOnInsert(OwnershipTestCase):
	def test_lead_creator_becomes_owner(self):
		frappe.set_user(SALESMAN)
		self.assertEqual(self.make_lead().lead_owner, SALESMAN)

	def test_deal_creator_becomes_owner(self):
		frappe.set_user(SALESMAN)
		self.assertEqual(self.make_deal().deal_owner, SALESMAN)

	def test_contact_creator_becomes_owner(self):
		frappe.set_user(SALESMAN)
		self.assertEqual(self.make_contact().custom_contact_owner, SALESMAN)

	def test_a_non_admin_cannot_nominate_someone_else_at_creation(self):
		"""The ticket is explicit: the creator owns it, whatever the client sent."""
		frappe.set_user(SALESMAN)
		deal = self.make_deal(deal_owner=OTHER_SALESMAN)
		self.assertEqual(deal.deal_owner, SALESMAN)

	def test_an_admin_may_nominate_someone_else_at_creation(self):
		frappe.set_user(ADMIN)
		deal = self.make_deal(deal_owner=SALESMAN)
		self.assertEqual(deal.deal_owner, SALESMAN)

	def test_an_admin_who_nominates_nobody_owns_it(self):
		frappe.set_user(ADMIN)
		self.assertEqual(self.make_deal().deal_owner, ADMIN)

	def test_guest_never_becomes_an_owner(self):
		"""The public registration endpoint runs as Guest and sets deal_owner itself,
		carrying it over from the source deal. Overwriting it with "Guest" would be wrong."""
		frappe.set_user("Guest")
		try:
			deal = self.make_deal(deal_owner=SALESMAN)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(deal.deal_owner, SALESMAN)

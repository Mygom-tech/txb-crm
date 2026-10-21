# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""Global search over Leads, Contacts, Companies and Deals."""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.txb.api.global_search import ROWS_PER_GROUP, search

# A surname no real record shares, so the assertions see only this test's rows.
SURNAME = "Zqxrimkute"


def groups(query):
	return {group["doctype"]: group for group in search(query)["groups"]}


class TestGlobalSearch(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def lead(self, first="Simona", **fields):
		return frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": first,
				"last_name": SURNAME,
				"email": f"{first.lower()}.{frappe.generate_hash(length=5)}@example.com",
				"status": "New",
				**fields,
			}
		).insert(ignore_permissions=True)

	def contact(self, first="Simona", phone=None):
		doc = frappe.new_doc("Contact")
		doc.first_name, doc.last_name = first, SURNAME
		if phone:
			doc.append("phone_nos", {"phone": phone, "is_primary_mobile_no": 1})
		return doc.insert(ignore_permissions=True)

	def organization(self, name, **fields):
		# TxB requires a unique company code on every company.
		code = {"custom_company_code": frappe.generate_hash(length=9)}
		return frappe.get_doc(
			{"doctype": "CRM Organization", "organization_name": name, **code, **fields}
		).insert(ignore_permissions=True)

	def deal(self, first="Simona"):
		return frappe.get_doc(
			{"doctype": "CRM Deal", "first_name": first, "last_name": SURNAME, "status": "Qualification"}
		).insert(ignore_permissions=True)

	def test_one_query_finds_every_kind_of_record_grouped_in_order(self):
		lead = self.lead()
		contact = self.contact()
		organization = self.organization(f"{SURNAME} ir partneriai UAB")
		deal = self.deal()

		found = search(SURNAME.lower()[:6])["groups"]
		self.assertEqual(
			[group["doctype"] for group in found], ["CRM Lead", "Contact", "CRM Organization", "CRM Deal"]
		)
		names = {group["doctype"]: [row["name"] for row in group["rows"]] for group in found}
		self.assertIn(lead.name, names["CRM Lead"])
		self.assertIn(contact.name, names["Contact"])
		self.assertIn(organization.name, names["CRM Organization"])
		self.assertIn(deal.name, names["CRM Deal"])
		self.assertEqual(found[0]["rows"][0]["title"], f"Simona {SURNAME}")

	def test_every_word_must_match(self):
		self.lead("Simona")
		self.lead("Agne")

		rows = groups(f"agne {SURNAME}")["CRM Lead"]["rows"]
		self.assertEqual([row["title"] for row in rows], [f"Agne {SURNAME}"])

	def test_email_and_company_code_match(self):
		lead = self.lead(email=f"{SURNAME.lower()}.mail@example.com")
		self.assertEqual(groups(f"{SURNAME.lower()}.mail@")["CRM Lead"]["rows"][0]["name"], lead.name)

		org = self.organization(f"{SURNAME} UAB", custom_company_code="304599871")
		self.assertEqual(groups("304599871")["CRM Organization"]["rows"][0]["name"], org.name)

	def test_a_phone_matches_whatever_its_formatting(self):
		contact = self.contact(phone="+370 699 12 873")

		for typed in ("869912873", "699 12873", "+37069912873"):
			rows = groups(typed)["Contact"]["rows"]
			self.assertEqual([row["name"] for row in rows], [contact.name], typed)

	def test_converted_leads_are_found(self):
		lead = self.lead(converted=1)
		rows = groups(SURNAME)["CRM Lead"]["rows"]
		self.assertEqual([row["name"] for row in rows], [lead.name])

	def test_a_group_shows_its_newest_rows_and_the_full_total(self):
		for index in range(ROWS_PER_GROUP + 2):
			self.lead(f"Lead{index}")

		group = groups(SURNAME)["CRM Lead"]
		self.assertEqual((group["total"], len(group["rows"])), (ROWS_PER_GROUP + 2, ROWS_PER_GROUP))

	def test_too_short_queries_return_nothing(self):
		self.lead()
		for query in ("", " ", "s"):
			self.assertEqual(search(query), {"groups": []}, query)

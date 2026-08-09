# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""Tests for the cross-object people search (TXB-112).

The normalization helpers are pure, so they are exercised without a database. The
endpoint itself is tested against real Lead and Contact rows because the whole point
of the feature is that it reaches records the caller's current View hides.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.txb.api.people_search import search_people
from crm.txb.people import (
	PHONE_SIGNIFICANT_DIGITS,
	find_exact_duplicate,
	normalize_email,
	normalize_name,
	normalize_phone,
)


class TestNormalization(FrappeTestCase):
	def test_name_is_case_and_whitespace_insensitive(self):
		self.assertEqual(normalize_name("  Jonas   Jonaitis "), "jonas jonaitis")
		self.assertEqual(normalize_name("JONAS"), normalize_name("jonas"))

	def test_name_handles_missing_value(self):
		self.assertEqual(normalize_name(None), "")

	def test_email_is_case_insensitive(self):
		self.assertEqual(normalize_email(" Jonas@Example.COM "), "jonas@example.com")

	def test_phone_ignores_formatting(self):
		"""The same number typed four ways has to collapse to one key."""
		expected = normalize_phone("+370 612 34567")
		self.assertEqual(len(expected), PHONE_SIGNIFICANT_DIGITS)
		for spelling in ("+37061234567", "8 612 34567", "(8-612) 34-567", "861234567"):
			self.assertEqual(normalize_phone(spelling), expected, spelling)

	def test_short_phone_fragment_is_ignored(self):
		"""Three digits would match most of the database, so it yields nothing."""
		self.assertEqual(normalize_phone("123"), "")
		self.assertEqual(normalize_phone(None), "")


class TestPeopleSearch(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": "Jonas",
				"last_name": "Jonaitis",
				"email_ids": [{"email_id": "jonas@example.com", "is_primary": 1}],
				"phone_nos": [{"phone": "+370 612 34567", "is_primary_mobile_no": 1}],
			}
		).insert(ignore_permissions=True)

		cls.lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Petras",
				"last_name": "Petraitis",
				"email": "petras@example.com",
				"mobile_no": "+370 611 11111",
				"status": "Disqualified",
			}
		).insert(ignore_permissions=True)

	@classmethod
	def tearDownClass(cls):
		frappe.delete_doc("CRM Lead", cls.lead.name, force=True, ignore_permissions=True)
		frappe.delete_doc("Contact", cls.contact.name, force=True, ignore_permissions=True)
		super().tearDownClass()

	def _names(self, result):
		return {(m["doctype"], m["name"]) for m in result["matches"]}

	def test_lead_flow_finds_an_existing_contact(self):
		"""The reported bug: typing a Contact's name while creating a Lead found nothing."""
		result = search_people(name="Jonas Jonaitis")
		self.assertIn(("Contact", self.contact.name), self._names(result))

	def test_contact_flow_finds_an_existing_lead(self):
		result = search_people(name="Petras")
		self.assertIn(("CRM Lead", self.lead.name), self._names(result))

	def test_disqualified_lead_is_still_returned(self):
		"""A hidden status must not be able to hide a duplicate."""
		result = search_people(email="petras@example.com")
		match = next(m for m in result["matches"] if m["doctype"] == "CRM Lead")
		self.assertEqual(match["status"], "Disqualified")
		self.assertEqual(match["strength"], "exact")

	def test_email_match_is_case_insensitive_and_exact(self):
		result = search_people(email="JONAS@EXAMPLE.COM")
		match = next(m for m in result["matches"] if m["doctype"] == "Contact")
		self.assertEqual(match["strength"], "exact")

	def test_phone_match_ignores_formatting(self):
		"""Stored as '+370 612 34567', typed without spaces — still the same person."""
		result = search_people(phone="861234567")
		self.assertIn(("Contact", self.contact.name), self._names(result))

	def test_name_only_match_is_advisory(self):
		"""A shared first name is a hint, never grounds to block a new person."""
		result = search_people(name="Jonas")
		match = next(m for m in result["matches"] if m["doctype"] == "Contact")
		self.assertEqual(match["strength"], "possible")

	def test_full_name_match_outranks_a_first_name_only_match(self):
		"""`limit` truncates, so a whole-name hit must never sit below every namesake.

		Left unranked this reproduces the original bug one layer up: the person you
		typed exists, the search finds them, and they fall off the end of the list.
		"""
		namesake = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Jonas",
				"last_name": "Kitoks",
				"email": "jonas.kitoks@example.com",
				"status": "Disqualified",
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "CRM Lead", namesake.name, force=True, ignore_permissions=True
		)

		matches = search_people(name="Jonas Jonaitis", limit=20)["matches"]
		scores = {(m["doctype"], m["name"]): m["score"] for m in matches}
		self.assertEqual(scores[("Contact", self.contact.name)], 2)
		self.assertEqual(scores[("CRM Lead", namesake.name)], 1)
		self.assertLess(
			matches.index(next(m for m in matches if m["name"] == self.contact.name)),
			matches.index(next(m for m in matches if m["name"] == namesake.name)),
		)

	def test_short_query_returns_nothing(self):
		self.assertEqual(search_people(name="Jo")["matches"], [])

	def test_empty_query_returns_nothing(self):
		self.assertEqual(search_people()["matches"], [])

	def test_result_carries_what_the_ui_shows(self):
		match = next(
			m for m in search_people(email="petras@example.com")["matches"] if m["doctype"] == "CRM Lead"
		)
		for key in ("doctype", "name", "full_name", "email", "phone", "status", "owner", "strength"):
			self.assertIn(key, match)
		self.assertEqual(match["full_name"], "Petras Petraitis")

	def test_search_agrees_with_the_create_time_block(self):
		"""If TXB-73 would refuse the insert, the search has to have shown it first."""
		blocked_in = find_exact_duplicate("Jonas", "Jonaitis", "jonas@example.com")
		self.assertEqual(blocked_in, "Contact")

		result = search_people(name="Jonas Jonaitis", email="jonas@example.com")
		exact = [m for m in result["matches"] if m["strength"] == "exact"]
		self.assertTrue(any(m["doctype"] == "Contact" for m in exact))

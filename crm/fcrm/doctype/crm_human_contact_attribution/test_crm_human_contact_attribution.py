# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-279: each human contact event is attributed only to the Contacts it was with.

Every test builds one Opportunity converted from a Lead, so it has Contact A (the converted
Lead's) and a second Contact B on the same Deal, and checks that only evidence -- the Contact
itself, an invitation, a matching email or phone -- attributes an event, never the shared Deal.
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.api import activities as activities_api
from crm.fcrm.doctype.crm_deal.crm_deal import add_contact
from crm.fcrm.doctype.crm_lead.test_crm_lead import (
	add_activity_note,
	create_lead,
	ensure_conversion_result_fields,
	ensure_deal_statuses,
)
from crm.txb.constants import FIELD_CONVERTED_CONTACT
from crm.txb.contact_attribution import (
	ATTRIBUTION_DOCTYPE,
	attribute_event,
	latest_contact,
	void_event_attributions,
)
from crm.txb.human_contact import EVENT_DOCTYPE
from crm.txb.meetings import complete_meeting, sync_meeting_event

DEAL_DOCTYPE = "CRM Deal"
B_PHONE = "+370 698 76543"


def attributions(event):
	return frappe.get_all(
		ATTRIBUTION_DOCTYPE,
		filters={"event": event},
		fields=["contact", "evidence", "occurred_at"],
		order_by="contact",
	)


def make_contact(first_name, email, phone=None):
	doc = frappe.new_doc("Contact")
	doc.first_name = first_name
	doc.last_name = "TXB279"
	doc.append("email_ids", {"email_id": email, "is_primary": 1})
	if phone:
		doc.append("phone_nos", {"phone": phone, "is_primary_phone": 1})
	return doc.insert(ignore_permissions=True).name


def make_event(reference_doctype, reference_name, channel, recipients, occurred_at="2026-09-01 10:00:00"):
	source = frappe.generate_hash(length=10)
	source_doctype = "CRM Call Log" if channel == "Call" else "Communication"
	doc = frappe.get_doc(
		{
			"doctype": EVENT_DOCTYPE,
			"source_doctype": source_doctype,
			"source_name": source,
			"source_key": f"{source_doctype}:{source}",
			"channel": channel,
			"provenance": "Human",
			"occurred_at": occurred_at,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"recipients": json.dumps(recipients),
		}
	)
	# The event contract is under test, not its source document, so no real source is created.
	doc.flags.ignore_links = True
	return doc.insert(ignore_permissions=True).name


class TestCRMHumanContactAttribution(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_conversion_result_fields()
		ensure_deal_statuses()

	def setUp(self):
		suffix = frappe.generate_hash(length=6)
		self.a_email = f"ann-{suffix}@example.com"
		self.b_email = f"bea-{suffix}@example.com"
		self.lead = create_lead(first_name="Ann", email=self.a_email, organization="TXB279 Co")
		self.deal = self.lead.convert_to_deal()
		self.contact_a = frappe.db.get_value("CRM Lead", self.lead.name, FIELD_CONVERTED_CONTACT)
		self.contact_b = make_contact("Bea", self.b_email, B_PHONE)
		add_contact(self.deal, self.contact_b)
		self.assertTrue(self.contact_a)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	# -- ac-1 -----------------------------------------------------------------------------------

	def test_a_deal_email_is_attributed_only_to_the_matching_recipient(self):
		event = make_event(DEAL_DOCTYPE, self.deal, "Email", [{"email": self.a_email.upper()}])

		self.assertEqual(attribute_event(event), [self.contact_a])
		rows = attributions(event)
		self.assertEqual([(r.contact, r.evidence) for r in rows], [(self.contact_a, "Email Match")])

	# -- ac-2 -----------------------------------------------------------------------------------

	def test_a_deal_call_is_attributed_only_to_the_matching_phone(self):
		matched = make_event(DEAL_DOCTYPE, self.deal, "Call", [{"phone": "8 698 76543"}])
		unmatched = make_event(DEAL_DOCTYPE, self.deal, "Call", [{"phone": "+370 600 00000"}])

		self.assertEqual(attribute_event(matched), [self.contact_b])
		self.assertEqual(
			[(r.contact, r.evidence) for r in attributions(matched)], [(self.contact_b, "Phone Match")]
		)
		self.assertEqual(attribute_event(unmatched), [])
		self.assertEqual(attributions(unmatched), [])

	# -- ac-3 -----------------------------------------------------------------------------------

	def test_a_contact_sourced_event_is_attributed_directly(self):
		event = make_event("Contact", self.contact_b, "Email", [{"email": "someone.else@example.com"}])

		self.assertEqual(attribute_event(event), [self.contact_b])
		self.assertEqual(
			[(r.contact, r.evidence) for r in attributions(event)], [(self.contact_b, "Direct")]
		)

	def test_a_meeting_is_attributed_only_to_invited_contacts(self):
		meeting = sync_meeting_event(
			reference_doctype=DEAL_DOCTYPE,
			reference_docname=self.deal,
			flow="txb279",
			subject="Coaching",
			starts_on="2026-09-10 10:00:00",
			participants=[{"reference_doctype": "Contact", "reference_docname": self.contact_a}],
		)
		self.assertTrue(meeting)
		event = complete_meeting(meeting)["name"]

		self.assertEqual(attribute_event(event), [self.contact_a])
		self.assertEqual(
			[(r.contact, r.evidence) for r in attributions(event)], [(self.contact_a, "Participant")]
		)

	# -- ac-4 -----------------------------------------------------------------------------------

	def test_reattribution_is_idempotent_and_voiding_removes_rows(self):
		event = make_event(
			DEAL_DOCTYPE, self.deal, "Email", [{"email": self.a_email}, {"email": self.b_email}]
		)

		attribute_event(event)
		attribute_event(event)
		self.assertEqual([r.contact for r in attributions(event)], sorted([self.contact_a, self.contact_b]))

		# The pair is unique in the database, not only in attribute_event's bookkeeping.
		frappe.db.savepoint("txb279_duplicate")
		with self.assertRaises(frappe.UniqueValidationError):
			frappe.get_doc(
				{
					"doctype": ATTRIBUTION_DOCTYPE,
					"event": event,
					"contact": self.contact_a,
					"occurred_at": "2026-09-01 10:00:00",
					"evidence": "Email Match",
				}
			).insert(ignore_permissions=True)
		frappe.db.rollback(save_point="txb279_duplicate")

		frappe.db.set_value(EVENT_DOCTYPE, event, "voided", 1)
		self.assertEqual(attribute_event(event), [])
		self.assertEqual(attributions(event), [])

	def test_void_event_attributions_removes_every_row(self):
		event = make_event(DEAL_DOCTYPE, self.deal, "Email", [{"email": self.a_email}])
		attribute_event(event)

		void_event_attributions(event)
		self.assertEqual(attributions(event), [])

	# -- ac-5 -----------------------------------------------------------------------------------

	def test_latest_contact_is_the_newest_non_voided_attribution(self):
		older, newer, voided = (
			make_event(DEAL_DOCTYPE, self.deal, "Email", [{"email": self.a_email}], occurred_at)
			for occurred_at in ("2026-08-01 09:00:00", "2026-09-01 09:00:00", "2026-10-01 09:00:00")
		)
		for event in (older, newer, voided):
			attribute_event(event)
		# Voided without re-attribution: its row is still there, and must not count.
		frappe.db.set_value(EVENT_DOCTYPE, voided, "voided", 1)

		self.assertEqual(str(latest_contact(self.contact_a)), "2026-09-01 09:00:00")
		self.assertIsNone(latest_contact(self.contact_b))

	# -- ac-6 -----------------------------------------------------------------------------------

	def test_contact_activities_read_the_same_sources_through_the_public_resolver(self):
		add_activity_note("CRM Lead", self.lead.name, "Lead note")
		add_activity_note(DEAL_DOCTYPE, self.deal, "Deal note")

		self.assertIs(activities_api._resolve_contact_sources, activities_api.resolve_contact_sources)
		self.assertEqual(
			activities_api.resolve_contact_sources(self.contact_a), ([self.lead.name], [self.deal])
		)

		before = activities_api.get_contact_activities(self.contact_a)
		after = activities_api.get_contact_activities(self.contact_a)
		self.assertEqual(before, after)
		self.assertEqual(sorted(note["title"] for note in after[2]), ["Deal note", "Lead note"])

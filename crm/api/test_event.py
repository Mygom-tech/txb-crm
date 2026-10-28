import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime

from crm.api.event import get_record_event_rows, get_record_events
from crm.fcrm.doctype.crm_lead.test_crm_lead import (
	create_lead,
	ensure_conversion_result_fields,
	ensure_deal_statuses,
)
from crm.txb.constants import FIELD_CONVERTED_CONTACT


class TestRecordEvents(FrappeTestCase):
	"""A record's Events tab lists every Event on it to whoever can read the record, including
	another user's Private meetings and follow-ups, which Frappe's Event list would hide."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_conversion_result_fields()
		ensure_deal_statuses()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _user(self, email):
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		return email

	def _private_event(self, owner, doctype, name, subject, participants=None):
		frappe.set_user(owner)
		event = frappe.get_doc(
			{
				"doctype": "Event",
				"subject": subject,
				"event_type": "Private",
				"starts_on": add_days(now_datetime(), 3),
				"reference_doctype": doctype,
				"reference_docname": name,
				"event_participants": participants or [],
			}
		).insert(ignore_permissions=True)
		frappe.set_user("Administrator")
		return event.name

	def _subjects(self, doctype, name):
		filters = {"reference_doctype": doctype, "reference_docname": name}
		return sorted(e["subject"] for e in get_record_events("Event", fields=["name", "subject"], filters=filters))

	def test_contact_lists_private_events_from_contact_lead_and_deal(self):
		lead = create_lead(first_name="Domas", email="domas@ex.com", organization="Domas Co")
		owner = self._user("rep.events@example.com")
		self._private_event(owner, "CRM Lead", lead.name, "Lead follow-up")
		deal = lead.convert_to_deal()
		contact = frappe.db.get_value("CRM Lead", lead.name, FIELD_CONVERTED_CONTACT)
		self._private_event(owner, "CRM Deal", deal, "Deal meeting")
		self._private_event(owner, "Contact", contact, "Follow-up call")

		self.assertEqual(self._subjects("Contact", contact), ["Deal meeting", "Follow-up call", "Lead follow-up"])
		self.assertEqual(self._subjects("CRM Deal", deal), ["Deal meeting"])

	def test_refused_without_read_on_the_record(self):
		lead = create_lead(first_name="Rasa", email="rasa@ex.com")
		self._private_event("Administrator", "CRM Lead", lead.name, "Hidden")
		frappe.set_user(self._user("no.access@example.com"))
		with self.assertRaises(frappe.PermissionError):
			self._subjects("CRM Lead", lead.name)

	def test_participants_of_a_private_event_on_a_readable_record(self):
		lead = create_lead(first_name="Ona", email="ona@ex.com")
		owner = self._user("rep.rows@example.com")
		event = self._private_event(
			owner,
			"CRM Lead",
			lead.name,
			"Intro",
			participants=[{"reference_doctype": "CRM Lead", "reference_docname": lead.name, "email": "ona@ex.com"}],
		)
		filters = {"parenttype": "Event", "parentfield": "event_participants", "parent": ["in", [event]]}

		rows = get_record_event_rows("Event Participants", fields=["parent", "email"], filters=filters)
		self.assertEqual([(r["parent"], r["email"]) for r in rows], [(event, "ona@ex.com")])

		frappe.set_user(self._user("no.rows@example.com"))
		self.assertEqual(get_record_event_rows("Event Participants", fields=["email"], filters=filters), [])

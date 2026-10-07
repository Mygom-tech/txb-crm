"""Who each human contact was actually with (TXB-279).

A CRM Human Contact Event says contact was made on a Contact, Lead or Deal. This module records
which Contacts it was with: one CRM Human Contact Attribution row per evidenced Contact.

Only Contacts linked to the event's record are candidates -- "linked" means exactly what the
Contact's activity feed reads (`resolve_contact_sources`) -- and a candidate is attributed only on
evidence:

- Direct: the event was recorded on the Contact itself.
- Participant: the Contact was invited to the completed meeting.
- Email Match / Phone Match: a recipient's address or number is one of the Contact's own.

Sharing the Deal or Lead is not evidence, so contact on a Deal with several Contacts counts only
for the ones it was with. A Call event carries only the number dialled, so it is attributed
directly when the call was logged on the Contact and otherwise only on a Phone Match (TXB-285).
"""

import json

import frappe
from frappe.query_builder.functions import Max

from crm.api.activities import resolve_contact_sources
from crm.txb.constants import FIELD_CONVERTED_CONTACT
from crm.txb.human_contact import CHANNEL_MEETING, EVENT_DOCTYPE
from crm.txb.people import normalize_email, normalize_phone

ATTRIBUTION_DOCTYPE = "CRM Human Contact Attribution"
CONTACT_DOCTYPE = "Contact"
DEAL_DOCTYPE = "CRM Deal"
LEAD_DOCTYPE = "CRM Lead"

EVIDENCE_DIRECT = "Direct"
EVIDENCE_PARTICIPANT = "Participant"
EVIDENCE_EMAIL = "Email Match"
EVIDENCE_PHONE = "Phone Match"

# The insert rolls back to here when a concurrent attribution wins the race for the same pair.
UPSERT_SAVEPOINT = "txb_contact_attribution"


def attribute_event(event: str) -> list[str]:
	"""Make the event's attribution rows exactly its evidenced Contacts; return those Contacts.

	Idempotent: a re-run rewrites the same rows in place and removes any no longer evidenced.
	A voided or missing event keeps no rows and returns [].
	"""
	row = frappe.db.get_value(
		EVENT_DOCTYPE,
		event,
		["voided", "channel", "occurred_at", "reference_doctype", "reference_name", "recipients"],
		as_dict=True,
	)
	if not row or row.voided:
		void_event_attributions(event)
		return []

	evidence = _evidence(row)
	existing = {
		attribution.contact: attribution
		for attribution in frappe.get_all(
			ATTRIBUTION_DOCTYPE,
			filters={"event": event},
			fields=["name", "contact", "occurred_at", "evidence"],
		)
	}
	for contact, kind in evidence.items():
		values = {"evidence": kind, "occurred_at": row.occurred_at}
		current = existing.pop(contact, None)
		if current is None:
			_insert(event, contact, values)
		elif current.evidence != kind or current.occurred_at != row.occurred_at:
			frappe.db.set_value(ATTRIBUTION_DOCTYPE, current.name, values)

	if existing:
		frappe.db.delete(
			ATTRIBUTION_DOCTYPE, {"name": ("in", [stale.name for stale in existing.values()])}
		)
	return sorted(evidence)


def void_event_attributions(event: str):
	"""Remove every attribution of an event, e.g. once it is voided."""
	frappe.db.delete(ATTRIBUTION_DOCTYPE, {"event": event})


def latest_contact(contact: str):
	"""When the Contact last had human contact (newest non-voided event), or None."""
	attribution = frappe.qb.DocType(ATTRIBUTION_DOCTYPE)
	event = frappe.qb.DocType(EVENT_DOCTYPE)
	rows = (
		frappe.qb.from_(attribution)
		.join(event)
		.on(event.name == attribution.event)
		.select(Max(attribution.occurred_at))
		.where(attribution.contact == contact)
		.where(event.voided == 0)
	).run()
	return rows[0][0] if rows else None


def contacts_for_source(doctype: str | None, name: str | None) -> list[str]:
	"""The Contacts linked to a Contact, Deal or Lead -- the only candidates for attribution."""
	if not name:
		return []
	if doctype == CONTACT_DOCTYPE:
		return [name] if frappe.db.exists(CONTACT_DOCTYPE, name) else []
	if doctype not in (DEAL_DOCTYPE, LEAD_DOCTYPE):
		return []

	deals = [name] if doctype == DEAL_DOCTYPE else frappe.get_all(
		DEAL_DOCTYPE, filters={"lead": name}, pluck="name"
	)
	candidates = set()
	if deals:
		candidates.update(
			frappe.get_all(
				"CRM Contacts",
				filters={"parenttype": DEAL_DOCTYPE, "parent": ("in", deals)},
				pluck="contact",
			)
		)
	if doctype == LEAD_DOCTYPE and frappe.get_meta(LEAD_DOCTYPE).has_field(FIELD_CONVERTED_CONTACT):
		candidates.add(frappe.db.get_value(LEAD_DOCTYPE, name, FIELD_CONVERTED_CONTACT))

	# resolve_contact_sources returns (leads, deals); keep a candidate only if its own activity
	# feed reads this record, so the two can never disagree about who is linked.
	side = 1 if doctype == DEAL_DOCTYPE else 0
	return sorted(
		contact for contact in candidates if contact and name in resolve_contact_sources(contact)[side]
	)


def _evidence(event) -> dict[str, str]:
	"""Each evidenced candidate Contact mapped to its strongest evidence."""
	candidates = contacts_for_source(event.reference_doctype, event.reference_name)
	if not candidates:
		return {}

	evidence = {}
	if event.reference_doctype == CONTACT_DOCTYPE:
		evidence[event.reference_name] = EVIDENCE_DIRECT

	emails, phones = _contact_addresses(candidates)
	for recipient in _recipients(event.recipients):
		by_email = emails.get(normalize_email(recipient.get("email")), set())
		if event.channel == CHANNEL_MEETING:
			# Only the people invited to the meeting were met; a phone is never an invitation.
			invited = by_email | ({recipient.get("contact")} & set(candidates))
			for contact in invited:
				evidence.setdefault(contact, EVIDENCE_PARTICIPANT)
			continue
		for contact in by_email:
			evidence.setdefault(contact, EVIDENCE_EMAIL)
		for contact in phones.get(normalize_phone(recipient.get("phone")), set()):
			evidence.setdefault(contact, EVIDENCE_PHONE)
	return evidence


def _contact_addresses(contacts: list[str]) -> tuple[dict, dict]:
	"""Normalized email -> Contacts and normalized phone -> Contacts, over all their entries."""
	emails, phones = {}, {}

	def add(index, key, contact):
		if key:
			index.setdefault(key, set()).add(contact)

	for row in frappe.get_all(
		CONTACT_DOCTYPE,
		filters={"name": ("in", contacts)},
		fields=["name", "email_id", "phone", "mobile_no"],
	):
		add(emails, normalize_email(row.email_id), row.name)
		add(phones, normalize_phone(row.phone), row.name)
		add(phones, normalize_phone(row.mobile_no), row.name)

	child_filters = {"parenttype": CONTACT_DOCTYPE, "parent": ("in", contacts)}
	for row in frappe.get_all("Contact Email", filters=child_filters, fields=["parent", "email_id"]):
		add(emails, normalize_email(row.email_id), row.parent)
	for row in frappe.get_all("Contact Phone", filters=child_filters, fields=["parent", "phone"]):
		add(phones, normalize_phone(row.phone), row.parent)
	return emails, phones


def _recipients(value) -> list[dict]:
	recipients = json.loads(value) if isinstance(value, str) else value
	return [recipient for recipient in recipients or [] if isinstance(recipient, dict)]


def _insert(event: str, contact: str, values: dict):
	frappe.db.savepoint(UPSERT_SAVEPOINT)
	try:
		frappe.get_doc(
			{"doctype": ATTRIBUTION_DOCTYPE, "event": event, "contact": contact, **values}
		).insert(ignore_permissions=True)
	except frappe.UniqueValidationError:
		# A concurrent attribution of this event inserted the pair first; update the winner.
		frappe.db.rollback(save_point=UPSERT_SAVEPOINT)
		frappe.db.set_value(ATTRIBUTION_DOCTYPE, {"event": event, "contact": contact}, values)

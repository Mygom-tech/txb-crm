"""Typed, human-origin contact events (TXB-277).

A CRM Human Contact Event row exists only for contact the server itself verified as made by a
person: a manual email send, an outgoing WhatsApp send, a completed outgoing call, a Completed
coaching call and a meeting someone marked completed. Everything else -- system, reminder,
assignment or registration mail, inbound or failed messages, reactions, inbound or unanswered
calls, scheduled/cancelled meetings -- records nothing.

Two questions decide a row, and they are answered at different times:

- Provenance. Emails, WhatsApp messages, coaching call Notes and meetings look identical whether
  a person or the system wrote them, so human origin is established only by the whitelisted action
  that a person invoked: it flags the source document (HUMAN_ORIGIN_FLAG) and records it. From then
  on, the ledger row itself is the durable proof. A completed outgoing Call Log proves itself: it
  is a call its caller placed (TXB-285).
- Qualification. `classify` reads the source's current state, so `sync_source` can be re-run at
  any time: it upserts the one row per `source_key`, and voids it (rather than deleting it) when
  the source stops qualifying -- a reopened meeting, a failed send, a deleted source.
"""

import json

import frappe
from frappe.utils import get_datetime, now_datetime, parse_addr, split_emails

from crm.txb.constants import FIELD_COACHING_CALL_DELIVERY_DATE, FIELD_COACHING_CALL_STATUS
from crm.txb.people import normalize_phone

EVENT_DOCTYPE = "CRM Human Contact Event"
COMMUNICATION_DOCTYPE = "Communication"
WHATSAPP_DOCTYPE = "WhatsApp Message"
NOTE_DOCTYPE = "FCRM Note"
CALL_LOG_DOCTYPE = "CRM Call Log"
MEETING_DOCTYPE = "Event"
DEAL_DOCTYPE = "CRM Deal"

CHANNEL_EMAIL = "Email"
CHANNEL_WHATSAPP = "WhatsApp"
CHANNEL_CALL = "Call"
CHANNEL_COACHING_CALL = "Coaching Call"
CHANNEL_MEETING = "Meeting"
PROVENANCE_HUMAN = "Human"

# Set on a source document by the human action that wrote it; see the module docstring.
HUMAN_ORIGIN_FLAG = "txb_human_origin"

# A Communication whose delivery ended in one of these never reached anyone.
FAILED_EMAIL_STATUSES = ("Error", "Rejected")
MEETING_COMPLETED = "Completed"
CALL_OUTGOING = "Outgoing"
CALL_COMPLETED = "Completed"

# The upsert rolls back to here when a concurrent writer wins the race for the same source_key.
UPSERT_SAVEPOINT = "txb_human_contact_event"


def source_key(doctype: str, name: str) -> str:
	return f"{doctype}:{name}"


def classify(doc) -> str | None:
	"""The contact channel `doc` currently qualifies for as human contact, or None."""
	if doc.doctype == COMMUNICATION_DOCTYPE:
		qualifies = (
			doc.communication_type == "Communication"
			and doc.communication_medium == "Email"
			and doc.sent_or_received == "Sent"
			and doc.delivery_status not in FAILED_EMAIL_STATUSES
		)
		return CHANNEL_EMAIL if qualifies and _human_origin(doc) else None

	if doc.doctype == WHATSAPP_DOCTYPE:
		qualifies = (
			doc.get("type") != "Incoming"
			and doc.get("content_type") != "reaction"
			and (doc.get("status") or "").lower() != "failed"
		)
		return CHANNEL_WHATSAPP if qualifies and _human_origin(doc) else None

	if doc.doctype == NOTE_DOCTYPE:
		# Imported here: coaching_calls reaches pipelines.common, which imports meetings, which
		# imports this module -- a top-level import would close that cycle.
		from crm.txb.coaching_calls import STATUS_COMPLETED, note_status

		status = note_status(doc.title, doc.content, doc.get(FIELD_COACHING_CALL_STATUS))
		qualifies = doc.reference_doctype == DEAL_DOCTYPE and status == STATUS_COMPLETED
		return CHANNEL_COACHING_CALL if qualifies and _human_origin(doc) else None

	if doc.doctype == CALL_LOG_DOCTYPE:
		qualifies = doc.get("type") == CALL_OUTGOING and doc.get("status") == CALL_COMPLETED
		return CHANNEL_CALL if qualifies else None

	if doc.doctype == MEETING_DOCTYPE:
		return CHANNEL_MEETING if doc.status == MEETING_COMPLETED and _human_origin(doc) else None

	return None


def sync_source(doctype: str, name: str) -> dict | None:
	"""Upsert the one event row for a source; void it when the source no longer qualifies.

	Idempotent: a second run finds the same row by `source_key` and rewrites the same values.
	Returns the row as a dict, or None when the source neither qualifies nor ever did.
	"""
	doc = frappe.get_doc(doctype, name) if frappe.db.exists(doctype, name) else None
	return _sync(doctype, name, doc)


def record_human_send(doc) -> dict | None:
	"""Record `doc` as written by the person in this request, then sync it.

	Only the whitelisted human actions call this; it is what separates their writes from an
	identical-looking document a system path inserts directly.
	"""
	doc.flags[HUMAN_ORIGIN_FLAG] = True
	return _sync(doc.doctype, doc.name, doc)


def sync_doc(doc) -> dict | None:
	"""`sync_source` for a document already in hand, e.g. from a doc event."""
	return _sync(doc.doctype, doc.name, doc)


def _sync(doctype: str, name: str, doc) -> dict | None:
	key = source_key(doctype, name)
	channel = classify(doc) if doc is not None else None
	existing = _find(key)

	if not channel:
		if existing is None:
			return None
		if not existing.voided:
			existing.voided = 1
			# Voiding runs from the source's after_delete too, when its link no longer resolves.
			existing.flags.ignore_links = True
			existing.save(ignore_permissions=True)
		return existing.as_dict()

	values = _event_values(doc, channel)
	if existing is not None:
		return _update(existing, values)

	row = frappe.get_doc({"doctype": EVENT_DOCTYPE, "source_key": key, **values})
	frappe.db.savepoint(UPSERT_SAVEPOINT)
	try:
		row.insert(ignore_permissions=True)
	except frappe.UniqueValidationError:
		# A concurrent sync inserted this source's row first; update the winner instead.
		frappe.db.rollback(save_point=UPSERT_SAVEPOINT)
		winner = _find(key)
		if winner is None:
			raise
		return _update(winner, values)
	return row.as_dict()


def _update(row, values: dict) -> dict:
	# Who made the contact is fixed when it is first recorded; a later re-sync by someone else
	# (a reopen-and-complete, a background re-check) must not reassign it.
	values["actor"] = row.actor or values["actor"]
	row.update(values)
	row.voided = 0
	row.save(ignore_permissions=True)
	return row.as_dict()


def _find(key: str):
	name = frappe.db.get_value(EVENT_DOCTYPE, {"source_key": key}, "name")
	return frappe.get_doc(EVENT_DOCTYPE, name) if name else None


def _human_origin(doc) -> bool:
	"""Whether a human action wrote this source: flagged now, or recorded by one earlier."""
	if doc.flags.get(HUMAN_ORIGIN_FLAG):
		return True
	return bool(frappe.db.exists(EVENT_DOCTYPE, {"source_key": source_key(doc.doctype, doc.name)}))


def _event_values(doc, channel: str) -> dict:
	return {
		"source_doctype": doc.doctype,
		"source_name": doc.name,
		"channel": channel,
		"provenance": PROVENANCE_HUMAN,
		"actor": _actor(doc),
		"occurred_at": _occurred_at(doc),
		"reference_doctype": doc.get("reference_doctype"),
		"reference_name": doc.get("reference_name") or doc.get("reference_docname"),
		"recipients": json.dumps(_recipients(doc)),
		"voided": 0,
	}


def _actor(doc) -> str:
	# A meeting is contact made by whoever completes it, not whoever scheduled the Event.
	if doc.doctype == MEETING_DOCTYPE:
		return frappe.session.user
	if doc.doctype == COMMUNICATION_DOCTYPE:
		return doc.user or doc.owner
	if doc.doctype == CALL_LOG_DOCTYPE:
		return doc.get("caller") or doc.owner
	return doc.owner


def _occurred_at(doc):
	if doc.doctype == COMMUNICATION_DOCTYPE:
		return doc.communication_date or doc.creation
	if doc.doctype == NOTE_DOCTYPE:
		delivered = doc.get(FIELD_COACHING_CALL_DELIVERY_DATE)
		return get_datetime(delivered) if delivered else doc.creation
	if doc.doctype == MEETING_DOCTYPE:
		# Completing a meeting ahead of its slot must not record contact in the future.
		return min(get_datetime(doc.starts_on), now_datetime())
	if doc.doctype == CALL_LOG_DOCTYPE:
		return get_datetime(doc.get("end_time") or doc.get("start_time") or doc.creation)
	return doc.creation


def _recipients(doc) -> list[dict]:
	"""Who was contacted, as `{email?, phone?, contact?}` entries (may be empty)."""
	if doc.doctype == COMMUNICATION_DOCTYPE:
		# To and CC are the people addressed; BCC is deliberately hidden from the record.
		emails = []
		for address in split_emails(doc.recipients or "") + split_emails(doc.cc or ""):
			email = parse_addr(address)[1]
			if email and email.lower() not in emails:
				emails.append(email.lower())
		return [{"email": email} for email in emails]

	if doc.doctype == WHATSAPP_DOCTYPE:
		return [{"phone": doc.get("to")}] if doc.get("to") else []

	if doc.doctype == CALL_LOG_DOCTYPE:
		# The number dialled; the "-" placeholder a log without one is given is no number at all.
		return [{"phone": doc.get("to")}] if normalize_phone(doc.get("to")) else []

	if doc.doctype == NOTE_DOCTYPE:
		contacts = frappe.get_all(
			"CRM Contacts",
			filters={"parenttype": DEAL_DOCTYPE, "parent": doc.reference_docname},
			pluck="contact",
		)
		return [{"contact": contact} for contact in contacts if contact]

	if doc.doctype == MEETING_DOCTYPE:
		# Users on the Event are the CRM side of the meeting, not the people contacted.
		recipients = []
		for participant in doc.get("event_participants") or []:
			if participant.reference_doctype == "User":
				continue
			entry = {}
			if participant.email:
				entry["email"] = participant.email
			if participant.reference_doctype == "Contact" and participant.reference_docname:
				entry["contact"] = participant.reference_docname
			if entry:
				recipients.append(entry)
		return recipients

	return []

"""One canonical, linked CRM Event per TxB meeting (TXB-209).

Every existing scheduling action -- Lead Discovery and the centralized Opportunity meeting
transitions -- routes through here so a meeting becomes exactly one auditable Frappe Event,
linked to its owning Lead/Deal through reference_doctype/reference_docname. That is the same
linkage the normalized activity reader (`crm.api.activities._meeting_events`) already reads,
so a scheduled meeting surfaces on the record's Events/Activity surface with no parallel store.

Stable identity. Each source meeting owns a deterministic key,
`<reference_doctype>:<reference_docname>:<flow>` (see FIELD_MEETING_KEY), so a repeated submit,
a retry, a reschedule or a cancel all resolve to the SAME Event and mutate it in place rather
than spawning a duplicate. The key field is database-UNIQUE, which both enforces the
one-Event-per-flow invariant and lets `sync_meeting_event` recover a lost duplicate-key race by
reusing the winner -- mirroring `create_coaching_deal`.

Transaction. Events are inserted/saved immediately, inside the caller's request transaction
(the action handler runs before `execute_action`/`schedule_discovery` saves the source once), so
the meeting-flow status/data mutation and the Event lifecycle change commit together or not at
all. Nothing here commits or talks to any calendar provider.
"""

import frappe
from frappe.utils import add_to_date, escape_html, get_datetime

from crm.txb.constants import FIELD_MEETING_KEY

EVENT_DOCTYPE = "Event"

# Status the Event rests in while the meeting is live. Frappe ships Open/Completed/Closed and
# (via customisation) Cancelled; the activity reader treats Cancelled as the cancellation moment.
STATUS_OPEN = "Open"
STATUS_CANCELLED = "Cancelled"

# A meeting with no explicit end is treated as a one-hour calendar block, so the Event always
# carries a valid, non-degenerate duration for the calendar and the activity reader.
DEFAULT_DURATION_HOURS = 1

# The insert rolls back to here when a concurrent writer wins the race for the same meeting key.
# Rolling back only to this savepoint keeps the caller's action transaction usable.
MEETING_SAVEPOINT = "txb_meeting_event"


def meeting_key(reference_doctype: str, reference_docname: str, flow: str) -> str:
	"""The stable identity for one source meeting: owning record + meeting-flow key."""
	return f"{reference_doctype}:{reference_docname}:{flow}"


def sync_meeting_event(
	*,
	reference_doctype: str,
	reference_docname: str,
	flow: str,
	subject: str,
	starts_on,
	ends_on=None,
	meeting_type: str | None = None,
	link: str | None = None,
	address: str | None = None,
	participants: list[dict] | None = None,
) -> str | None:
	"""Create or update the one canonical Event for (source, flow); return its name.

	Idempotent by construction: the meeting key resolves an existing Event and mutates it
	(a reschedule moves starts_on/ends_on and re-opens it), preserving Event audit Versions,
	or inserts a new one when none exists. A repeated identical submit finds the same Event and
	writes the same values -- no duplicate. Returns None when there is nothing to schedule or the
	key field is not installed on this site (an un-migrated site keeps its prior behaviour).
	"""
	if not starts_on:
		return None
	if not _meeting_key_installed():
		return None

	key = meeting_key(reference_doctype, reference_docname, flow)
	starts_on = get_datetime(starts_on)
	ends_on = get_datetime(ends_on) if ends_on else add_to_date(starts_on, hours=DEFAULT_DURATION_HOURS)

	values = {
		"subject": subject,
		"starts_on": starts_on,
		"ends_on": ends_on,
		# Rescheduling a previously cancelled meeting means it is on again.
		"status": STATUS_OPEN,
		"location": address or "",
		"description": _meeting_description(meeting_type, link, address),
	}

	event = _find_meeting_event(key)
	if event is not None:
		_apply(event, values)
		event.save(ignore_permissions=True)
		return event.name

	return _insert_meeting_event(key, reference_doctype, reference_docname, values, participants)


def cancel_meeting_event(reference_doctype: str, reference_docname: str, flow: str) -> str | None:
	"""Cancel the canonical Event for (source, flow) in place, preserving its history.

	Mutates the same Event to the Cancelled status rather than deleting or replacing it, so the
	cancellation is recoverable from the Event's audit Versions and reads as the cancellation
	moment in the timeline. Idempotent: a second cancel, or a cancel with no Event yet, is a
	no-op. Returns the Event name when one exists, else None.
	"""
	if not _meeting_key_installed():
		return None

	key = meeting_key(reference_doctype, reference_docname, flow)
	event = _find_meeting_event(key)
	if event is None:
		return None
	if event.status == STATUS_CANCELLED:
		return event.name

	event.status = STATUS_CANCELLED
	event.save(ignore_permissions=True)
	return event.name


def deal_participants(deal) -> list[dict]:
	"""The Event participants derivable from a Deal: its linked Contacts.

	Each Contact row becomes an Event Participant referencing the same Contact, so the meeting
	carries the people it is with when they are known. Owners/trainers are captured on the deal's
	own fields and in the description rather than forced into the participants table.
	"""
	participants = []
	for row in deal.get("contacts") or []:
		contact = row.get("contact") if hasattr(row, "get") else getattr(row, "contact", None)
		if contact:
			participants.append({"reference_doctype": "Contact", "reference_docname": contact})
	return participants


def _insert_meeting_event(
	key: str,
	reference_doctype: str,
	reference_docname: str,
	values: dict,
	participants: list[dict] | None,
) -> str:
	"""Insert a fresh canonical Event, yielding to the winner if a race beats us to the key."""
	event = frappe.new_doc(EVENT_DOCTYPE)
	event.set(FIELD_MEETING_KEY, key)
	event.reference_doctype = reference_doctype
	event.reference_docname = reference_docname
	event.event_type = "Private"
	event.event_category = "Meeting"
	_apply(event, values)
	for participant in participants or []:
		event.append("event_participants", participant)

	frappe.db.savepoint(MEETING_SAVEPOINT)
	try:
		event.insert(ignore_permissions=True)
	except frappe.UniqueValidationError:
		# A concurrent scheduling inserted this meeting's Event first. Roll our failed insert
		# back so the caller's transaction stays usable, then update the winner in place. If the
		# conflict was on some other unique field the lookup finds nothing and we re-raise.
		frappe.db.rollback(save_point=MEETING_SAVEPOINT)
		winner = _find_meeting_event(key)
		if not winner:
			raise
		_apply(winner, values)
		winner.save(ignore_permissions=True)
		return winner.name

	return event.name


def _find_meeting_event(key: str):
	"""The Event carrying this meeting key, as a saved document, or None."""
	name = frappe.db.get_value(EVENT_DOCTYPE, {FIELD_MEETING_KEY: key}, "name")
	if not name:
		return None
	return frappe.get_doc(EVENT_DOCTYPE, name)


def _apply(event, values: dict):
	for field, value in values.items():
		event.set(field, value)


def _meeting_key_installed() -> bool:
	"""Whether this site has run the meeting-key patch; guarded so an un-migrated site still works."""
	return frappe.get_meta(EVENT_DOCTYPE).has_field(FIELD_MEETING_KEY)


def _meeting_description(meeting_type: str | None, link: str | None, address: str | None) -> str:
	"""Render the meeting's type and its link or address as the Event's description body.

	Only the details the action actually supplied are shown, each escaped so a user-entered link
	or address is recorded as text rather than interpreted as markup. Returns an empty string when
	nothing extra was supplied, leaving the Event's date/time and linkage to speak for themselves.
	"""
	rows = []
	if meeting_type:
		rows.append(f"<div><b>Type:</b> {escape_html(str(meeting_type).strip())}</div>")
	if link:
		rows.append(f"<div><b>Link:</b> {escape_html(str(link).strip())}</div>")
	if address:
		rows.append(f"<div><b>Address:</b> {escape_html(str(address).strip())}</div>")
	return "".join(rows)

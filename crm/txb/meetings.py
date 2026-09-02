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
LEAD_DOCTYPE = "CRM Lead"
DEAL_DOCTYPE = "CRM Deal"
CONTACT_DOCTYPE = "Contact"

# TXB-213: joins the flow's base meeting title and the resolved customer name in a generated
# Event subject, composing `<meeting title> — <customer name>`. An em dash, matching the format
# the meeting brief specifies.
SUBJECT_NAME_SEPARATOR = " — "

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

	# TXB-213: name the customer in the generated Event's subject so it reads
	# `<meeting title> — <Lead or Contact name>` wherever the Event surfaces. Composed here, on the
	# shared boundary, so both insert and existing-Event update paths apply the same subject on
	# creation, retry and reschedule -- and every flow keeps supplying only its translatable base
	# title.
	subject = _compose_subject(subject, reference_doctype, reference_docname)

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
		# TXB-212: reconcile required attendees on every existing-Event save (reschedule, retry,
		# plain update) -- not only on first insert -- so a meeting that gained a resolvable owner
		# or target since it was created acquires them without spawning a second Event.
		_reconcile_participants(event, participants)
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
	"""The required Event attendees derivable from a Deal: its owner and its linked Contacts.

	TXB-212: the Deal owner (a User) is the source-record owner and every linked Contact is a
	customer target, so both belong on the meeting when they resolve. The owner is emitted first,
	then each Contact row as an Event Participant referencing that Contact. An owner with no
	resolvable email is omitted rather than blocking the meeting; blank Contact rows are skipped.
	"""
	participants = []
	owner = _owner_participant(deal.get("deal_owner"))
	if owner:
		participants.append(owner)
	for row in deal.get("contacts") or []:
		contact = row.get("contact") if hasattr(row, "get") else getattr(row, "contact", None)
		if contact:
			participants.append({"reference_doctype": "Contact", "reference_docname": contact})
	return participants


def lead_participants(lead) -> list[dict]:
	"""The required Event attendees derivable from a Lead: its owner and the Lead target.

	TXB-212: the owner comes from `lead_owner` (a User) and the customer target is the Lead itself,
	carrying its resolvable email. Either is omitted when its email cannot be resolved, so a Lead
	with no owner or no email still schedules its meeting -- just with fewer attendees.
	"""
	participants = []
	owner = _owner_participant(lead.get("lead_owner"))
	if owner:
		participants.append(owner)
	target = _lead_target_participant(lead)
	if target:
		participants.append(target)
	return participants


def _owner_participant(owner: str | None) -> dict | None:
	"""An Event Participant for a source-record owner (a User), or None when it cannot resolve.

	The owner is a User link, so the participant references that User and carries the User's email
	for deduplication against a target or a manually added attendee. Returns None when there is no
	owner or the User has no resolvable email -- the missing-attendee case never blocks the meeting.
	"""
	if not owner:
		return None
	email = _user_email(owner)
	if not email:
		return None
	return {"reference_doctype": "User", "reference_docname": owner, "email": email}


def _lead_target_participant(lead) -> dict | None:
	"""An Event Participant for a Lead's customer target: the Lead itself with its email.

	The Lead is not yet a Contact, so the resolvable reference is the Lead record carrying its
	primary email. Returns None when the Lead has no email, omitting only this attendee.
	"""
	email = lead.get("email")
	if not _normalize_email(email):
		return None
	return {"reference_doctype": "CRM Lead", "reference_docname": lead.get("name"), "email": email}


def _user_email(user: str | None) -> str | None:
	"""The email address for a User, from its `email` field, falling back to an email-shaped id."""
	if not user:
		return None
	email = frappe.db.get_value("User", user, "email")
	if email:
		return email
	return user if "@" in user else None


def _normalize_email(email: str | None) -> str:
	"""A canonical form for email comparison: trimmed and lower-cased, or empty when absent."""
	return email.strip().lower() if email else ""


def _reconcile_participants(event, participants: list[dict] | None):
	"""Add required attendees to an Event without removing any it already carries (TXB-212).

	Additive and idempotent: an attendee already present -- by canonical reference
	(reference_doctype + reference_docname) or by normalized email -- is skipped, so a repeat sync
	adds nothing and manually added participants survive. Deduplicates the incoming list against
	itself too, collapsing an owner and target that share a reference or email into one row.
	"""
	seen_refs = set()
	seen_emails = set()
	for existing in event.get("event_participants") or []:
		if existing.reference_docname:
			seen_refs.add((existing.reference_doctype, existing.reference_docname))
		email = _normalize_email(existing.get("email"))
		if email:
			seen_emails.add(email)

	for part in participants or []:
		ref = (part.get("reference_doctype"), part.get("reference_docname"))
		email = _normalize_email(part.get("email"))
		if part.get("reference_docname") and ref in seen_refs:
			continue
		if email and email in seen_emails:
			continue
		event.append("event_participants", part)
		if part.get("reference_docname"):
			seen_refs.add(ref)
		if email:
			seen_emails.add(email)


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
	_reconcile_participants(event, participants)

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
		# TXB-212: the winner is an existing Event too, so it gets the same additive attendee
		# reconciliation -- the race loser's required attendees are not silently dropped.
		_reconcile_participants(winner, participants)
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


def _compose_subject(subject: str, reference_doctype: str, reference_docname: str) -> str:
	"""Append the resolved customer name to the base meeting title (TXB-213).

	Composes `<meeting title> — <customer name>` so a generated Event identifies the Lead or the
	Opportunity's Contact. When no person name resolves the base subject is returned unchanged --
	never a dangling separator, an internal document id or placeholder text -- so the fallback is
	exactly the flow's existing generic title.
	"""
	name = _customer_display_name(reference_doctype, reference_docname)
	if not name:
		return subject
	return f"{subject}{SUBJECT_NAME_SEPARATOR}{name}"


def _customer_display_name(reference_doctype: str, reference_docname: str) -> str:
	"""The customer person name for a generated meeting's owning record, or "" when none resolves.

	A Lead resolves to its own display/full name; a Deal resolves to its primary linked Contact,
	falling back to the first linked Contact that has a resolvable name. Any other source, or an
	unresolvable name, yields "" so the caller keeps the generic title.
	"""
	if reference_doctype == LEAD_DOCTYPE:
		return _lead_display_name(reference_docname)
	if reference_doctype == DEAL_DOCTYPE:
		return _deal_contact_display_name(reference_docname)
	return ""


def _lead_display_name(lead: str) -> str:
	"""A Lead's display name: its `lead_name`, else its first/last name, else "" (TXB-213)."""
	row = frappe.db.get_value(
		LEAD_DOCTYPE, lead, ["lead_name", "first_name", "last_name"], as_dict=True
	)
	if not row:
		return ""
	return _format_person_name(row.get("lead_name")) or _format_person_name(
		row.get("first_name"), row.get("last_name")
	)


def _deal_contact_display_name(deal: str) -> str:
	"""A Deal's customer name from its linked Contacts, preferring the primary one (TXB-213).

	Contacts are considered primary-first, then in table order, and the first that resolves to a
	real person name wins. Returns "" when the Deal has no linked Contact with a resolvable name.
	"""
	doc = frappe.get_doc(DEAL_DOCTYPE, deal)
	for row in _primary_first(doc.get("contacts") or []):
		contact = row.get("contact") if hasattr(row, "get") else getattr(row, "contact", None)
		name = _contact_display_name(contact)
		if name:
			return name
	return ""


def _primary_first(rows: list) -> list:
	"""Deal Contact rows ordered primary-first, otherwise preserving their table order (TXB-213).

	`sorted` is stable, so a non-primary row keeps its original position relative to its peers,
	making the first resolvable name deterministic: the primary Contact, else the first listed.
	"""
	return sorted(rows, key=lambda row: 0 if _row_is_primary(row) else 1)


def _row_is_primary(row) -> bool:
	value = row.get("is_primary") if hasattr(row, "get") else getattr(row, "is_primary", 0)
	return bool(value)


def _contact_display_name(contact: str | None) -> str:
	"""A Contact's display name from its first/last name, or "" when it cannot resolve (TXB-213)."""
	if not contact:
		return ""
	row = frappe.db.get_value(
		CONTACT_DOCTYPE, contact, ["first_name", "last_name"], as_dict=True
	)
	if not row:
		return ""
	return _format_person_name(row.get("first_name"), row.get("last_name"))


def _format_person_name(*parts: str | None) -> str:
	"""Join present, stripped name parts with a single space; "" when none resolve (TXB-213)."""
	return " ".join(part.strip() for part in parts if part and part.strip())


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

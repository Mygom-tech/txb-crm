"""Completed coaching calls, counted from the notes that record them (TXB-247).

`total_completed_calls` on a Delivering Coaching Opportunity used to be stale state: the Log
Coaching Call action incremented whatever number happened to be stored, and the generic CRM
Call Log counter overwrote the same field from a completely different table. Neither could be
reconciled against what actually happened, and both drifted -- CRM-DEAL-2026-00315 sat at 0
with several linked notes reading "Call Status: Completed".

The linked notes are the record of the calls, so they are the authority here. The total is
never incremented; it is always recounted from the deal's CURRENT linked FCRM Notes, and only
a note carrying an EXPLICIT, exactly-matching Completed status is counted. A coaching note
whose status cannot be read is unclassified and counts for nothing -- a missing marker is
never read as Completed.

Two sources of that status, in precedence order:

1. `FIELD_COACHING_CALL_STATUS`, app-owned structured metadata seeded when the workflow logs
   a call and populated for recognizable legacy notes by the reconcile patch.
2. The note body, parsed from a normalized copy of its HTML. Notes predate the field, users
   may edit a note after it is written, and the body is what a human reads, so a status read
   from the body wins over a stored value that no longer matches it. The note's content is
   never rewritten -- only classified.
"""

import datetime
import html
import re

import frappe
from frappe.utils import getdate

from crm.txb.constants import (
	FIELD_COACHING_CALL_DELIVERY_DATE,
	FIELD_COACHING_CALL_STATUS,
	FIELD_FIRST_CALL_DATE,
	PIPELINE_DELIVERING_COACHING,
)

DEAL_DOCTYPE = "CRM Deal"
NOTE_DOCTYPE = "FCRM Note"
TOTAL_FIELD = "total_completed_calls"

STATUS_COMPLETED = "Completed"

# The statuses a coaching call may be logged under, in the order the Log Coaching Call form
# offers them. Only these three are accepted when classifying a note: anything else ("Not
# completed", "Rescheduled", a free-text remark) leaves the note unclassified.
CALL_STATUSES = (STATUS_COMPLETED, "Missed", "No charge")

# Matched case-insensitively against a note's normalized title. Every coaching call note the
# workflow has ever written is titled "Coaching Call #<n> - <date>[ - <topic>]"; the substring
# also recognizes the legacy variants that carry a prefix.
TITLE_MARKER = "coaching call"

# The labelled status line inside a note body, e.g. "Call Status: Completed". The captured
# segment runs to the end of its line and is then compared whole against CALL_STATUSES, so a
# qualified value such as "Completed early" does not match "Completed".
STATUS_LINE = re.compile(r"call status\s*:\s*([^\n]*)", re.IGNORECASE)

# `<br>` is how the workflow joins a note body's lines, so tags become line breaks before the
# markup is stripped -- otherwise "Call Status: Completed<br>Topic: X" reads as one line and
# the captured segment is never an exact status.
BLOCK_TAGS = re.compile(r"<\s*/?\s*(br|p|div|li|tr|h[1-6])\b[^>]*>", re.IGNORECASE)
ANY_TAG = re.compile(r"<[^>]*>")

# An ISO date inside a historical Coaching Call title, e.g. "Coaching Call #3 - 2026-02-14 - Goals".
# Read only by the migration: live notes carry `FIELD_COACHING_CALL_DELIVERY_DATE` instead.
TITLE_ISO_DATE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")


def note_status(title: str, content: str, stored: str = None) -> str | None:
	"""The call status of one note, or None when it carries no readable one.

	The title gate is what keeps ordinary notes -- a hold reason, a contract cleared note --
	out of the count entirely, whatever words their body happens to contain. A note already
	carrying app-owned metadata is a coaching call note by construction, so it passes the gate
	on that alone.
	"""
	stored = stored if stored in CALL_STATUSES else None

	if not stored and TITLE_MARKER not in _normalize(title):
		return None

	return _status_from_content(content) or stored


def count_completed_calls(deal_name: str) -> int:
	"""How many of a deal's current linked notes record a Completed coaching call."""
	notes = frappe.get_all(
		NOTE_DOCTYPE,
		filters={"reference_doctype": DEAL_DOCTYPE, "reference_docname": deal_name},
		fields=["title", "content", *_stored_status_field()],
	)
	return sum(1 for note in notes if _row_status(note) == STATUS_COMPLETED)


def reconcile_deal(deal_name: str) -> int | None:
	"""Recount and store a Delivering Coaching deal's total. Returns the total, or None.

	Scoped to the one pipeline that owns this meaning: on every other pipeline the field is
	the CRM Call Log count and is left exactly as it is.

	Written with `update_modified=False`, deliberately. Editing a note is not an edit of the
	Opportunity, so it must not move the deal's timestamp -- and the note handlers can fire
	while the caller still holds the deal document in memory (Log Coaching Call inserts its
	note mid-action), where bumping `modified` underneath it would fail the caller's own save
	on a timestamp mismatch.
	"""
	if not deal_name:
		return None

	deal = frappe.db.get_value(
		DEAL_DOCTYPE, deal_name, ["pipeline_type", TOTAL_FIELD], as_dict=True
	)
	if not deal or deal.get("pipeline_type") != PIPELINE_DELIVERING_COACHING:
		return None

	total = count_completed_calls(deal_name)
	# An unset total and a count of zero are the same state; writing one over the other would
	# make every note event a deal write for nothing.
	if (deal.get(TOTAL_FIELD) or 0) != total:
		frappe.db.set_value(DEAL_DOCTYPE, deal_name, TOTAL_FIELD, total, update_modified=False)

	return total


def is_coaching_call_note(title: str) -> bool:
	"""Whether a note's title identifies it as a coaching call note."""
	return TITLE_MARKER in _normalize(title or "")


def classify_note_status(title: str, content: str, stored: str = None) -> str | None:
	"""The status to persist on a note, or None to leave the stored metadata alone.

	Idempotent: re-running over an already-classified note derives the same value from the
	same body, so the patch and the note hook can both run any number of times.
	"""
	status = note_status(title, content, stored)
	return status if status != stored else None


def status_field_installed() -> bool:
	"""Whether this site has run the reconcile patch; guarded so an un-migrated site still counts."""
	return frappe.get_meta(NOTE_DOCTYPE).has_field(FIELD_COACHING_CALL_STATUS)


def delivery_date_field_installed() -> bool:
	"""Whether this site has run the first-call-date patch; an un-migrated site seeds nothing."""
	return frappe.get_meta(NOTE_DOCTYPE).has_field(FIELD_COACHING_CALL_DELIVERY_DATE)


def title_delivery_date(title: str) -> datetime.date | None:
	"""The Delivery Date an historical Coaching Call title states, or None when it is ambiguous.

	Strict on purpose: exactly one distinct, valid `YYYY-MM-DD` date. A title with no date, two
	different dates, or an impossible one ("2026-02-30") yields nothing rather than a guess.
	"""
	found = set(TITLE_ISO_DATE.findall(title or ""))
	if len(found) != 1:
		return None
	try:
		return datetime.date.fromisoformat(found.pop())
	except ValueError:
		return None


def first_call_value(delivery_date) -> str:
	"""A note's Delivery Date as the Opportunity's First Coaching Call Datetime: local midnight."""
	return f"{getdate(delivery_date).isoformat()} 00:00:00"


def lock_deal_row(deal_name: str) -> None:
	"""Take the Opportunity row lock that serializes first-call seeding (TXB-261).

	The official Log Coaching Call action takes it before it counts the deal's existing call
	notes, so a concurrent first call queues here and then counts the note the winner inserted.
	"""
	if deal_name:
		frappe.db.get_value(DEAL_DOCTYPE, deal_name, "name", for_update=True)


def seed_first_call_date_from_action(
	deal_name: str, note_name: str, delivery_date, call_status: str
) -> str | None:
	"""Seed an empty First Coaching Call Date from the submitted first-call Delivery Date (TXB-261).

	The document-event path (`seed_first_call_date`) reads the note's structured metadata, which
	a site mid-migration may not have installed yet -- there the hook correctly stands down, and
	the official action would silently log a first call that seeds nothing. This path takes the
	same row lock and the same guards, but from the values the action itself submitted, so the
	seed never depends on note metadata being present.

	Every guard of the document-event path still applies, and by the same rules: the pipeline,
	an already-set date, and a date a user deliberately cleared on a deal that already has call
	notes. Written with `update_modified=False` inside the action's transaction, so a
	rolled-back note takes the date with it. Returns the value written, or None.
	"""
	if not deal_name or not delivery_date or call_status not in CALL_STATUSES:
		return None

	deal = frappe.db.get_value(
		DEAL_DOCTYPE,
		deal_name,
		["pipeline_type", FIELD_FIRST_CALL_DATE],
		as_dict=True,
		for_update=True,
	)
	if not deal or deal.get("pipeline_type") != PIPELINE_DELIVERING_COACHING:
		return None
	if deal.get(FIELD_FIRST_CALL_DATE):
		return None
	if _has_other_call_note(deal_name, note_name):
		return None

	value = first_call_value(delivery_date)
	frappe.db.set_value(DEAL_DOCTYPE, deal_name, FIELD_FIRST_CALL_DATE, value, update_modified=False)
	return value


def lock_deal_for_first_call(note) -> None:
	"""Serialize a Coaching Call Note insert against its Opportunity (TXB-224).

	Taken before the note is inserted, so two concurrent first calls on the same deal queue up
	here; the second only proceeds once the first has committed or rolled back, and then sees
	the seeded date (or the other note) and leaves the deal alone.
	"""
	if not delivery_date_field_installed():
		return
	if note.get("reference_doctype") != DEAL_DOCTYPE or not note.get("reference_docname"):
		return
	if not note.get(FIELD_COACHING_CALL_DELIVERY_DATE):
		return

	frappe.db.get_value(DEAL_DOCTYPE, note.get("reference_docname"), "name", for_update=True)


def seed_first_call_date(note) -> str | None:
	"""Seed a Delivering Coaching deal's empty First Coaching Call Date from its first call note.

	Runs once, on insert, and only for a canonical Coaching Call Note -- one carrying a
	recognized call status and a structured Delivery Date. The date is a seed, never a mirror:
	a deal that already has a date, or that already has any other Coaching Call Note (so a user
	who cleared the date keeps it cleared), is left alone, and note edits, deletes and moves
	never come back here.

	Written with `update_modified=False` inside the note's own transaction, for the same reason
	as `reconcile_deal`: a rolled-back note takes the date with it, and the caller of Log
	Coaching Call can still save the deal it holds in memory. Returns the value written, or None.
	"""
	if not delivery_date_field_installed():
		return None

	deal_name = note.get("reference_docname")
	delivery_date = note.get(FIELD_COACHING_CALL_DELIVERY_DATE)
	if note.get("reference_doctype") != DEAL_DOCTYPE or not deal_name or not delivery_date:
		return None
	if note.get(FIELD_COACHING_CALL_STATUS) not in CALL_STATUSES:
		return None

	deal = frappe.db.get_value(
		DEAL_DOCTYPE,
		deal_name,
		["pipeline_type", FIELD_FIRST_CALL_DATE],
		as_dict=True,
		for_update=True,
	)
	if not deal or deal.get("pipeline_type") != PIPELINE_DELIVERING_COACHING:
		return None
	if deal.get(FIELD_FIRST_CALL_DATE):
		return None
	if _has_other_call_note(deal_name, note.name):
		return None

	value = first_call_value(delivery_date)
	frappe.db.set_value(DEAL_DOCTYPE, deal_name, FIELD_FIRST_CALL_DATE, value, update_modified=False)
	return value


def _has_other_call_note(deal_name: str, note_name: str) -> bool:
	"""Whether the deal already has a Coaching Call Note other than this one, by metadata or title.

	Only metadata columns this site has actually installed are queried: a site part-way through
	the migration still answers the question from note titles rather than failing on a column
	that is not there yet.
	"""
	or_filters = [["title", "like", f"%{TITLE_MARKER}%"]]
	if status_field_installed():
		or_filters.append([FIELD_COACHING_CALL_STATUS, "is", "set"])
	if delivery_date_field_installed():
		or_filters.append([FIELD_COACHING_CALL_DELIVERY_DATE, "is", "set"])

	return bool(
		frappe.get_all(
			NOTE_DOCTYPE,
			filters={
				"reference_doctype": DEAL_DOCTYPE,
				"reference_docname": deal_name,
				"name": ["!=", note_name],
			},
			or_filters=or_filters,
			limit=1,
			pluck="name",
		)
	)


def _row_status(note: dict) -> str | None:
	"""The status of one queried note row, from its metadata or its body."""
	return note_status(
		note.get("title") or "", note.get("content") or "", note.get(FIELD_COACHING_CALL_STATUS)
	)


def _stored_status_field() -> list:
	return [FIELD_COACHING_CALL_STATUS] if status_field_installed() else []


def _status_from_content(content: str) -> str | None:
	match = STATUS_LINE.search(_to_text(content or ""))
	if not match:
		return None

	value = match.group(1).strip()
	for status in CALL_STATUSES:
		if value.casefold() == status.casefold():
			return status
	return None


def _to_text(markup: str) -> str:
	"""A note's HTML body as plain text, safely: tags dropped, entities resolved.

	Nothing here is rendered or re-stored; the text exists only to read a status marker out of
	bodies written by several generations of the workflow.
	"""
	text = BLOCK_TAGS.sub("\n", markup)
	text = ANY_TAG.sub(" ", text)
	text = html.unescape(text)
	return "\n".join(re.sub(r"[^\S\n]+", " ", line).strip() for line in text.splitlines())


def _normalize(text: str) -> str:
	return " ".join(_to_text(text).split()).casefold()

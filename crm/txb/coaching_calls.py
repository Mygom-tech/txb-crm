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

import html
import re

import frappe

from crm.txb.constants import FIELD_COACHING_CALL_STATUS, PIPELINE_DELIVERING_COACHING

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

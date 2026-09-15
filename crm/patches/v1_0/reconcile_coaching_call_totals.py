"""Install the coaching call status field and repair every Delivering Coaching total (TXB-247).

Two steps, both idempotent:

1. Classify. Recognizable historical Coaching Call notes get their explicit status --
   Completed, Missed or No charge -- recorded in the app-owned
   ``FIELD_COACHING_CALL_STATUS`` field, read out of a normalized copy of the note body. A
   note whose status cannot be read exactly is left unclassified, and so counts for nothing.
   Note content is never rewritten; only the metadata beside it is filled in.

2. Reconcile. Every Delivering Coaching deal's ``total_completed_calls`` is recounted from
   its current linked notes (see ``crm.txb.coaching_calls``) and stored only when it actually
   differs -- which is what repairs both the under-counted deals (CRM-DEAL-2026-00315 sat at
   0) and the over-counted ones.

Nothing outside those two sets is touched: other pipelines keep the CRM Call Log meaning of
the field, and every write here uses ``update_modified=False``, so a repaired row keeps its
real last-edited timestamp and no record looks freshly changed to users or to reporting.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from crm.txb.coaching_calls import (
	DEAL_DOCTYPE,
	NOTE_DOCTYPE,
	TITLE_MARKER,
	TOTAL_FIELD,
	classify_note_status,
	count_completed_calls,
)
from crm.txb.constants import FIELD_COACHING_CALL_STATUS, PIPELINE_DELIVERING_COACHING

FIELD_DEFINITION = {
	"fieldname": FIELD_COACHING_CALL_STATUS,
	"fieldtype": "Data",
	"label": "Coaching Call Status",
	"description": "Call status recorded by the Log Coaching Call action. System-maintained.",
	"hidden": 1,
	"read_only": 1,
	"no_copy": 1,
	"insert_after": "content",
}


def execute():
	_install_field()
	classified = _classify_notes()
	repaired = _reconcile_deals()

	frappe.logger("txb").info(
		f"[TXB-247] Coaching call reconcile: {classified} note(s) classified, "
		f"{repaired} Delivering Coaching total(s) repaired"
	)


def _install_field():
	if frappe.get_meta(NOTE_DOCTYPE).has_field(FIELD_COACHING_CALL_STATUS):
		return

	create_custom_fields({NOTE_DOCTYPE: [FIELD_DEFINITION]})
	frappe.clear_cache(doctype=NOTE_DOCTYPE)


def _classify_notes() -> int:
	"""Record the explicit status of every recognizable deal-linked Coaching Call note."""
	notes = frappe.get_all(
		NOTE_DOCTYPE,
		filters={
			"reference_doctype": DEAL_DOCTYPE,
			"reference_docname": ["is", "set"],
			"title": ["like", f"%{TITLE_MARKER}%"],
		},
		fields=["name", "title", "content", FIELD_COACHING_CALL_STATUS],
	)

	classified = 0
	for note in notes:
		status = classify_note_status(
			note.get("title") or "", note.get("content") or "", note.get(FIELD_COACHING_CALL_STATUS)
		)
		if not status:
			continue
		frappe.db.set_value(
			NOTE_DOCTYPE, note["name"], FIELD_COACHING_CALL_STATUS, status, update_modified=False
		)
		classified += 1

	return classified


def _reconcile_deals() -> int:
	"""Recount every Delivering Coaching deal, writing only the totals that are wrong."""
	deals = frappe.get_all(
		DEAL_DOCTYPE,
		filters={"pipeline_type": PIPELINE_DELIVERING_COACHING},
		fields=["name", TOTAL_FIELD],
	)

	repaired = 0
	for deal in deals:
		total = count_completed_calls(deal["name"])
		if (deal.get(TOTAL_FIELD) or 0) == total:
			continue
		frappe.db.set_value(DEAL_DOCTYPE, deal["name"], TOTAL_FIELD, total, update_modified=False)
		repaired += 1

	return repaired

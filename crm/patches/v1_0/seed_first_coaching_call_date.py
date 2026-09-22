"""Install Coaching Call Delivery Date metadata and backfill First Coaching Call Dates (TXB-224).

Three steps, all idempotent:

1. Install. ``FIELD_COACHING_CALL_DELIVERY_DATE`` is added to FCRM Note -- hidden, read-only
   and no-copy, beside the TXB-247 call status.

2. Derive. Every deal-linked Coaching Call Note without a Delivery Date gets the one read from
   its title, and only when the title holds exactly one valid ISO date (see
   ``crm.txb.coaching_calls.title_delivery_date``). A note with no unambiguous date is left
   without one. Note content and titles are never rewritten.

3. Backfill. Each Delivering Coaching deal whose ``custom_first_call_date`` is empty takes the
   EARLIEST Delivery Date of its Coaching Call Notes, at local midnight. Earliest by date, not by
   creation order or call number: imported notes were not inserted chronologically (#119 was
   inserted before #118). A deal that already has a date keeps it.

Nothing else is touched: other pipelines, the legacy ``custom_first_coaching_date`` field, and
every row's ``modified`` timestamp -- all writes use ``update_modified=False``.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from crm.txb.coaching_calls import (
	DEAL_DOCTYPE,
	NOTE_DOCTYPE,
	TITLE_MARKER,
	first_call_value,
	title_delivery_date,
)
from crm.txb.constants import (
	FIELD_COACHING_CALL_DELIVERY_DATE,
	FIELD_COACHING_CALL_STATUS,
	FIELD_FIRST_CALL_DATE,
	PIPELINE_DELIVERING_COACHING,
)

FIELD_DEFINITION = {
	"fieldname": FIELD_COACHING_CALL_DELIVERY_DATE,
	"fieldtype": "Date",
	"label": "Coaching Call Delivery Date",
	"description": "Delivery Date recorded by the Log Coaching Call action. System-maintained.",
	"hidden": 1,
	"read_only": 1,
	"no_copy": 1,
	"insert_after": FIELD_COACHING_CALL_STATUS,
}


def execute():
	_install_field()
	derived = _derive_delivery_dates()
	seeded = _backfill_first_call_dates()

	frappe.logger("txb").info(
		f"[TXB-224] First coaching call date: {derived} note Delivery Date(s) derived, "
		f"{seeded} Delivering Coaching First Coaching Call Date(s) backfilled"
	)


def _install_field():
	if frappe.get_meta(NOTE_DOCTYPE).has_field(FIELD_COACHING_CALL_DELIVERY_DATE):
		return

	definition = dict(FIELD_DEFINITION)
	if not frappe.get_meta(NOTE_DOCTYPE).has_field(FIELD_COACHING_CALL_STATUS):
		definition["insert_after"] = "content"

	create_custom_fields({NOTE_DOCTYPE: [definition]})
	frappe.clear_cache(doctype=NOTE_DOCTYPE)


def _derive_delivery_dates() -> int:
	"""Record the unambiguous title date of every deal-linked Coaching Call Note that lacks one."""
	notes = frappe.get_all(
		NOTE_DOCTYPE,
		filters={
			"reference_doctype": DEAL_DOCTYPE,
			"reference_docname": ["is", "set"],
			"title": ["like", f"%{TITLE_MARKER}%"],
			FIELD_COACHING_CALL_DELIVERY_DATE: ["is", "not set"],
		},
		fields=["name", "title"],
	)

	derived = 0
	for note in notes:
		delivery_date = title_delivery_date(note.get("title"))
		if not delivery_date:
			continue
		frappe.db.set_value(
			NOTE_DOCTYPE,
			note["name"],
			FIELD_COACHING_CALL_DELIVERY_DATE,
			delivery_date,
			update_modified=False,
		)
		derived += 1

	return derived


def _backfill_first_call_dates() -> int:
	"""Fill only empty Delivering Coaching First Coaching Call Dates, from the earliest call."""
	deals = frappe.get_all(
		DEAL_DOCTYPE,
		filters={
			"pipeline_type": PIPELINE_DELIVERING_COACHING,
			FIELD_FIRST_CALL_DATE: ["is", "not set"],
		},
		pluck="name",
	)

	seeded = 0
	for deal_name in deals:
		earliest = frappe.get_all(
			NOTE_DOCTYPE,
			filters={
				"reference_doctype": DEAL_DOCTYPE,
				"reference_docname": deal_name,
				FIELD_COACHING_CALL_DELIVERY_DATE: ["is", "set"],
			},
			fields=[FIELD_COACHING_CALL_DELIVERY_DATE],
			order_by=f"{FIELD_COACHING_CALL_DELIVERY_DATE} asc",
			limit=1,
			pluck=FIELD_COACHING_CALL_DELIVERY_DATE,
		)
		if not earliest:
			continue
		frappe.db.set_value(
			DEAL_DOCTYPE,
			deal_name,
			FIELD_FIRST_CALL_DATE,
			first_call_value(earliest[0]),
			update_modified=False,
		)
		seeded += 1

	return seeded

"""Make `custom_first_call_date` the First Coaching Call Date coaches actually see (TXB-261).

TXB-224 seeded the canonical field on the deal row, but the stored CRM Deal Delivery Sheet
layout still rendered the legacy `custom_first_coaching_date` -- so a coach opened the
Opportunity, saw the legacy field, and the date the first Coaching Call Note had just written
was nowhere on screen. On sites where the TXB-260 patch had not landed, the note metadata the
seeding hook guards on was missing too, so nothing was written in the first place.

Four steps, all idempotent and all safe to re-run:

1. Install. Both Coaching Call Note metadata fields -- the TXB-247 call status and the TXB-224
   Delivery Date -- are ensured on FCRM Note, so a site that missed either patch stops
   standing down from seeding.

2. Render. Inside the stored ``CRM Deal-Data Fields`` layout, the legacy fieldname is replaced
   in place by ``FIELD_FIRST_CALL_DATE``, at exactly the position it held. Nothing else about
   the layout moves: the Delivery Sheet section, its neighbouring Last Coaching Call Date, and
   the layout's edit permissions are untouched. A layout already carrying the canonical field
   simply drops the legacy duplicate.

3. Migrate. A deal whose canonical First is EMPTY takes the value from the legacy field, so a
   date entered under the old rendering is not lost. A deal that already has a canonical value
   -- seeded or typed by hand -- keeps it; the legacy value is never written over it.

4. Backfill. The TXB-224 derive-and-backfill runs again, now that the metadata it needs is
   guaranteed installed.

The legacy custom field itself is deliberately left installed and populated; only its place in
the layout is given up. Every write uses ``update_modified=False``.
"""

import json

import frappe

from crm.patches.v1_0.reconcile_coaching_call_totals import (
	_install_field as _install_status_field,
)
from crm.patches.v1_0.seed_first_coaching_call_date import (
	_backfill_first_call_dates,
	_derive_delivery_dates,
	_install_field as _install_delivery_date_field,
)
from crm.txb.coaching_calls import DEAL_DOCTYPE
from crm.txb.constants import FIELD_FIRST_CALL_DATE

# The Opportunity layout the Delivery Sheet lives in, and the field it used to render there.
DEAL_LAYOUT = "CRM Deal-Data Fields"
LEGACY_FIRST_FIELD = "custom_first_coaching_date"


def execute():
	_install_status_field()
	_install_delivery_date_field()

	rendered = _render_canonical_first_field()
	migrated = _migrate_legacy_first_dates()
	derived = _derive_delivery_dates()
	seeded = _backfill_first_call_dates()

	frappe.logger("txb").info(
		f"[TXB-261] Canonical First Coaching Call Date: layout repaired={rendered}, "
		f"{migrated} legacy date(s) migrated, {derived} note Delivery Date(s) derived, "
		f"{seeded} First Coaching Call Date(s) backfilled"
	)


def _render_canonical_first_field() -> bool:
	"""Swap the legacy fieldname for the canonical one inside the stored Deal layout."""
	if not frappe.db.exists("CRM Fields Layout", DEAL_LAYOUT):
		return False
	if not frappe.get_meta(DEAL_DOCTYPE).has_field(FIELD_FIRST_CALL_DATE):
		return False

	doc = frappe.get_doc("CRM Fields Layout", DEAL_LAYOUT)
	try:
		layout = json.loads(doc.layout or "[]")
	except (ValueError, TypeError):
		return False

	changed = False
	for column in _iter_columns(layout):
		fields = column.get("fields")
		if not fields or LEGACY_FIRST_FIELD not in fields:
			continue

		if FIELD_FIRST_CALL_DATE in fields:
			# The canonical field is already rendered elsewhere in this column: the legacy
			# entry is a duplicate, so it just goes.
			fields.remove(LEGACY_FIRST_FIELD)
		else:
			fields[fields.index(LEGACY_FIRST_FIELD)] = FIELD_FIRST_CALL_DATE
		changed = True

	if not changed:
		return False

	doc.layout = json.dumps(layout)
	doc.save()
	return True


def _iter_columns(layout):
	"""Every column dict in a layout tree, including the columns of nested tab sections."""
	for section in layout:
		yield from section.get("columns", [])
		for nested in section.get("sections", []):
			yield from nested.get("columns", [])


def _migrate_legacy_first_dates() -> int:
	"""Copy a legacy First value onto the canonical field, only where the canonical one is empty."""
	if not frappe.get_meta(DEAL_DOCTYPE).has_field(LEGACY_FIRST_FIELD):
		return 0
	if not frappe.get_meta(DEAL_DOCTYPE).has_field(FIELD_FIRST_CALL_DATE):
		return 0

	deals = frappe.get_all(
		DEAL_DOCTYPE,
		filters={
			LEGACY_FIRST_FIELD: ["is", "set"],
			FIELD_FIRST_CALL_DATE: ["is", "not set"],
		},
		fields=["name", LEGACY_FIRST_FIELD],
	)

	for deal in deals:
		frappe.db.set_value(
			DEAL_DOCTYPE,
			deal["name"],
			FIELD_FIRST_CALL_DATE,
			deal[LEGACY_FIRST_FIELD],
			update_modified=False,
		)

	return len(deals)

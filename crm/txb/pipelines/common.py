"""Effects shared by pipeline actions.

Handlers mutate the deal in place; the caller saves once, so a failure part-way leaves
nothing half-applied. Notes and tasks are inserted immediately, which matches the previous
behaviour and keeps them inside the caller's transaction.
"""

import frappe
from frappe.utils import nowdate

from crm.txb.constants import (
	FIELD_DELIVERY_DEAL,
	FIELD_SALES_SOURCE_DEAL,
	PIPELINE_DELIVERING_COACHING,
)

DEAL_DOCTYPE = "CRM Deal"

# The savepoint the handover insert rolls back to when a concurrent Won wins the race for
# the same source. Rolling back only to here keeps the caller's action transaction usable.
HANDOVER_SAVEPOINT = "txb_coaching_handover"
NOTE_DOCTYPE = "FCRM Note"
TASK_DOCTYPE = "CRM Task"
TASK_BACKLOG = "Backlog"

YES_NO = "Yes\nNo"


def add_note(deal, title: str, content: str):
	"""Attach a note. Titles carry the date, matching the previous behaviour."""
	frappe.get_doc(
		{
			"doctype": NOTE_DOCTYPE,
			"reference_doctype": DEAL_DOCTYPE,
			"reference_docname": deal.name,
			"title": f"{title} - {nowdate()}",
			"content": content,
		}
	).insert(ignore_permissions=True)


def add_task(deal, title: str, description: str, assigned_to=None, due_date=None, priority="Medium"):
	frappe.get_doc(
		{
			"doctype": TASK_DOCTYPE,
			"title": f"{title} - {deal.organization or deal.name}",
			"description": description,
			"assigned_to": assigned_to or "",
			"due_date": due_date or None,
			"priority": priority,
			"reference_doctype": DEAL_DOCTYPE,
			"reference_docname": deal.name,
			"status": TASK_BACKLOG,
		}
	).insert(ignore_permissions=True)


def lines(*parts) -> str:
	"""Join the truthy parts into a note body."""
	return "\n".join(part for part in parts if part)


def apply_deal_fields(deal, action: dict, data: dict):
	"""Copy answers onto the deal using each field's `deal_field`.

	The wizard expressed this as a `dealField` on the field definition and looped over it
	in every handler. Declaring it on the field keeps the mapping visible in the table and
	removes the same loop from eight places.

	Empty answers are skipped so an untouched optional field never blanks existing data.
	"""
	for field in action.get("fields", []):
		target = field.get("deal_field")
		if not target:
			continue

		value = data.get(field["fieldname"])
		if value in (None, ""):
			continue

		deal.set(target, value)


def date_part(value: str | None) -> str | None:
	"""The date half of a datetime answer, however the client formatted it."""
	if not value:
		return None
	return str(value).split(" ")[0].split("T")[0]


def mark_lost(deal, reason: str, detail: str = ""):
	"""Lost is a status like any other, but it carries a reason the CRM displays."""
	deal.status = "Lost"
	deal.lost_reason = reason
	if deal.meta.has_field("lost_reason_detail"):
		deal.lost_reason_detail = detail or ""


def create_coaching_deal(source, notes: str = ""):
	"""Hand a Won sales Opportunity over to delivery, idempotently.

	Exactly one aggregate Delivering Coaching deal exists per source Opportunity: in
	Submitted, carrying the source's Organization, Contacts (with their primary flags),
	owner and a textual source note. The two deals are cross-linked with dedicated
	app-managed fields (FIELD_SALES_SOURCE_DEAL on the delivery deal, FIELD_DELIVERY_DEAL
	on the source) so a user can navigate delivery -> sales source and source -> delivery.

	Retry-safe. A repeated Won -- a double click, a retried request, a concurrent writer --
	must never spawn a second delivery deal:

	- the ordinary case is caught by querying the unique sales-source link before inserting;
	- a lost duplicate-key race is caught by FIELD_SALES_SOURCE_DEAL's database uniqueness,
	  which turns the second insert into a UniqueValidationError we recover from by reusing
	  the winner rather than surfacing the conflict.

	Either way the source's reverse link is (re)pointed at the surviving delivery deal, so a
	prior handover whose forward link committed but whose reverse link was lost self-heals on
	the next run.

	This deliberately creates a deal in a restricted pipeline. That is allowed: the status
	guard exempts inserts precisely so these handover flows keep working. Runs inside the
	caller's action transaction (crm.txb.api.actions.execute_action): the insert and both
	links commit together or not at all -- the reverse link is set in memory here and
	persisted by the caller's single save.
	"""
	target = find_coaching_deal(source.name)
	if not target:
		target = insert_coaching_deal(source, notes)

	link_source_to_delivery(source, target)
	return target


def find_coaching_deal(source_name: str) -> str | None:
	"""Name of the aggregate delivery deal already handed off from this source, if any.

	Filters on the dedicated sales-source link, so Workshop QR attendee candidates -- which
	carry `custom_source_deal` but never FIELD_SALES_SOURCE_DEAL -- are never mistaken for
	the aggregate handover.
	"""
	return frappe.db.get_value(DEAL_DOCTYPE, {FIELD_SALES_SOURCE_DEAL: source_name}, "name")


def insert_coaching_deal(source, notes: str) -> str:
	"""Insert the aggregate delivery deal, yielding to the winner if a race beats us to it."""
	origin = f"Source: {source.pipeline_type} ({source.name})"

	coaching = frappe.get_doc(
		{
			"doctype": DEAL_DOCTYPE,
			"pipeline_type": PIPELINE_DELIVERING_COACHING,
			"status": "Submitted",
			"organization": source.organization or "",
			"deal_owner": source.get("deal_owner") or "",
			FIELD_SALES_SOURCE_DEAL: source.name,
			"custom_delivery_notes": lines(origin, notes),
			"contacts": [
				{"contact": row.contact, "is_primary": row.is_primary or 0}
				for row in (source.get("contacts") or [])
			],
		}
	)

	frappe.db.savepoint(HANDOVER_SAVEPOINT)
	try:
		coaching.insert(ignore_permissions=True)
	except frappe.UniqueValidationError:
		# A concurrent Won inserted the aggregate first. Roll our failed insert back to the
		# savepoint so the caller's transaction stays usable, then reuse the winner. If the
		# conflict was on some other unique field the query finds nothing and we re-raise.
		frappe.db.rollback(save_point=HANDOVER_SAVEPOINT)
		winner = find_coaching_deal(source.name)
		if not winner:
			raise
		return winner

	return coaching.name


def link_source_to_delivery(source, target_name: str):
	"""Point the source's reverse link at the surviving delivery deal, repairing if lost.

	Set in memory; the caller's save persists it inside the same transaction. Guarded by
	has_field so a site that has not yet run the handover fields patch still completes the
	forward handover instead of failing on an absent column.
	"""
	if not source.meta.has_field(FIELD_DELIVERY_DEAL):
		return

	if source.get(FIELD_DELIVERY_DEAL) != target_name:
		source.set(FIELD_DELIVERY_DEAL, target_name)

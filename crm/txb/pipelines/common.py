"""Effects shared by pipeline actions.

Handlers mutate the deal in place; the caller saves once, so a failure part-way leaves
nothing half-applied. Notes and tasks are inserted immediately, which matches the previous
behaviour and keeps them inside the caller's transaction.
"""

import frappe
from frappe.utils import nowdate

DEAL_DOCTYPE = "CRM Deal"
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
	"""Spawn the Delivering Coaching opportunity a won deal hands over to.

	Contacts are carried across so the coach starts with the people already involved.

	Note this deliberately creates a deal in a restricted pipeline. That is allowed: the
	status guard exempts inserts precisely so these handover flows keep working.
	"""
	origin = f"Source: {source.pipeline_type} ({source.name})"

	coaching = frappe.get_doc(
		{
			"doctype": DEAL_DOCTYPE,
			"pipeline_type": "Delivering Coaching",
			"status": "Submitted",
			"organization": source.organization or "",
			"custom_delivery_notes": lines(origin, notes),
			"contacts": [
				{"contact": row.contact, "is_primary": row.is_primary or 0}
				for row in (source.get("contacts") or [])
			],
		}
	)
	coaching.insert(ignore_permissions=True)
	return coaching.name

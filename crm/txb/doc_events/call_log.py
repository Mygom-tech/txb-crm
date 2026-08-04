"""CRM Call Log document events.

Ported from `Default Call Log Phone Numbers` and the three `Update Deal Call Count`
Server Scripts. Those three were byte-identical -- one function is bound to all three
events here instead.
"""

import frappe

CALL_STATUS_COMPLETED = "Completed"
DEAL_DOCTYPE = "CRM Deal"
MISSING_NUMBER_PLACEHOLDER = "-"


def default_phone_numbers(doc, method=None):
	"""Placeholder for missing numbers; the frontend crashes on an empty from/to."""
	if not doc.get("from"):
		doc.set("from", MISSING_NUMBER_PLACEHOLDER)

	if not doc.get("to"):
		doc.set("to", MISSING_NUMBER_PLACEHOLDER)


def update_deal_call_count(doc, method=None):
	"""Recount a deal's completed calls.

	Bound to insert, update and delete: the count is derived from a query rather than
	incremented, so every event recomputes the same correct value.
	"""
	if doc.reference_doctype != DEAL_DOCTYPE or not doc.reference_docname:
		return

	count = frappe.db.count(
		"CRM Call Log",
		filters={
			"reference_doctype": DEAL_DOCTYPE,
			"reference_docname": doc.reference_docname,
			"status": CALL_STATUS_COMPLETED,
		},
	)

	frappe.db.set_value(DEAL_DOCTYPE, doc.reference_docname, "total_completed_calls", count)

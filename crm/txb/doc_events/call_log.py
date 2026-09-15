"""CRM Call Log document events.

Ported from `Default Call Log Phone Numbers` and the three `Update Deal Call Count`
Server Scripts. Those three were byte-identical -- one function is bound to all three
events here instead.
"""

import frappe

from crm.txb.constants import PIPELINE_DELIVERING_COACHING

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

	Delivering Coaching is the one pipeline where this field does not mean "CRM Call Log rows":
	there it is the count of completed coaching calls, owned by `crm.txb.coaching_calls` and
	recounted from the deal's notes. A telephony call log on such a deal would otherwise
	silently overwrite that total, so those deals are left alone here (TXB-247). Every other
	pipeline keeps this behaviour unchanged.
	"""
	if doc.reference_doctype != DEAL_DOCTYPE or not doc.reference_docname:
		return

	pipeline = frappe.db.get_value(DEAL_DOCTYPE, doc.reference_docname, "pipeline_type")
	if pipeline == PIPELINE_DELIVERING_COACHING:
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

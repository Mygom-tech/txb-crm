"""Take Action transitions for the Delivering Coaching pipeline.

Ported from the `CRM Wizard Framework` Form Script, which held this as 1355 lines of
DOM-injected JavaScript in the database. The transition table was already there in data
form -- `stages` (the states an action may start from), a target status, and an
`adminOnly` flag -- it simply had no enforcement behind it.

Every action here is admin_only except Log Coaching Call, which is the one that does not
move the status. See crm/txb/permissions.py for the rule that enforces it.
"""

from crm.txb.pipelines.common import (
	DEAL_DOCTYPE,
	NOTE_DOCTYPE,
	YES_NO,
	add_note,
	add_task,
	lines,
)

import frappe

INACTIVE_REASONS = (
	"Coaching completed",
	"Client withdrew",
	"Non-payment",
	"No response",
	"Contract expired",
	"Other",
)

CALL_STATUSES = ("Completed", "Missed", "No charge")


def move_to_review(deal, data):
	if data.get("admin_owner"):
		deal.custom_admin_owner = data["admin_owner"]

	add_note(
		deal,
		"Review Started",
		lines(
			"Moved to Waiting on Review",
			f"Admin/Owner: {data['admin_owner']}" if data.get("admin_owner") else None,
			f"Notes: {data['review_notes']}" if data.get("review_notes") else None,
		),
	)


def clear_contract(deal, data):
	deal.custom_master_agreement = data.get("master_agreement")
	deal.custom_registration_agreement = data.get("registration_agreement")
	deal.custom_payment_confirmed = data.get("payment_confirmed")
	deal.custom_test_completed = data.get("wiley_completed")
	deal.custom_wiley_results_uploaded = data.get("wiley_results")
	deal.custom_assigned_coach = data.get("assigned_coach")

	add_note(
		deal,
		"Contract Cleared",
		lines(
			f"Coach: {data.get('assigned_coach')}",
			f"Master Agreement: {data.get('master_agreement')}",
			f"Registration: {data.get('registration_agreement')}",
			f"Payment: {data.get('payment_confirmed')}",
			f"DISC Assessment: {data.get('wiley_completed')}",
			f"Results: {data.get('wiley_results')}",
		),
	)
	add_task(
		deal,
		"Schedule First Coaching Call",
		"Contract cleared. Schedule first coaching call.",
		assigned_to=data.get("assigned_coach"),
		priority="High",
	)


def set_first_call_date(deal, data):
	deal.custom_first_call_date = data.get("first_call_date")

	if data.get("call_notes"):
		add_note(
			deal,
			"First Call Scheduled",
			lines(f"First call: {data.get('first_call_date')}", data["call_notes"]),
		)


def log_coaching_call_defaults(deal) -> dict:
	"""Per-deal defaults injected into the Log Coaching Call form when it is offered.

	Completed Calls is shown read-only from the deal's canonical total, so the coach sees
	the current count while logging. An unset total reads as 0 rather than a blank the
	browser could be trusted to fill; the count is only ever advanced server-side by the
	handler below, never taken from this default.
	"""
	return {"completed_calls": deal.total_completed_calls or 0}


def log_coaching_call(deal, data):
	"""The only Delivering Coaching action that does not move the status.

	That is why coaches keep it: logging a call is their daily work and changes no state.
	"""
	if data.get("is_last_call"):
		deal.custom_last_coaching_call = "Yes"
	if data.get("next_call_date"):
		deal.custom_next_call_date = data["next_call_date"]
	if data.get("delivery_date"):
		deal.custom_last_coaching_call_date = data["delivery_date"]

	if data.get("call_status") == "Completed":
		deal.total_completed_calls = (deal.total_completed_calls or 0) + 1

	call_number = (
		frappe.db.count(
			NOTE_DOCTYPE,
			{
				"reference_doctype": DEAL_DOCTYPE,
				"reference_docname": deal.name,
				"title": ["like", "Coaching Call%"],
			},
		)
		+ 1
	)

	body = lines(
		f"Date: {data.get('delivery_date')}",
		f"Call Status: {data.get('call_status')}",
		f"Topic: {data['topic']}" if data.get("topic") else None,
		data.get("call_notes"),
		f"Next call: {data['next_call_date']}" if data.get("next_call_date") else None,
		"--- LAST COACHING CALL ---" if data.get("is_last_call") else None,
	)

	add_note(deal, f"Coaching Call #{call_number}", body.replace("\n", "<br>"))

	if data.get("next_call_date") and not data.get("is_last_call"):
		add_task(
			deal,
			"Next Coaching Call",
			"Follow up for next coaching call",
			assigned_to=deal.custom_assigned_coach,
		)


def put_on_hold(deal, data):
	deal.custom_hold_reason = data.get("hold_reason")
	if data.get("review_date"):
		deal.custom_review_date = data["review_date"]

	add_note(
		deal,
		"Put on Hold",
		lines(
			f"Reason: {data.get('hold_reason')}",
			f"Review date: {data['review_date']}" if data.get("review_date") else None,
		),
	)

	if data.get("review_date") and data.get("task_owner"):
		add_task(
			deal,
			"Review Hold Status",
			f"Review hold status. Reason: {data.get('hold_reason')}",
			assigned_to=data["task_owner"],
			due_date=getdate(data["review_date"]),
		)


def put_on_payment_hold(deal, data):
	deal.custom_payment_hold_reason = data.get("payment_hold_reason")
	if data.get("review_date"):
		deal.custom_review_date = data["review_date"]

	add_note(
		deal,
		"Payment Hold",
		lines(
			f"Reason: {data.get('payment_hold_reason')}",
			f"Review date: {data['review_date']}" if data.get("review_date") else None,
		),
	)

	if data.get("review_date") and data.get("task_owner"):
		add_task(
			deal,
			"Review Payment Hold",
			f"Review payment hold. Reason: {data.get('payment_hold_reason')}",
			assigned_to=data["task_owner"],
			due_date=getdate(data["review_date"]),
			priority="High",
		)


def mark_inactive(deal, data):
	detail = data.get("inactive_reason") or ""
	if data.get("inactive_notes"):
		detail = f"{detail} - {data['inactive_notes']}"

	deal.custom_inactive_reason = detail
	add_note(deal, "Marked Inactive", f"Reason: {detail}")


def reactivate(deal, data):
	deal.custom_hold_reason = ""
	deal.custom_payment_hold_reason = ""

	if data.get("reactivation_notes"):
		add_note(deal, "Reactivated", data["reactivation_notes"])


# --------------------------------------------------------------------------------------
# The transition table
# --------------------------------------------------------------------------------------

DELIVERING_COACHING_ACTIONS = (
	{
		"name": "move_to_review",
		"label": "Move to Waiting on Review",
		"from_states": ["Submitted"],
		"to_state": "Waiting on Review",
		"changes_status": True,
		"admin_only": True,
		"handler": move_to_review,
		"fields": [
			{"fieldname": "source_info", "label": "Source / Delivery Notes", "fieldtype": "Small Text"},
			{"fieldname": "admin_owner", "label": "Admin / Owner", "fieldtype": "Link", "options": "User"},
			{"fieldname": "review_notes", "label": "Notes", "fieldtype": "Small Text"},
		],
	},
	{
		"name": "clear_contract",
		"label": "Clear Contract",
		"from_states": ["Waiting on Review"],
		"to_state": "Contract Cleared",
		"changes_status": True,
		"admin_only": True,
		"handler": clear_contract,
		"fields": [
			{"fieldname": "master_agreement", "label": "Master Agreement Uploaded", "fieldtype": "Select", "options": YES_NO, "reqd": 1},
			{"fieldname": "registration_agreement", "label": "Registration Agreement Uploaded", "fieldtype": "Select", "options": YES_NO, "reqd": 1},
			{"fieldname": "payment_confirmed", "label": "Payment Confirmed", "fieldtype": "Select", "options": YES_NO, "reqd": 1},
			{"fieldname": "wiley_completed", "label": "DISC Assessment Completed", "fieldtype": "Select", "options": YES_NO, "reqd": 1},
			{"fieldname": "wiley_results", "label": "DISC Results Uploaded", "fieldtype": "Select", "options": YES_NO, "reqd": 1},
			{"fieldname": "assigned_coach", "label": "Assigned Coach", "fieldtype": "Link", "options": "User", "reqd": 1},
		],
	},
	{
		"name": "set_first_call_date",
		"label": "Set First Call Date",
		"from_states": ["Contract Cleared"],
		"to_state": "Active",
		"changes_status": True,
		"admin_only": True,
		"handler": set_first_call_date,
		"fields": [
			{"fieldname": "first_call_date", "label": "First Coaching Call Date & Time", "fieldtype": "Datetime", "reqd": 1},
			{"fieldname": "call_notes", "label": "Notes", "fieldtype": "Small Text"},
		],
	},
	{
		"name": "log_coaching_call",
		"label": "Log Coaching Call",
		"from_states": ["Active"],
		"to_state": None,
		"changes_status": False,
		"admin_only": False,
		"handler": log_coaching_call,
		"field_defaults": log_coaching_call_defaults,
		"fields": [
			{"fieldname": "call_status", "label": "Call Status", "fieldtype": "Select", "options": "\n".join(CALL_STATUSES), "reqd": 1},
			{"fieldname": "delivery_date", "label": "Coaching Call Delivery Date", "fieldtype": "Date", "reqd": 1, "default": "Today"},
			{"fieldname": "completed_calls", "label": "Completed Calls", "fieldtype": "Int", "read_only": 1, "default": 0},
			{"fieldname": "topic", "label": "Topic", "fieldtype": "Data", "reqd": 1},
			{"fieldname": "call_notes", "label": "Coaching Call Notes", "fieldtype": "Small Text", "reqd": 1},
			{"fieldname": "is_last_call", "label": "This is the last coaching call", "fieldtype": "Check"},
			{"fieldname": "next_call_date", "label": "Next Coaching Call Date", "fieldtype": "Datetime"},
		],
	},
	{
		"name": "put_on_hold",
		"label": "Put on Hold",
		"from_states": ["Active"],
		"to_state": "On Hold",
		"changes_status": True,
		"admin_only": True,
		"handler": put_on_hold,
		"fields": [
			{"fieldname": "hold_reason", "label": "Hold Reason", "fieldtype": "Small Text", "reqd": 1},
			{"fieldname": "review_date", "label": "Next Review Date", "fieldtype": "Date"},
			{"fieldname": "task_owner", "label": "Task Owner", "fieldtype": "Link", "options": "User"},
		],
	},
	{
		"name": "put_on_payment_hold",
		"label": "Put on Payment Hold",
		"from_states": ["Active"],
		"to_state": "Payment Hold",
		"changes_status": True,
		"admin_only": True,
		"handler": put_on_payment_hold,
		"fields": [
			{"fieldname": "payment_hold_reason", "label": "Payment Hold Reason", "fieldtype": "Small Text", "reqd": 1},
			{"fieldname": "review_date", "label": "Next Review Date", "fieldtype": "Date"},
			{"fieldname": "task_owner", "label": "Task Owner", "fieldtype": "Link", "options": "User"},
		],
	},
	{
		"name": "mark_inactive",
		"label": "Mark Inactive",
		"from_states": ["Active", "On Hold", "Payment Hold"],
		"to_state": "Inactive",
		"changes_status": True,
		"admin_only": True,
		"handler": mark_inactive,
		"fields": [
			{"fieldname": "inactive_reason", "label": "Inactive Reason", "fieldtype": "Select", "options": "\n".join(INACTIVE_REASONS), "reqd": 1},
			{"fieldname": "inactive_notes", "label": "Additional Notes", "fieldtype": "Small Text"},
		],
	},
	{
		"name": "reactivate",
		"label": "Reactivate",
		"from_states": ["On Hold", "Payment Hold", "Inactive"],
		"to_state": "Active",
		"changes_status": True,
		"admin_only": True,
		"handler": reactivate,
		"fields": [
			{"fieldname": "reactivation_notes", "label": "Reactivation Notes", "fieldtype": "Small Text"},
		],
	},
)

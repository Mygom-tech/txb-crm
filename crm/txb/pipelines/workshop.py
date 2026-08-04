"""Take Action transitions for the Workshop pipeline.

Ported from the `CRM Wizard Framework` Form Script.

Two shapes appear here that Delivering Coaching did not have:

- **Branching transitions.** One action can end in several states depending on an answer,
  expressed as `to_state_map` so the possible destinations stay visible in the table.
- **Conditional fields.** The wizard's `showIf` becomes Frappe's native `depends_on`,
  which FieldLayout already evaluates -- so the multi-page wizards collapse into a single
  dialog whose later fields appear once the relevant answer is given.
"""

from crm.txb.pipelines.common import (
	YES_NO,
	add_note,
	add_task,
	apply_deal_fields,
	create_coaching_deal,
	date_part,
	lines,
	mark_lost,
)

NOT_INTERESTED_REASONS = (
	"Prospect declined",
	"No response after follow-up",
	"Budget / timing mismatch",
	"Went with competitor",
	"Other",
)

WORKSHOP_OUTCOMES = (
	"Won - proceed to coaching",
	"Follow-up needed",
	"Lost",
)

RESCHEDULE_HAS_DATE = "Yes, I have a new date"
RESCHEDULE_NEEDS_FOLLOWUP = "No, need to follow up"


# --------------------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------------------


def set_vcs_call(deal, data):
	apply_deal_fields(deal, SET_VCS_CALL, data)


def run_vcs_call(deal, data):
	apply_deal_fields(deal, RUN_VCS_CALL, data)

	add_note(
		deal,
		"VCS Call Run",
		lines(
			data.get("vcs_notes"),
			f"Financial support decided: {data['financial_support']}"
			if data.get("financial_support")
			else None,
			f"Financial notes: {data['financial_notes']}" if data.get("financial_notes") else None,
			f"Workshop date confirmed: {data['confirmed_ws_date']}"
			if data.get("ws_confirmed") == "Yes" and data.get("confirmed_ws_date")
			else None,
			f"Notes: {data['additional_notes']}" if data.get("additional_notes") else None,
		),
	)


def set_workshop(deal, data):
	apply_deal_fields(deal, SET_WORKSHOP, data)
	deal.custom_workshop_date_confirmed = "Yes"

	if data.get("ws_datetime"):
		deal.workshop_date = date_part(data["ws_datetime"])


def run_workshop(deal, data):
	apply_deal_fields(deal, RUN_WORKSHOP, data)
	deal.workshop_participants = data.get("ws_participants") or 0

	if data.get("ws_outcome") == "Lost":
		mark_lost(deal, "Workshop Cancelled", data.get("ws_notes") or "")

	add_note(
		deal,
		"Workshop Run",
		lines(
			data.get("ws_notes"),
			f"Participants: {data.get('ws_participants') or 0}",
			f"Outcome: {data.get('ws_outcome')}",
			data.get("ws_additional"),
		),
	)

	if data.get("ws_outcome") == "Won - proceed to coaching":
		add_task(
			deal,
			"Send contract approval email",
			f"Workshop deal {deal.name} has been won. "
			"Please send the contract approval email to the client.",
			assigned_to="Administrator",
			priority="High",
		)


def workshop_won(deal, data):
	if data.get("won_notes"):
		add_note(deal, "Won", data["won_notes"])

	create_coaching_deal(deal, data.get("coaching_notes") or "")

	add_task(
		deal,
		"Send contract approval email",
		f"Workshop deal {deal.name} has been won. "
		"Please send the contract approval email to the client.",
		assigned_to="Administrator",
		priority="High",
	)


def reschedule_workshop(deal, data):
	"""Either a new date is known, or a follow-up task is raised to find one."""
	note = [f"Reason: {data.get('reschedule_reason')}"]

	if data.get("reschedule_type") == RESCHEDULE_HAS_DATE:
		deal.custom_workshop_scheduled_at = data.get("new_datetime")
		deal.workshop_date = date_part(data.get("new_datetime"))
		note.append(f"New date: {data.get('new_datetime')}")
		if data.get("reschedule_notes"):
			note.append(f"Notes: {data['reschedule_notes']}")
	else:
		note.append("Status changed to Workshop rescheduling in progress")
		if data.get("followup_notes"):
			note.append(f"Notes: {data['followup_notes']}")

		add_task(
			deal,
			"Follow up on workshop reschedule",
			lines(data.get("reschedule_reason"), data.get("followup_notes")),
			assigned_to=data.get("followup_owner"),
			due_date=data.get("followup_due_date"),
			priority="High",
		)

	add_note(deal, "Rescheduled", "\n".join(note))


def cancel_workshop(deal, data):
	mark_lost(deal, "Workshop Cancelled", data.get("cancellation_notes") or "")

	if data.get("schedule_followup") and data.get("followup_type") == "Task":
		add_task(
			deal,
			data.get("followup_subject") or "Follow-Up",
			data.get("followup_notes") or "",
			assigned_to=data.get("followup_owner"),
			due_date=data.get("followup_date"),
		)


def workshop_not_interested(deal, data):
	detail = data.get("ni_reason") or ""
	if data.get("ni_notes"):
		detail = f"{detail} - {data['ni_notes']}"

	mark_lost(deal, "Not Interested", detail)

	add_note(
		deal,
		"Marked Not Interested",
		lines(f"Reason: {data.get('ni_reason')}", data.get("ni_notes")),
	)

	if (
		data.get("ni_reason") == "Other"
		and data.get("ni_followup") == "Yes"
		and data.get("ni_followup_date")
	):
		add_task(
			deal,
			"Follow up after Not Interested",
			lines("Reason: Other", data.get("ni_notes")),
			due_date=data["ni_followup_date"],
		)


# --------------------------------------------------------------------------------------
# Transitions
# --------------------------------------------------------------------------------------

SET_VCS_CALL = {
	"name": "set_vcs_call",
	"label": "Set VCS Call",
	"from_states": ["Workshop submitted"],
	"to_state": "VCS call set",
	"changes_status": True,
	"admin_only": False,
	"handler": set_vcs_call,
	"fields": [
		{"fieldname": "vcs_datetime", "label": "VCS Call Date & Time", "fieldtype": "Datetime", "reqd": 1, "deal_field": "custom_vcs_call_datetime"},
		{"fieldname": "potential_ws_date", "label": "Potential Workshop Date (optional)", "fieldtype": "Date", "deal_field": "workshop_date"},
		{"fieldname": "vcs_notes", "label": "Notes", "fieldtype": "Small Text", "deal_field": "custom_vcs_notes"},
	],
}

RUN_VCS_CALL = {
	"name": "run_vcs_call",
	"label": "Run VCS Call",
	"from_states": ["VCS call set"],
	"to_state": None,
	# A confirmed date jumps straight to a scheduled workshop; otherwise the call is
	# simply recorded as run.
	"to_state_map": {"ws_confirmed": {"Yes": "Workshop set", "No": "VCS call run"}},
	"changes_status": True,
	"admin_only": False,
	"handler": run_vcs_call,
	"fields": [
		{"fieldname": "vcs_notes", "label": "VCS Call Notes", "fieldtype": "Small Text", "reqd": 1},
		{"fieldname": "financial_support", "label": "Was Financial Support Decided?", "fieldtype": "Select", "options": YES_NO, "deal_field": "custom_financial_support_decided"},
		{"fieldname": "financial_notes", "label": "Financial Support Notes", "fieldtype": "Small Text", "deal_field": "custom_financial_support_notes"},
		{"fieldname": "funding_manager", "label": "Funding Manager", "fieldtype": "Link", "options": "Contact", "deal_field": "custom_funding_manager_link"},
		{"fieldname": "ws_confirmed", "label": "Is Workshop Date Confirmed?", "fieldtype": "Select", "options": YES_NO, "reqd": 1, "deal_field": "custom_workshop_date_confirmed"},
		{"fieldname": "confirmed_ws_date", "label": "Confirmed Workshop Date", "fieldtype": "Date", "deal_field": "workshop_date", "depends_on": "eval:doc.ws_confirmed=='Yes'"},
		{"fieldname": "additional_notes", "label": "Additional Notes", "fieldtype": "Small Text"},
	],
}

SET_WORKSHOP = {
	"name": "set_workshop",
	"label": "Set Workshop",
	"from_states": ["VCS call run", "Workshop rescheduling in progress"],
	"to_state": "Workshop set",
	"changes_status": True,
	"admin_only": False,
	"handler": set_workshop,
	"fields": [
		{"fieldname": "ws_datetime", "label": "Workshop Date & Time", "fieldtype": "Datetime", "reqd": 1, "deal_field": "custom_workshop_scheduled_at"},
		{"fieldname": "ws_name", "label": "Workshop Name", "fieldtype": "Data", "deal_field": "workshop_name"},
		{"fieldname": "ws_notes", "label": "Notes", "fieldtype": "Small Text", "deal_field": "custom_vcs_notes"},
	],
}

RUN_WORKSHOP = {
	"name": "run_workshop",
	"label": "Run Workshop",
	"from_states": ["Workshop set"],
	"to_state": None,
	"to_state_map": {
		"ws_outcome": {
			"Won - proceed to coaching": "Workshop ran",
			"Follow-up needed": "Workshop rescheduling in progress",
			# Declared so the graph knows this branch exists; mark_lost still writes
			# the reason alongside it.
			"Lost": "Lost",
		}
	},
	"changes_status": True,
	"admin_only": False,
	"handler": run_workshop,
	"fields": [
		{"fieldname": "ws_notes", "label": "Workshop Notes", "fieldtype": "Small Text", "reqd": 1},
		{"fieldname": "ws_participants", "label": "Number of Participants", "fieldtype": "Int", "reqd": 1, "deal_field": "workshop_participants"},
		{"fieldname": "ws_outcome", "label": "Outcome", "fieldtype": "Select", "options": "\n".join(WORKSHOP_OUTCOMES), "reqd": 1},
		{"fieldname": "ws_additional", "label": "Additional Notes", "fieldtype": "Small Text"},
	],
}

WORKSHOP_WON = {
	"name": "workshop_won",
	"label": "Won",
	"from_states": ["Workshop ran"],
	"to_state": "Sold",
	"changes_status": True,
	"admin_only": False,
	"handler": workshop_won,
	"fields": [
		{"fieldname": "won_notes", "label": "Notes (optional)", "fieldtype": "Small Text"},
		{"fieldname": "coaching_notes", "label": "Handover Notes", "fieldtype": "Small Text", "description": "A Delivering Coaching opportunity will be created automatically."},
	],
}

RESCHEDULE_WORKSHOP = {
	"name": "reschedule_workshop",
	"label": "Reschedule",
	"from_states": ["VCS call set", "Workshop set"],
	"to_state": None,
	# Only the "no date yet" branch moves the deal; a known new date just reschedules it.
	"to_state_map": {
		"reschedule_type": {RESCHEDULE_NEEDS_FOLLOWUP: "Workshop rescheduling in progress"}
	},
	"changes_status": True,
	"admin_only": False,
	"handler": reschedule_workshop,
	"fields": [
		{"fieldname": "reschedule_type", "label": "Do you have a new date?", "fieldtype": "Select", "options": f"{RESCHEDULE_HAS_DATE}\n{RESCHEDULE_NEEDS_FOLLOWUP}", "reqd": 1},
		{"fieldname": "reschedule_reason", "label": "Reason for Rescheduling", "fieldtype": "Small Text", "reqd": 1},
		{"fieldname": "new_datetime", "label": "New Date & Time", "fieldtype": "Datetime", "depends_on": f"eval:doc.reschedule_type=='{RESCHEDULE_HAS_DATE}'"},
		{"fieldname": "reschedule_notes", "label": "Additional Notes", "fieldtype": "Small Text", "depends_on": f"eval:doc.reschedule_type=='{RESCHEDULE_HAS_DATE}'"},
		{"fieldname": "followup_due_date", "label": "Follow-Up Due Date", "fieldtype": "Date", "depends_on": f"eval:doc.reschedule_type=='{RESCHEDULE_NEEDS_FOLLOWUP}'"},
		{"fieldname": "followup_owner", "label": "Assign To", "fieldtype": "Link", "options": "User", "depends_on": f"eval:doc.reschedule_type=='{RESCHEDULE_NEEDS_FOLLOWUP}'"},
		{"fieldname": "followup_notes", "label": "Follow-Up Notes", "fieldtype": "Small Text", "depends_on": f"eval:doc.reschedule_type=='{RESCHEDULE_NEEDS_FOLLOWUP}'"},
	],
}

CANCEL_WORKSHOP = {
	"name": "cancel_workshop",
	"label": "Cancel Workshop",
	"from_states": [],  # available from any status, as before
	# Declared, not left to the handler: an invisible target cannot be enforced by the
	# transition graph. `cancel_workshop` still sets the reason via mark_lost, and
	# execute_action then assigns the same value -- idempotent.
	"to_state": "Lost",
	"changes_status": True,
	"admin_only": False,
	"handler": cancel_workshop,
	"fields": [
		{"fieldname": "cancellation_notes", "label": "Cancellation Notes", "fieldtype": "Small Text"},
		{"fieldname": "schedule_followup", "label": "Schedule a follow-up?", "fieldtype": "Check"},
		{"fieldname": "followup_type", "label": "Follow-Up Type", "fieldtype": "Select", "options": "Task\nEvent on Calendar", "depends_on": "eval:doc.schedule_followup"},
		{"fieldname": "followup_owner", "label": "Assign To", "fieldtype": "Link", "options": "User", "depends_on": "eval:doc.schedule_followup"},
		{"fieldname": "followup_subject", "label": "Subject", "fieldtype": "Data", "default": "Follow-Up on Cancelled Workshop", "depends_on": "eval:doc.schedule_followup"},
		{"fieldname": "followup_date", "label": "Date", "fieldtype": "Date", "depends_on": "eval:doc.schedule_followup"},
		{"fieldname": "followup_notes", "label": "Notes", "fieldtype": "Small Text", "depends_on": "eval:doc.schedule_followup"},
	],
}

WORKSHOP_NOT_INTERESTED = {
	"name": "workshop_not_interested",
	"label": 'Mark as "Not Interested"',
	"from_states": [],
	# Declared, not left to the handler: an invisible target cannot be enforced by the
	# transition graph. `workshop_not_interested` still sets the reason via mark_lost, and
	# execute_action then assigns the same value -- idempotent.
	"to_state": "Lost",
	"changes_status": True,
	"admin_only": False,
	"handler": workshop_not_interested,
	"fields": [
		{"fieldname": "ni_reason", "label": "Reason", "fieldtype": "Select", "options": "\n".join(NOT_INTERESTED_REASONS), "reqd": 1},
		{"fieldname": "ni_notes", "label": "Additional Notes", "fieldtype": "Small Text"},
		{"fieldname": "ni_followup", "label": "Are you going to follow up?", "fieldtype": "Select", "options": YES_NO, "depends_on": "eval:doc.ni_reason=='Other'"},
		{"fieldname": "ni_followup_date", "label": "Follow-Up Date", "fieldtype": "Date", "depends_on": "eval:doc.ni_reason=='Other' && doc.ni_followup=='Yes'"},
	],
}

WORKSHOP_ACTIONS = (
	SET_VCS_CALL,
	RUN_VCS_CALL,
	SET_WORKSHOP,
	RUN_WORKSHOP,
	WORKSHOP_WON,
	RESCHEDULE_WORKSHOP,
	CANCEL_WORKSHOP,
	WORKSHOP_NOT_INTERESTED,
)

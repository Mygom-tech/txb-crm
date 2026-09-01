"""Take Action transitions for the Individual Session pipeline.

Ported from the `CRM Wizard Framework` Form Script.

"BAP" is the business assessment session this pipeline is built around. A won session
hands over to Delivering Coaching, which is why `create_coaching_deal` lives in `common`
-- Workshop does the same thing.

`Add an Attendee (TBD)` is deliberately absent: it carried `config: null` and rendered as
a dead menu entry, so it was dropped rather than ported as a no-op.
"""

from crm.txb.pipelines.common import (
	add_handover_note,
	add_note,
	add_task,
	apply_deal_fields,
	cancel_deal_meeting,
	create_coaching_deal,
	lines,
	mark_lost,
	set_deal_meeting,
)

# TXB-209 meeting-flow key. Booking, rescheduling and cancelling all act on the one BAP session
# Event, so a reschedule moves it and a cancel closes it rather than creating a second.
MEETING_FLOW_BAP = "bap"

BAP_TYPES = ("Sales", "Leadership")
LOCATION_TYPES = ("On-Site", "Virtual")

SESSION_OUTCOMES = (
	"Won - proceed to coaching",
	"Follow-up needed",
	"Not interested",
)

CANCEL_REASONS = (
	"Client requested cancellation",
	"Scheduling conflict",
	"No-show",
	"Internal decision",
	"Other",
)

NOT_INTERESTED_REASONS = (
	"Prospect declined",
	"No response after follow-up",
	"Budget / timing mismatch",
	"Went with competitor",
	"Other",
)

RESCHEDULE_HAS_DATE = "Yes, I have a new date"
RESCHEDULE_NEEDS_FOLLOWUP = "No, need to follow up"


# --------------------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------------------


def bap_address(*parts) -> str:
	"""Join the supplied On-Site address parts (street, city, country), dropping the blanks."""
	return ", ".join(str(part).strip() for part in parts if part and str(part).strip())


def book_bap(deal, data):
	apply_deal_fields(deal, BOOK_BAP, data)

	set_deal_meeting(
		deal,
		flow=MEETING_FLOW_BAP,
		subject="BAP Session",
		starts_on=data.get("bap_datetime"),
		meeting_type=data.get("bap_location_type"),
		address=bap_address(data.get("bap_street"), data.get("bap_city"), data.get("bap_country")),
	)


def run_bap(deal, data):
	"""A won session both closes this deal and opens the coaching one."""
	won = data.get("outcome") == "Won - proceed to coaching"

	add_note(
		deal,
		"BAP Won" if won else "BAP Session Run",
		lines(
			f"Outcome: {data.get('outcome')}",
			data.get("session_notes"),
			f"Additional notes: {data['additional_notes']}" if data.get("additional_notes") else None,
		),
	)

	if won:
		create_coaching_deal(deal)


def reschedule_bap(deal, data):
	"""Either a new date is known, or a follow-up task is raised to find one."""
	note = [f"Reason: {data.get('reschedule_reason')}"]

	if data.get("reschedule_type") == RESCHEDULE_HAS_DATE:
		deal.custom_bap_datetime = data.get("new_bap_datetime")
		if data.get("new_location_type"):
			deal.custom_bap_location_type = data["new_location_type"]

		# A known new date moves the same BAP Event, preserving its audit history.
		set_deal_meeting(
			deal,
			flow=MEETING_FLOW_BAP,
			subject="BAP Session",
			starts_on=data.get("new_bap_datetime"),
			meeting_type=data.get("new_location_type"),
		)

		note.append(f"New date: {data.get('new_bap_datetime')}")
		if data.get("reschedule_notes"):
			note.append(f"Notes: {data['reschedule_notes']}")
	else:
		note.append("Status changed to Follow-up")
		note.append(f"Follow-up task created, due: {data.get('followup_due_date')}")
		if data.get("followup_notes"):
			note.append(f"Notes: {data['followup_notes']}")

		add_task(
			deal,
			"Follow up on BAP reschedule",
			lines(data.get("reschedule_reason"), data.get("followup_notes")),
			assigned_to=data.get("followup_owner"),
			due_date=data.get("followup_due_date"),
			priority="High",
		)

	add_note(deal, "BAP Rescheduled", "\n".join(note))


def cancel_bap(deal, data):
	detail = data.get("cancel_reason") or ""
	if data.get("cancel_notes"):
		detail = f"{detail} - {data['cancel_notes']}"

	mark_lost(deal, "BAP Cancelled", detail)

	# Cancelling the session cancels its BAP Event in place, preserving its lifecycle history.
	cancel_deal_meeting(deal, flow=MEETING_FLOW_BAP)

	add_note(
		deal,
		"BAP Cancelled",
		lines(f"Reason: {data.get('cancel_reason')}", data.get("cancel_notes")),
	)

	if data.get("schedule_followup"):
		add_task(
			deal,
			data.get("followup_subject") or "Follow up after BAP cancellation",
			data.get("followup_notes") or "",
			assigned_to=data.get("followup_owner"),
			due_date=data.get("followup_date"),
		)


def not_interested(deal, data):
	detail = data.get("ni_reason") or ""
	if data.get("ni_notes"):
		detail = f"{detail} - {data['ni_notes']}"

	mark_lost(deal, "Not Interested", detail)

	add_note(
		deal,
		"Marked Not Interested",
		lines(f"Reason: {data.get('ni_reason')}", data.get("ni_notes")),
	)


def session_won(deal, data):
	"""Close the session as Won and hand it over, documenting where delivery continues.

	`create_coaching_deal` is the single idempotent handover authority; its returned target is
	retained so the source-side note carries a navigable reference to the one aggregate
	delivery Opportunity alongside the submitted handover information.
	"""
	target = create_coaching_deal(deal, data.get("coaching_notes") or "")

	add_handover_note(
		deal,
		target,
		data.get("won_notes"),
		f"Handover notes: {data['coaching_notes']}" if data.get("coaching_notes") else None,
	)


def reopen(deal, data: dict):
	"""Return a lost opportunity to the top of the pipeline.

	The lost reason is cleared, otherwise the deal carries a stale explanation for a
	state it is no longer in.
	"""
	deal.lost_reason = None
	if deal.meta.has_field("lost_reason_detail"):
		deal.lost_reason_detail = ""

	add_note(deal, "Opportunity Reopened", lines(f"Reason: {data.get('reopen_reason', '')}"))


# --------------------------------------------------------------------------------------
# Transitions
# --------------------------------------------------------------------------------------

BOOK_BAP = {
	"name": "book_bap",
	"label": "Book a BAP",
	# "Follow-up" is where a rescheduled BAP lands. Booking is exactly how it comes back,
	# and the form already records everything re-booking needs -- so this is a from_state,
	# not a new action.
	"from_states": ["Submitted", "Follow-up"],
	"to_state": "Session Set",
	"changes_status": True,
	"admin_only": False,
	"handler": book_bap,
	"fields": [
		{"fieldname": "bap_type", "label": "BAP Type", "fieldtype": "Select", "options": "\n".join(BAP_TYPES), "reqd": 1, "deal_field": "custom_bap_type"},
		{"fieldname": "bap_set_by", "label": "Who Set This BAP", "fieldtype": "Link", "options": "User", "reqd": 1, "deal_field": "custom_bap_set_by_user"},
		{"fieldname": "bap_industry", "label": "Industry", "fieldtype": "Link", "options": "CRM Industry", "deal_field": "custom_bap_industry"},
		{"fieldname": "bap_date_set", "label": "Date Set", "fieldtype": "Date", "reqd": 1, "deal_field": "custom_bap_date_set"},
		{"fieldname": "bap_datetime", "label": "Date & Time of BAP", "fieldtype": "Datetime", "reqd": 1, "deal_field": "custom_bap_datetime"},
		{"fieldname": "bap_location_type", "label": "On-Site or Virtual", "fieldtype": "Select", "options": "\n".join(LOCATION_TYPES), "reqd": 1, "deal_field": "custom_bap_location_type"},
		{"fieldname": "bap_street", "label": "Street", "fieldtype": "Data", "deal_field": "custom_bap_street", "depends_on": "eval:doc.bap_location_type=='On-Site'"},
		{"fieldname": "bap_city", "label": "City", "fieldtype": "Data", "deal_field": "custom_bap_city", "depends_on": "eval:doc.bap_location_type=='On-Site'"},
		{"fieldname": "bap_country", "label": "Country", "fieldtype": "Data", "deal_field": "custom_bap_state", "depends_on": "eval:doc.bap_location_type=='On-Site'"},
		{"fieldname": "bap_notes", "label": "Session Notes", "fieldtype": "Small Text", "deal_field": "session_notes"},
	],
}

RUN_BAP = {
	"name": "run_bap",
	"label": "Run a BAP",
	"from_states": ["Session Set"],
	"to_state": None,
	# A won session closes immediately; anything else is simply recorded as run.
	"to_state_map": {
		"outcome": {
			"Won - proceed to coaching": "Won",
			"Follow-up needed": "Session Run",
			"Not interested": "Session Run",
		}
	},
	"changes_status": True,
	"admin_only": False,
	"handler": run_bap,
	"fields": [
		{"fieldname": "session_notes", "label": "Session Notes", "fieldtype": "Small Text", "reqd": 1},
		{"fieldname": "outcome", "label": "Outcome / Next Step", "fieldtype": "Select", "options": "\n".join(SESSION_OUTCOMES), "reqd": 1},
		{"fieldname": "additional_notes", "label": "Additional Notes", "fieldtype": "Small Text"},
	],
}

RESCHEDULE_BAP = {
	"name": "reschedule_bap",
	"label": "Reschedule a BAP",
	"from_states": ["Session Set"],
	"to_state": None,
	# Only the "no date yet" branch moves the deal.
	"to_state_map": {"reschedule_type": {RESCHEDULE_NEEDS_FOLLOWUP: "Follow-up"}},
	"changes_status": True,
	"admin_only": False,
	"handler": reschedule_bap,
	"fields": [
		{"fieldname": "reschedule_type", "label": "Do you have a new date?", "fieldtype": "Select", "options": f"{RESCHEDULE_HAS_DATE}\n{RESCHEDULE_NEEDS_FOLLOWUP}", "reqd": 1},
		{"fieldname": "reschedule_reason", "label": "Reason for Rescheduling", "fieldtype": "Small Text", "reqd": 1},
		{"fieldname": "new_bap_datetime", "label": "New BAP Date & Time", "fieldtype": "Datetime", "depends_on": f"eval:doc.reschedule_type=='{RESCHEDULE_HAS_DATE}'"},
		{"fieldname": "new_location_type", "label": "On-Site or Virtual", "fieldtype": "Select", "options": "\n".join(LOCATION_TYPES), "depends_on": f"eval:doc.reschedule_type=='{RESCHEDULE_HAS_DATE}'"},
		{"fieldname": "reschedule_notes", "label": "Additional Notes", "fieldtype": "Small Text", "depends_on": f"eval:doc.reschedule_type=='{RESCHEDULE_HAS_DATE}'"},
		{"fieldname": "followup_due_date", "label": "Follow-Up Due Date", "fieldtype": "Date", "depends_on": f"eval:doc.reschedule_type=='{RESCHEDULE_NEEDS_FOLLOWUP}'"},
		{"fieldname": "followup_owner", "label": "Assign To", "fieldtype": "Link", "options": "User", "depends_on": f"eval:doc.reschedule_type=='{RESCHEDULE_NEEDS_FOLLOWUP}'"},
		{"fieldname": "followup_notes", "label": "Follow-Up Notes", "fieldtype": "Small Text", "depends_on": f"eval:doc.reschedule_type=='{RESCHEDULE_NEEDS_FOLLOWUP}'"},
	],
}

CANCEL_BAP = {
	"name": "cancel_bap",
	"label": "Cancel a BAP",
	"from_states": [],  # available from any status, as before
	# Declared, not left to the handler: an invisible target cannot be enforced by the
	# transition graph. `cancel_bap` still sets the reason via mark_lost, and
	# execute_action then assigns the same value -- idempotent.
	"to_state": "Lost",
	"changes_status": True,
	"admin_only": False,
	# TXB-192: still reachable via explicit Lost status and Kanban transition routing, but
	# not offered as a direct entry in the Take Action dropdown.
	"hidden_from_menu": True,
	"handler": cancel_bap,
	"fields": [
		{"fieldname": "cancel_reason", "label": "Cancellation Reason", "fieldtype": "Select", "options": "\n".join(CANCEL_REASONS), "reqd": 1},
		{"fieldname": "cancel_notes", "label": "Cancellation Notes", "fieldtype": "Small Text"},
		{"fieldname": "schedule_followup", "label": "Schedule a follow-up?", "fieldtype": "Check"},
		{"fieldname": "followup_owner", "label": "Assign To", "fieldtype": "Link", "options": "User", "depends_on": "eval:doc.schedule_followup"},
		{"fieldname": "followup_subject", "label": "Subject", "fieldtype": "Data", "default": "Follow up after BAP cancellation", "depends_on": "eval:doc.schedule_followup"},
		{"fieldname": "followup_date", "label": "Due Date", "fieldtype": "Date", "depends_on": "eval:doc.schedule_followup"},
		{"fieldname": "followup_notes", "label": "Notes", "fieldtype": "Small Text", "depends_on": "eval:doc.schedule_followup"},
	],
}

NOT_INTERESTED = {
	"name": "not_interested",
	"label": 'Mark as "Not Interested"',
	"from_states": [],
	# Declared, not left to the handler: an invisible target cannot be enforced by the
	# transition graph. `not_interested` still sets the reason via mark_lost, and
	# execute_action then assigns the same value -- idempotent.
	"to_state": "Lost",
	"changes_status": True,
	"admin_only": False,
	"handler": not_interested,
	"fields": [
		{"fieldname": "ni_reason", "label": "Reason", "fieldtype": "Select", "options": "\n".join(NOT_INTERESTED_REASONS), "reqd": 1},
		{"fieldname": "ni_notes", "label": "Additional Notes", "fieldtype": "Small Text"},
	],
}

SESSION_WON = {
	"name": "session_won",
	"label": "Won",
	"from_states": ["Session Run"],
	"to_state": "Won",
	"changes_status": True,
	"admin_only": False,
	"handler": session_won,
	"fields": [
		{"fieldname": "won_notes", "label": "Notes (optional)", "fieldtype": "Small Text"},
		{"fieldname": "coaching_notes", "label": "Handover Notes", "fieldtype": "Small Text", "description": "A Delivering Coaching opportunity will be created automatically, carrying the contact and organization over."},
	],
}

REOPEN = {
	"name": "reopen",
	"label": "Reopen",
	"from_states": ["Lost"],
	"to_state": "Submitted",
	"changes_status": True,
	"admin_only": False,
	"handler": reopen,
	"fields": [
		{
			"fieldname": "reopen_reason",
			"label": "Why is this being reopened?",
			"fieldtype": "Small Text",
			"reqd": 1,
		},
	],
}

INDIVIDUAL_SESSION_ACTIONS = (
	BOOK_BAP,
	RUN_BAP,
	RESCHEDULE_BAP,
	CANCEL_BAP,
	NOT_INTERESTED,
	SESSION_WON,
	REOPEN,
)

"""Take Action transitions for the Selling Training pipeline.

Ported from the `CRM Wizard Framework` Form Script.

This is the most branch-heavy pipeline: five of its nine actions end in one of several
states depending on a decision made in the form, so most carry a `to_state_map`.

Unlike Workshop and Individual Session, "not interested" here moves to the pipeline's own
`Training not interested` status rather than the shared `Lost`, and does not set a lost
reason. That is preserved as-is.
"""

import frappe

from crm.txb.pipelines.common import (
	YES_NO,
	add_note,
	add_task,
	apply_deal_fields,
	lines,
)

PROPOSAL_STATUSES = ("Draft", "Sent", "Accepted", "Rejected")

DISCOVERY_DECISIONS = ("Prepare proposal", "Follow-up needed", "Lost")
PROPOSAL_DECISIONS = ("Proceed to negotiations", "Follow-up needed", "Lost")
NEGOTIATION_RESULTS = ("Agreement reached", "Follow-up needed", "Lost")
TRAINING_OUTCOMES = ("Completed successfully", "Partially completed", "Cancelled")

NOT_INTERESTED_REASONS = (
	"Client declined",
	"No response after follow-up",
	"Budget / timing mismatch",
	"Went with competitor",
	"Internal decision",
	"Other",
)

TRAINING_DATE_KNOWN = "Yes"
TRAINING_DATE_UNKNOWN = "Not yet"

STATUS_NOT_INTERESTED = "Training not interested"


# --------------------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------------------


def set_discovery_meeting(deal, data):
	apply_deal_fields(deal, SET_DISCOVERY_MEETING, data)

	if data.get("notes"):
		add_note(deal, "Discovery Meeting Scheduled", data["notes"])


def run_discovery_meeting(deal, data):
	apply_deal_fields(deal, RUN_DISCOVERY_MEETING, data)

	add_note(
		deal,
		"Discovery Meeting Run",
		lines(
			data.get("discovery_notes"),
			f"Decision: {data.get('decision')}",
			f"Proposal notes: {data['proposal_notes']}" if data.get("proposal_notes") else None,
		),
	)

	if data.get("decision") == "Follow-up needed":
		add_task(
			deal,
			"Follow up after discovery meeting",
			data.get("followup_notes") or "",
			assigned_to=data.get("followup_owner"),
			due_date=data.get("followup_due_date"),
		)

	if data.get("decision") == "Prepare proposal":
		# Both the person acting and the nominated proposal owner get the task, unless
		# they are the same person. The wizard read the actor from a browser cookie; on
		# the server the session user is authoritative.
		actor = frappe.session.user
		owners = [actor]
		if data.get("proposal_owner") and data["proposal_owner"] != actor:
			owners.append(data["proposal_owner"])

		for owner in owners:
			add_task(
				deal,
				"Prepare training proposal for",
				data.get("proposal_notes") or "",
				assigned_to=owner,
				priority="High",
			)


def set_proposal_meeting(deal, data):
	apply_deal_fields(deal, SET_PROPOSAL_MEETING, data)

	if data.get("notes"):
		add_note(deal, "Proposal Meeting Scheduled", data["notes"])


def run_proposal_meeting(deal, data):
	add_note(
		deal,
		"Proposal Meeting Run",
		lines(data.get("meeting_notes"), f"Decision: {data.get('decision')}"),
	)


def negotiation_result(deal, data):
	apply_deal_fields(deal, NEGOTIATION_RESULT, data)

	add_note(
		deal,
		"Negotiation Result",
		lines(data.get("negotiation_notes"), f"Result: {data.get('decision')}"),
	)

	if data.get("decision") == "Follow-up needed":
		add_task(
			deal,
			"Negotiation follow-up",
			data.get("followup_notes") or "",
			assigned_to=data.get("followup_owner"),
			due_date=data.get("followup_due_date"),
			priority="High",
		)


def contract_signed(deal, data):
	apply_deal_fields(deal, CONTRACT_SIGNED, data)

	add_note(
		deal,
		"Contract Signed",
		lines(
			f"Contract signed: {data.get('contract_signed')}",
			f"Contract ref: {data['contract_ref']}" if data.get("contract_ref") else None,
			f"Training date: {data['training_datetime']}" if data.get("training_datetime") else None,
			data.get("notes"),
		),
	)


def set_training_date(deal, data):
	apply_deal_fields(deal, SET_TRAINING_DATE, data)


def training_run(deal, data):
	apply_deal_fields(deal, TRAINING_RUN, data)

	add_note(
		deal,
		"Training Run",
		lines(
			data.get("training_notes"),
			f"Participants: {data.get('participants') or 'N/A'}",
			f"Outcome: {data.get('outcome')}",
		),
	)


def training_not_interested(deal, data):
	"""Moves to the pipeline's own terminal status; no lost reason, as before."""
	add_note(
		deal,
		"Marked Not Interested",
		lines(f"Reason: {data.get('ni_reason')}", data.get("ni_notes")),
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

SET_DISCOVERY_MEETING = {
	"name": "set_discovery_meeting",
	"label": "Set Discovery Meeting",
	"from_states": ["Training submitted"],
	"to_state": "Training discovery meeting set",
	"changes_status": True,
	"admin_only": False,
	"handler": set_discovery_meeting,
	"fields": [
		{"fieldname": "discovery_datetime", "label": "Discovery Meeting Date & Time", "fieldtype": "Datetime", "reqd": 1, "deal_field": "custom_discovery_datetime"},
		{"fieldname": "training_owner", "label": "Opportunity Owner / Trainer", "fieldtype": "Link", "options": "User", "deal_field": "custom_training_owner"},
		{"fieldname": "training_topic", "label": "Training Topic", "fieldtype": "Data", "deal_field": "training_topic"},
		{"fieldname": "notes", "label": "Notes", "fieldtype": "Small Text"},
	],
}

RUN_DISCOVERY_MEETING = {
	"name": "run_discovery_meeting",
	"label": "Run Discovery Meeting",
	"from_states": ["Training discovery meeting set"],
	"to_state": None,
	"to_state_map": {
		"decision": {
			"Prepare proposal": "Training proposal submitted",
			"Follow-up needed": "Training discovery meeting run",
			"Lost": STATUS_NOT_INTERESTED,
		}
	},
	"changes_status": True,
	"admin_only": False,
	"handler": run_discovery_meeting,
	"fields": [
		{"fieldname": "discovery_notes", "label": "Discovery Notes", "fieldtype": "Small Text", "reqd": 1, "deal_field": "custom_discovery_notes"},
		{"fieldname": "decision", "label": "Decision", "fieldtype": "Select", "options": "\n".join(DISCOVERY_DECISIONS), "reqd": 1},
		{"fieldname": "training_company", "label": "Company Name", "fieldtype": "Data", "deal_field": "training_company"},
		{"fieldname": "participants", "label": "Estimated Participants", "fieldtype": "Int", "deal_field": "training_participants"},
		{"fieldname": "proposal_owner", "label": "Proposal Preparation Owner", "fieldtype": "Link", "options": "User", "deal_field": "custom_proposal_owner", "depends_on": "eval:doc.decision=='Prepare proposal'"},
		{"fieldname": "proposal_notes", "label": "Proposal Notes", "fieldtype": "Small Text", "depends_on": "eval:doc.decision=='Prepare proposal'"},
		{"fieldname": "followup_due_date", "label": "Follow-Up Due Date", "fieldtype": "Date", "depends_on": "eval:doc.decision=='Follow-up needed'"},
		{"fieldname": "followup_owner", "label": "Assign To", "fieldtype": "Link", "options": "User", "depends_on": "eval:doc.decision=='Follow-up needed'"},
		{"fieldname": "followup_notes", "label": "Follow-Up Notes", "fieldtype": "Small Text", "depends_on": "eval:doc.decision=='Follow-up needed'"},
	],
}

SET_PROPOSAL_MEETING = {
	"name": "set_proposal_meeting",
	"label": "Set Proposal Meeting",
	"from_states": ["Training discovery meeting run", "Training proposal submitted"],
	"to_state": "Training proposal meeting set",
	"changes_status": True,
	"admin_only": False,
	"handler": set_proposal_meeting,
	"fields": [
		{"fieldname": "proposal_meeting_datetime", "label": "Proposal Meeting Date & Time", "fieldtype": "Datetime", "reqd": 1},
		{"fieldname": "proposal_status", "label": "Proposal Status", "fieldtype": "Select", "options": "\n".join(PROPOSAL_STATUSES), "deal_field": "proposal_status"},
		{"fieldname": "notes", "label": "Notes", "fieldtype": "Small Text"},
	],
}

RUN_PROPOSAL_MEETING = {
	"name": "run_proposal_meeting",
	"label": "Run Proposal Meeting",
	"from_states": ["Training proposal meeting set"],
	"to_state": None,
	"to_state_map": {
		"decision": {
			"Proceed to negotiations": "Training negotiations",
			"Follow-up needed": "Training proposal meeting run",
			"Lost": STATUS_NOT_INTERESTED,
		}
	},
	"changes_status": True,
	"admin_only": False,
	"handler": run_proposal_meeting,
	"fields": [
		{"fieldname": "meeting_notes", "label": "Meeting Notes", "fieldtype": "Small Text", "reqd": 1},
		{"fieldname": "decision", "label": "Decision", "fieldtype": "Select", "options": "\n".join(PROPOSAL_DECISIONS), "reqd": 1},
	],
}

NEGOTIATION_RESULT = {
	"name": "negotiation_result",
	"label": "Negotiation Result",
	"from_states": ["Training proposal meeting run", "Training negotiations"],
	"to_state": None,
	"to_state_map": {
		"decision": {
			"Agreement reached": "Contract signed",
			"Follow-up needed": "Training negotiations",
			"Lost": STATUS_NOT_INTERESTED,
		}
	},
	"changes_status": True,
	"admin_only": False,
	"handler": negotiation_result,
	"fields": [
		{"fieldname": "negotiation_notes", "label": "Negotiation Notes", "fieldtype": "Small Text", "reqd": 1},
		{"fieldname": "decision", "label": "Result", "fieldtype": "Select", "options": "\n".join(NEGOTIATION_RESULTS), "reqd": 1},
		{"fieldname": "contract_ref", "label": "Contract Reference", "fieldtype": "Data", "deal_field": "custom_contract_reference"},
		{"fieldname": "followup_due_date", "label": "Follow-Up Due Date", "fieldtype": "Date", "depends_on": "eval:doc.decision=='Follow-up needed'"},
		{"fieldname": "followup_owner", "label": "Assign To", "fieldtype": "Link", "options": "User", "depends_on": "eval:doc.decision=='Follow-up needed'"},
		{"fieldname": "followup_notes", "label": "Follow-Up Notes", "fieldtype": "Small Text", "depends_on": "eval:doc.decision=='Follow-up needed'"},
	],
}

CONTRACT_SIGNED = {
	"name": "contract_signed",
	"label": "Contract Signed",
	"from_states": ["Training negotiations"],
	"to_state": None,
	# A known training date skips straight past "Contract signed".
	"to_state_map": {
		"training_date_known": {
			TRAINING_DATE_KNOWN: "Training date set",
			TRAINING_DATE_UNKNOWN: "Contract signed",
		}
	},
	"changes_status": True,
	"admin_only": False,
	"handler": contract_signed,
	"fields": [
		{"fieldname": "contract_ref", "label": "Contract Reference / Upload", "fieldtype": "Data", "deal_field": "custom_contract_reference"},
		{"fieldname": "contract_signed", "label": "Contract Signed?", "fieldtype": "Select", "options": YES_NO, "reqd": 1, "deal_field": "custom_contract_signed"},
		{"fieldname": "training_date_known", "label": "Is training date known?", "fieldtype": "Select", "options": f"{TRAINING_DATE_KNOWN}\n{TRAINING_DATE_UNKNOWN}", "reqd": 1},
		{"fieldname": "notes", "label": "Notes", "fieldtype": "Small Text"},
		{"fieldname": "training_datetime", "label": "Training Date & Time", "fieldtype": "Datetime", "depends_on": f"eval:doc.training_date_known=='{TRAINING_DATE_KNOWN}'"},
		{"fieldname": "trainer", "label": "Trainer / Owner", "fieldtype": "Link", "options": "User", "deal_field": "custom_training_owner", "depends_on": f"eval:doc.training_date_known=='{TRAINING_DATE_KNOWN}'"},
	],
}

SET_TRAINING_DATE = {
	"name": "set_training_date",
	"label": "Set Training Date",
	"from_states": ["Contract signed"],
	"to_state": "Training date set",
	"changes_status": True,
	"admin_only": False,
	"handler": set_training_date,
	"fields": [
		{"fieldname": "training_datetime", "label": "Training Date & Time", "fieldtype": "Datetime", "reqd": 1},
		{"fieldname": "trainer", "label": "Trainer / Owner", "fieldtype": "Link", "options": "User", "deal_field": "custom_training_owner"},
		{"fieldname": "notes", "label": "Notes", "fieldtype": "Small Text", "deal_field": "custom_training_notes"},
	],
}

TRAINING_RUN = {
	"name": "training_run",
	"label": "Training Run",
	"from_states": ["Training date set"],
	"to_state": None,
	"to_state_map": {
		"outcome": {
			"Completed successfully": "Training run",
			"Partially completed": "Training run",
			"Cancelled": STATUS_NOT_INTERESTED,
		}
	},
	"changes_status": True,
	"admin_only": False,
	"handler": training_run,
	"fields": [
		{"fieldname": "training_notes", "label": "Training Notes", "fieldtype": "Small Text", "reqd": 1, "deal_field": "custom_training_notes"},
		{"fieldname": "participants", "label": "Number of Participants", "fieldtype": "Int", "deal_field": "training_participants"},
		{"fieldname": "outcome", "label": "Outcome", "fieldtype": "Select", "options": "\n".join(TRAINING_OUTCOMES), "reqd": 1},
	],
}

TRAINING_NOT_INTERESTED = {
	"name": "training_not_interested",
	"label": 'Mark as "Not Interested"',
	"from_states": [],
	"to_state": STATUS_NOT_INTERESTED,
	"changes_status": True,
	"admin_only": False,
	"handler": training_not_interested,
	"fields": [
		{"fieldname": "ni_reason", "label": "Reason", "fieldtype": "Select", "options": "\n".join(NOT_INTERESTED_REASONS), "reqd": 1},
		{"fieldname": "ni_notes", "label": "Additional Notes", "fieldtype": "Small Text"},
	],
}

REOPEN = {
	"name": "reopen",
	"label": "Reopen",
	"from_states": [STATUS_NOT_INTERESTED],
	"to_state": "Training submitted",
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

SELLING_TRAINING_ACTIONS = (
	SET_DISCOVERY_MEETING,
	RUN_DISCOVERY_MEETING,
	SET_PROPOSAL_MEETING,
	RUN_PROPOSAL_MEETING,
	NEGOTIATION_RESULT,
	CONTRACT_SIGNED,
	SET_TRAINING_DATE,
	TRAINING_RUN,
	TRAINING_NOT_INTERESTED,
	REOPEN,
)

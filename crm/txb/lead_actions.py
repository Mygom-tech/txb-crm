"""Server-owned Lead action authority.

The Lead has one guarded transition: into "Contact attempted". Three surfaces used to reach
it independently -- a Kanban drag onto that column, the Lead status dropdown, and Take Action
-- and none of them recorded that a call had actually been dialled. A card could be dragged to
Contact attempted having attempted nothing.

This module is the single place that owns the rule, mirroring the Deal arrangement in
`crm.txb.api.actions`:

* `log_a_dial` is the only way in. It records a canonical dial-attempt activity (an outgoing
  CRM Call Log with a fixed "No Answer" result, its timestamp, the authenticated caller, and
  optional notes and follow-up) **and** advances the Lead's status, in one transaction, so a
  failure part-way leaves neither a stray activity nor a moved Lead.

* `guard_contact_attempted` is bound to the Lead's `validate` and rejects any other write that
  lands the status on Contact attempted. It is the enforcement boundary: a crafted API call, a
  Kanban drag, or the status dropdown writing the status straight through are all refused
  unless they arrived through `log_a_dial`, which sets a per-document flag the guard trusts.

The activity is never copied or reparented when the Lead converts. It stays a CRM Call Log
referencing the Lead (`reference_doctype`/`reference_docname`), and the converted Deal -- and
through it the Contact -- aggregates that timeline by query in `crm.api.activities`, attributed
to its Lead source, rather than duplicating the record.
"""

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from crm.txb.constants import (
	CONVERSION_PIPELINE_INITIAL_STATUS,
	DIAL_RESULT_NO_ANSWER,
	DISCOVERY_STATUS_OUTCOMES,
	DISCOVERY_TERMINAL_OUTCOMES,
	LEAD_STATUS_CONTACT_ATTEMPTED,
	LEAD_STATUS_DISCOVERY_MEETING_SET,
)
from crm.txb.permissions import is_admin

LEAD_DOCTYPE = "CRM Lead"
CALL_LOG_DOCTYPE = "CRM Call Log"
NOTE_DOCTYPE = "FCRM Note"
TASK_DOCTYPE = "CRM Task"

# An outgoing dial. CRM Call Log `type` is Incoming/Outgoing.
DIAL_CALL_TYPE = "Outgoing"

# A hand-dialled attempt, not one placed through a telephony provider.
DIAL_TELEPHONY_MEDIUM = "Manual"

# Set by log_a_dial on the in-memory Lead so guard_contact_attempted knows this transition
# arrived through the canonical action. A flag rather than a status comparison because the
# guard must distinguish "reached here via a logged dial" from "someone wrote the status".
DIAL_FLAG = "logged_dial"

# The Lead action contract, kept in step with the browser's copy in
# frontend/src/utils/leadActions.js. The result is a read-only, server-fixed field so a client
# cannot re-point it at a connected outcome.
LOG_A_DIAL = {
	"name": "log_a_dial",
	"label": "Log a dial",
	"to_state": LEAD_STATUS_CONTACT_ATTEMPTED,
	"changes_status": True,
	"fields": [
		{
			"fieldname": "dialed_at",
			"label": "Dialed At",
			"fieldtype": "Datetime",
			"reqd": 1,
			"default": "Now",
		},
		{
			"fieldname": "dial_result",
			"label": "Result",
			"fieldtype": "Data",
			"read_only": 1,
			"default": "No answer",
		},
		{"fieldname": "notes", "label": "Notes", "fieldtype": "Small Text"},
		{
			"fieldname": "follow_up_date",
			"label": "Follow-up Date",
			"fieldtype": "Datetime",
		},
	],
}


def discovery_outcomes() -> tuple:
	"""The six approved discovery outcomes, in display order.

	Three keep the lead a lead (the non-conversion resting statuses); three convert it into a
	pipeline-correct Opportunity. Kept in step with the browser's copy in
	frontend/src/utils/leadActions.js so the two offer exactly the same choices.
	"""
	return (*DISCOVERY_STATUS_OUTCOMES, *CONVERSION_PIPELINE_INITIAL_STATUS)


def is_conversion_outcome(outcome: str) -> bool:
	"""Whether an outcome converts the lead rather than resting it at a Lead status."""
	return outcome in CONVERSION_PIPELINE_INITIAL_STATUS


# The Run Discovery Meeting contract, kept in step with the browser's copy in
# frontend/src/utils/leadActions.js. It is a guarded action available only from
# LEAD_STATUS_DISCOVERY_MEETING_SET, never a durable resting status: the submit records notes
# and applies exactly one of six outcomes atomically, so there is no stranded "meeting run"
# state between the two. `to_state` is intentionally None because the target depends on the
# chosen outcome, resolved server-side in run_discovery_meeting.
RUN_DISCOVERY_MEETING = {
	"name": "run_discovery_meeting",
	"label": "Run Discovery Meeting",
	"from_states": [LEAD_STATUS_DISCOVERY_MEETING_SET],
	"to_state": None,
	"changes_status": True,
	"fields": [
		{
			"fieldname": "notes",
			"label": "Meeting Notes",
			"fieldtype": "Small Text",
			"reqd": 1,
		},
		{
			"fieldname": "outcome",
			"label": "Outcome",
			"fieldtype": "Select",
			"reqd": 1,
			"options": "\n".join(discovery_outcomes()),
		},
	],
}


def get_lead_actions():
	"""Every action the Lead exposes, as a tuple so callers stay uniform."""
	return (LOG_A_DIAL, RUN_DISCOVERY_MEETING)


@frappe.whitelist()
def get_available_lead_actions(lead: str) -> dict:
	"""What this user may do to this Lead right now.

	Mirrors `crm.txb.api.actions.get_available_actions`: the same rule that enforces the
	transition also decides what the UI offers, so the two cannot drift. A user without write
	permission is offered nothing and the status controls disable themselves from
	`can_change_status`.
	"""
	frappe.has_permission(LEAD_DOCTYPE, "read", lead, throw=True)

	may_write = bool(frappe.has_permission(LEAD_DOCTYPE, "write", lead))
	status = frappe.db.get_value(LEAD_DOCTYPE, lead, "status")

	actions = []
	if may_write:
		for action in get_lead_actions():
			# An action that declares `from_states` is offered only from those statuses, so a
			# state-specific action such as Run Discovery Meeting never appears elsewhere. One
			# with none (Log a dial) stays available from any status, as before.
			from_states = action.get("from_states")
			if from_states and status not in from_states:
				continue
			actions.append(
				{
					"name": action["name"],
					"label": action["label"],
					"to_state": action["to_state"],
					"fields": action["fields"],
				}
			)

	return {"actions": actions, "can_change_status": may_write}


@frappe.whitelist()
def log_a_dial(
	lead: str,
	dialed_at: str | None = None,
	notes: str | None = None,
	follow_up_date: str | None = None,
	dial_result: str | None = None,
	data: str | dict | None = None,
) -> dict:
	"""Record a No-answer dial and advance the Lead to Contact attempted, atomically.

	The activity and the status move are applied to the request's single transaction: the
	call log is inserted, then the Lead is saved through its normal validation with a flag that
	authorises the guarded status. If either step throws -- a permission failure, a validation
	error -- the whole request rolls back and nothing is left behind.

	`data` accepts the field dict the browser dialog collects; explicit keyword arguments win
	so a direct API caller can pass them by name. The result is never taken from the caller: a
	dial that only reaches Contact attempted is a No Answer by definition.
	"""
	frappe.has_permission(LEAD_DOCTYPE, "write", lead, throw=True)

	payload = _coerce_payload(data)
	dialed_at = dialed_at or payload.get("dialed_at") or now_datetime()
	notes = notes if notes is not None else payload.get("notes")
	follow_up_date = follow_up_date or payload.get("follow_up_date")

	doc = frappe.get_doc(LEAD_DOCTYPE, lead)
	actor = frappe.session.user

	call = frappe.new_doc(CALL_LOG_DOCTYPE)
	call.update(
		{
			# CRM Call Log is named by its `id` (autoname field:id). A telephony provider
			# supplies a call SID; a hand-logged dial has none, so mint a unique one here or the
			# insert has nothing to name the record by.
			"id": f"dial-{frappe.generate_hash(length=12)}",
			"type": DIAL_CALL_TYPE,
			# Fixed outcome -- never the caller's `dial_result`, which is a read-only display
			# field on the form and would be a side door to a connected status.
			"status": DIAL_RESULT_NO_ANSWER,
			"start_time": dialed_at,
			"caller": actor,
			"telephony_medium": DIAL_TELEPHONY_MEDIUM,
			"reference_doctype": LEAD_DOCTYPE,
			"reference_docname": doc.name,
		}
	)
	note_name = _attach_dial_note(notes)
	if note_name:
		call.note = note_name
	call.insert(ignore_permissions=True)

	# The flag authorises the one guarded status; the guard rejects the same write from any
	# other caller. Saved through validate so SLA, ownership and status-log hooks still run.
	doc.flags[DIAL_FLAG] = True
	doc.status = LEAD_STATUS_CONTACT_ATTEMPTED
	doc.save()

	follow_up = _create_follow_up_task(doc, follow_up_date) if follow_up_date else None

	return {
		"call_log": call.name,
		"status": doc.status,
		"note": note_name,
		"follow_up": follow_up,
	}


def guard_contact_attempted(doc, method=None):
	"""Reject a move into Contact attempted that did not come from log_a_dial.

	Bound to CRM Lead `validate`. It fires only when the status is actually changing to the
	guarded value, so editing an already-attempted Lead's other fields is untouched, and a Lead
	that never approaches the status is unaffected. `log_a_dial` sets `DIAL_FLAG` on the
	document before saving; every other route -- Kanban drag, status dropdown, a raw API write
	-- arrives without it and is refused, which is what makes a logged dial mandatory.
	"""
	if doc.get("status") != LEAD_STATUS_CONTACT_ATTEMPTED:
		return
	if not doc.has_value_changed("status"):
		return
	if doc.flags.get(DIAL_FLAG):
		return

	frappe.throw(
		_("Log a dial to move this lead to {0}.").format(_(LEAD_STATUS_CONTACT_ATTEMPTED)),
		title=_("Dial Required"),
	)


@frappe.whitelist()
def run_discovery_meeting(
	lead: str,
	notes: str | None = None,
	outcome: str | None = None,
	data: str | dict | None = None,
) -> dict:
	"""Run the discovery meeting and apply exactly one outcome, atomically.

	Guarded to the booked state: the lead must be at Discovery meeting set, so this is an
	action from that status rather than a durable "meeting run" resting state. The submit
	requires notes and exactly one of six approved outcomes; anything else is refused before a
	single write, so a rejected submission -- like a cancelled dialog -- leaves the lead exactly
	where it was.

	Three outcomes rest the lead at a Lead status (Nurture, Not interested, Disqualified). The
	other three convert it, reusing the one conversion authority
	(`crm.fcrm.doctype.crm_lead.crm_lead.convert_to_deal`): a Contact is created or reused and a
	single pipeline-correct Opportunity is created in that pipeline's required entry state. Both
	the note and the outcome are applied inside the request's one transaction, so a failure
	part-way rolls back the whole request and leaves neither a stray note nor a half-moved lead.
	"""
	frappe.has_permission(LEAD_DOCTYPE, "write", lead, throw=True)

	payload = _coerce_payload(data)
	notes = (notes if notes is not None else payload.get("notes")) or ""
	notes = notes.strip()
	outcome = outcome or payload.get("outcome")

	# Notes are mandatory: every completed discovery meeting produces a record of what happened.
	if not notes:
		frappe.throw(
			_("Meeting notes are required to run the discovery meeting."),
			title=_("Notes Required"),
		)

	# Exactly one of the six approved outcomes. A Select carries one value; the server re-checks
	# it here so a crafted call cannot smuggle an unapproved or empty outcome past the form.
	if outcome not in discovery_outcomes():
		frappe.throw(
			_("Choose one of the approved discovery outcomes."),
			title=_("Outcome Required"),
		)

	doc = frappe.get_doc(LEAD_DOCTYPE, lead)
	if doc.status != LEAD_STATUS_DISCOVERY_MEETING_SET:
		frappe.throw(
			_('Run Discovery Meeting is only available while the lead is "{0}".').format(
				_(LEAD_STATUS_DISCOVERY_MEETING_SET)
			),
			title=_("Action not available"),
		)

	note_name = _record_meeting_note(doc, notes)

	if is_conversion_outcome(outcome):
		# Reuse the existing conversion authority: it creates or reuses the Contact, resolves the
		# organization, and creates one Opportunity pinned to the chosen pipeline's entry state.
		# Imported here to avoid a module-load cycle with the CRM Lead controller.
		from crm.fcrm.doctype.crm_lead.crm_lead import convert_to_deal

		deal = convert_to_deal(lead=doc.name, deal={"pipeline_type": outcome})
		return {
			"outcome": outcome,
			"converted": True,
			"deal": deal,
			"note": note_name,
		}

	# Non-conversion: rest the lead at the outcome's status. Saved through validate so SLA,
	# ownership and status-log hooks still run; the guard only fires when reopening a terminal
	# status, so moving *into* one here is unaffected.
	doc.status = DISCOVERY_STATUS_OUTCOMES[outcome]
	doc.save()

	return {
		"outcome": outcome,
		"converted": False,
		"status": doc.status,
		"note": note_name,
	}


def guard_discovery_outcome(doc, method=None):
	"""Reject a non-Admin reopening a terminal discovery outcome.

	Bound to CRM Lead `validate`. A discovery meeting that ends Not interested or Disqualified
	closes the lead; only an Admin may move it back out. The guard fires only when the status is
	actually leaving one of those terminal values, so setting it (from Discovery meeting set) and
	editing an already-closed lead's other fields are both untouched, and Nurture -- a warm
	resting state, not a closed one -- stays freely movable.
	"""
	if not doc.has_value_changed("status"):
		return

	before = doc.get_doc_before_save()
	old_status = before.status if before else None
	if old_status not in DISCOVERY_TERMINAL_OUTCOMES:
		return

	if is_admin():
		return

	frappe.throw(
		_("Only an Admin can reopen a lead that is {0}.").format(_(old_status)),
		title=_("Reopen Not Permitted"),
	)


def _record_meeting_note(doc, notes: str) -> str:
	"""Store the discovery meeting notes as an FCRM Note linked to the lead.

	Referenced to the lead so the note shows in its activity timeline, mirroring how pipeline
	actions attach a deal note. Inserted inside the caller's transaction.
	"""
	note = frappe.new_doc(NOTE_DOCTYPE)
	note.update(
		{
			"title": _("Discovery Meeting Run"),
			"content": notes,
			"reference_doctype": LEAD_DOCTYPE,
			"reference_docname": doc.name,
		}
	)
	note.insert(ignore_permissions=True)
	return note.name


def _coerce_payload(data) -> dict:
	"""Normalise the optional `data` bag to a dict; a JSON string is what the browser sends."""
	if isinstance(data, str):
		data = json.loads(data)
	if not isinstance(data, dict):
		return {}
	return data


def _attach_dial_note(notes) -> str | None:
	"""Store optional dial notes as an FCRM Note linked to the call log.

	The CRM Call Log `note` field is a Link to FCRM Note, so the text lives in a note the call
	carries rather than a plain column. Returns the note name, or None when no notes were given.
	"""
	text = (notes or "").strip()
	if not text:
		return None

	note = frappe.new_doc(NOTE_DOCTYPE)
	note.update(
		{
			"title": _("Dial attempt"),
			"content": text,
		}
	)
	note.insert(ignore_permissions=True)
	return note.name


def _create_follow_up_task(lead, follow_up_date) -> str | None:
	"""Create the optional follow-up as a CRM Task referencing the Lead.

	Kept referencing the Lead (not copied onto anything) so it travels with the Lead's timeline
	and, after conversion, aggregates into the Deal and Contact views by query.
	"""
	task = frappe.new_doc(TASK_DOCTYPE)
	task.update(
		{
			"title": _("Follow up on dial"),
			"due_date": follow_up_date,
			"status": "Todo",
			"reference_doctype": LEAD_DOCTYPE,
			"reference_docname": lead.name,
		}
	)
	task.insert(ignore_permissions=True)
	return task.name

"""Manual call logging: one contract for Log a dial (Lead) and Log a call (Lead, Deal, Contact).

Calls are made on the phone and logged by hand. Every surface posts the same fields, validated
here -- the browser is not a boundary -- and every call is recorded the same way: an outgoing
manual CRM Call Log with its result, a linked Note holding the Call Notes, and, when a next call
date is given, a follow-up CRM Task. An "In Progress" result means the person was not reached and
a retry is planned, so it requires the next call date.

Log a dial adds the Lead's move to Contact attempted on top (`crm.txb.lead_actions.log_a_dial`).
"""

import json

import frappe
from frappe import _
from frappe.utils import escape_html, now_datetime

from crm.txb import meetings
from crm.txb.constants import DIAL_RESULT_RETRY, DIAL_RESULTS

CALL_LOG_DOCTYPE = "CRM Call Log"
NOTE_DOCTYPE = "FCRM Note"
TASK_DOCTYPE = "CRM Task"

# The one calendar Event a Deal or Contact keeps for its next call; rescheduling moves it.
FOLLOW_UP_CALL_FLOW = "call_follow_up"

# The records a call can be logged on.
LOGGABLE_DOCTYPES = ("CRM Lead", "CRM Deal", "Contact")

# An outgoing, hand-dialled attempt -- not one placed through a telephony provider.
DIAL_CALL_TYPE = "Outgoing"
DIAL_TELEPHONY_MEDIUM = "Manual"

# The call contract every surface renders, kept in step with the browser's copy in
# frontend/src/utils/leadActions.js. The server applies the same rules before writing anything.
CALL_FIELDS = [
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
		"fieldtype": "Select",
		"reqd": 1,
		"options": "\n".join(DIAL_RESULTS),
		"default": "No Answer",
	},
	{"fieldname": "notes", "label": "Call Notes", "fieldtype": "Small Text", "reqd": 1},
	{
		"fieldname": "follow_up_date",
		"label": "Next Call Date",
		"fieldtype": "Datetime",
		"mandatory_depends_on": f"eval:doc.dial_result=='{DIAL_RESULT_RETRY}'",
		# TXB-239: business-hour (07:00) option list; earlier times can still be typed.
		"time_options_start": "07:00",
	},
]


def require_dial_result(result: str | None) -> str:
	"""Return an approved result or refuse the request before any write occurs."""
	if result not in DIAL_RESULTS:
		frappe.throw(_("Choose one of the approved call results."), title=_("Result Required"))
	return str(result)


def require_dial_notes(notes: str | None) -> str:
	"""Return trimmed call notes or refuse the request before any artifact is written."""
	text = (notes or "").strip()
	if not text:
		frappe.throw(_("Enter the call notes."), title=_("Call Notes Required"))
	return text


def parse_call(data) -> dict:
	"""The validated call fields from the browser's payload (a dict or its JSON string)."""
	if isinstance(data, str):
		data = json.loads(data)
	payload = data if isinstance(data, dict) else {}
	result = require_dial_result(payload.get("dial_result"))
	follow_up_date = payload.get("follow_up_date") or None
	if result == DIAL_RESULT_RETRY and not follow_up_date:
		frappe.throw(
			_("Enter the next call date for a call that is still in progress."),
			frappe.MandatoryError,
			title=_("Next Call Date Required"),
		)
	return {
		"dialed_at": payload.get("dialed_at") or now_datetime(),
		"dial_result": result,
		"notes": require_dial_notes(payload.get("notes")),
		"follow_up_date": follow_up_date,
		"opportunity": payload.get("opportunity") or None,
	}


def record_call(reference_doctype: str, reference_name: str, call: dict) -> dict:
	"""Write the Call Log, its Note and the optional follow-up Task for one validated call."""
	note = _attach_call_note(reference_doctype, reference_name, call)
	log = frappe.new_doc(CALL_LOG_DOCTYPE)
	log.update(
		{
			# CRM Call Log is named by its `id`: a provider supplies a call SID, a hand-logged call
			# has none, so one is minted here.
			"id": f"dial-{frappe.generate_hash(length=12)}",
			"type": DIAL_CALL_TYPE,
			"status": call["dial_result"],
			"start_time": call["dialed_at"],
			"caller": frappe.session.user,
			"telephony_medium": DIAL_TELEPHONY_MEDIUM,
			"reference_doctype": reference_doctype,
			"reference_docname": reference_name,
			"opportunity": call["opportunity"],
			"note": note,
		}
	)
	log.insert(ignore_permissions=True)
	follow_up = None
	if call["follow_up_date"]:
		follow_up = _create_follow_up_task(reference_doctype, reference_name, call["follow_up_date"])
		_schedule_follow_up_call(reference_doctype, reference_name, call["follow_up_date"])
	return {"call_log": log.name, "note": note, "follow_up": follow_up}


@frappe.whitelist()
def log_a_call(reference_doctype: str, reference_name: str, data: str | dict | None = None) -> dict:
	"""Log a manual call on a Lead, Deal or Contact the user may write to."""
	if reference_doctype not in LOGGABLE_DOCTYPES:
		frappe.throw(_("Calls cannot be logged on {0}.").format(reference_doctype), frappe.ValidationError)
	frappe.has_permission(reference_doctype, "write", reference_name, throw=True)
	return record_call(reference_doctype, reference_name, parse_call(data))


def _attach_call_note(reference_doctype: str, reference_name: str, call: dict) -> str:
	"""One record-linked Note holding the call details, so it shows on the record's timeline."""
	note = frappe.new_doc(NOTE_DOCTYPE)
	note.update(
		{
			"title": _("Dial attempt - {0}").format(call["dial_result"]),
			"content": (
				f"<p><strong>{_('Dialed At')}:</strong> {escape_html(str(call['dialed_at']))}</p>"
				f"<p><strong>{_('Result')}:</strong> {escape_html(call['dial_result'])}</p>"
				f"<p><strong>{_('Call Notes')}:</strong><br>{escape_html(call['notes'])}</p>"
			),
			"reference_doctype": reference_doctype,
			"reference_docname": reference_name,
			"opportunity": call["opportunity"],
		}
	)
	note.insert(ignore_permissions=True)
	return note.name


def _create_follow_up_task(reference_doctype: str, reference_name: str, due) -> str:
	"""The next call as a CRM Task on the same record, assigned to whoever logged this one."""
	task = frappe.new_doc(TASK_DOCTYPE)
	task.update(
		{
			"title": _("Follow up on call"),
			"due_date": due,
			"status": "Todo",
			"assigned_to": frappe.session.user,
			"reference_doctype": reference_doctype,
			"reference_docname": reference_name,
		}
	)
	task.insert(ignore_permissions=True)
	return task.name


def _schedule_follow_up_call(reference_doctype: str, reference_name: str, starts_on):
	"""Put the next call on the calendar of whoever logged this one and the record's people."""
	me = meetings.user_participant(frappe.session.user)
	caller = [me] if me else []
	if reference_doctype == meetings.LEAD_DOCTYPE:
		# A Lead's follow-up is occurrence-based: a new date retires the previous open one.
		meetings.sync_lead_occurrence_event(
			reference_docname=reference_name,
			flow=meetings.LEAD_FOLLOW_UP_FLOW,
			submission_id=meetings.submission_id(None, starts_on),
			# Same subject as the Lead's Schedule Follow-up action, which owns this flow too.
			subject=_("Follow-up"),
			starts_on=starts_on,
			participants=meetings.lead_participants(frappe.get_doc(reference_doctype, reference_name))
			+ caller,
		)
		return
	# A Deal or Contact keeps one follow-up Event that moves, so each caller joins it as they log.
	people = (
		meetings.deal_participants(frappe.get_doc(reference_doctype, reference_name))
		if reference_doctype == meetings.DEAL_DOCTYPE
		else []
	)
	meetings.sync_meeting_event(
		reference_doctype=reference_doctype,
		reference_docname=reference_name,
		flow=FOLLOW_UP_CALL_FLOW,
		subject=_("Follow-up call"),
		starts_on=starts_on,
		participants=people + caller,
	)

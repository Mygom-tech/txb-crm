"""Take Action endpoints.

The server owns the transition table, so the list a user is offered and the rules applied
when they act come from the same place. The previous implementation decided visibility in
the browser and enforced nothing, which is how a check written as `adminOnly` ended up
protecting nothing at all.
"""

import json

import frappe
from frappe import _

from crm.txb.constants import LEAD_STATUS_FOLLOW_UP, LEAD_STATUS_NURTURE
from crm.txb.meetings import sync_meeting_event
from crm.txb.permissions import can_change_status, is_admin
from crm.txb.pipelines.actions import find_action, get_actions, resolve_to_state

DEAL_DOCTYPE = "CRM Deal"
LEAD_DOCTYPE = "CRM Lead"

# TXB-128: the canonical status a Log a reach unlocks, and the required reach fields. The
# retired "Qualifying call" status is folded onto Contacted by a migration patch, so this
# is the only status the reach gate ever targets.
CONTACTED_STATUS = "Contacted"
REACH_REQUIRED_FIELDS = ("summary", "follow_up_context")
# TXB-164: the reach Note and the Contacted status write share this savepoint so they land as
# one unit -- a failure on either rolls both back, leaving no partial note and the status as it
# was.
REACH_SAVEPOINT = "txb_log_reach"

# TXB-129: the status a scheduled Discovery meeting unlocks, ordered right after Contacted.
# Date, time and type are always required; a Virtual meeting also requires a manually entered
# link, an Onsite meeting an address. No link is generated and no calendar entry is created.
DISCOVERY_STATUS = "Discovery meeting set"
DISCOVERY_REQUIRED_FIELDS = ("meeting_date", "meeting_time", "meeting_type")
DISCOVERY_TYPE_VIRTUAL = "Virtual"
DISCOVERY_TYPE_ONSITE = "Onsite"
# TXB-209: the meeting-flow key that gives a Lead's Discovery meeting its stable Event identity,
# so a reschedule or a repeated submit updates the one canonical Event rather than duplicating it.
DISCOVERY_MEETING_FLOW = "lead_discovery"

# TXB-210: the Follow-up transition. A follow-up date-time and context are both required and are
# recorded as one canonical linked Lead note committed atomically with the status; the note insert
# and the status write share a savepoint so a failure on either rolls both back.
FOLLOW_UP_STATUS = LEAD_STATUS_FOLLOW_UP
FOLLOW_UP_REQUIRED_FIELDS = ("follow_up_date", "follow_up_context")
FOLLOW_UP_SAVEPOINT = "txb_follow_up"

# TXB-210: the Nurture transition. Nurture context and next action are both required; a
# next-action date-time is optional. The three are recorded as one canonical linked Lead note
# committed atomically with the status under a shared savepoint, exactly as the reach gate is.
NURTURE_STATUS = LEAD_STATUS_NURTURE
NURTURE_REQUIRED_FIELDS = ("nurture_context", "next_action")
NURTURE_SAVEPOINT = "txb_nurture"


@frappe.whitelist()
def get_available_actions(deal: str) -> dict:
	"""What this user may do to this deal, right now.

	Returns the actions filtered by the deal's current status and by role -- so the UI
	never offers something `execute_action` would refuse -- along with whether the user
	may change the status at all, which the status controls use to disable themselves.

	Both come from the same rule, so what is shown and what is enforced cannot drift.
	"""
	frappe.has_permission(DEAL_DOCTYPE, "read", deal, throw=True)

	doc = frappe.get_cached_doc(DEAL_DOCTYPE, deal)
	may_change_status = can_change_status(doc.pipeline_type)

	available = []
	for action in get_actions(doc.pipeline_type):
		if not is_available(action, doc.status):
			continue
		if not is_permitted(action, may_change_status):
			continue

		available.append(
			{
				"name": action["name"],
				"label": action["label"],
				"to_state": action["to_state"],
				# The board pre-selects a branch value from the column the card was
				# dropped on, which it can only do if it can see the mapping.
				"to_state_map": action.get("to_state_map") or {},
				# TXB-192: kept in the collection so status-change and Kanban routing can
				# still resolve and execute the action; the direct Take Action dropdown
				# filters these out client-side.
				"hidden_from_menu": action.get("hidden_from_menu", False),
				"fields": fields_with_defaults(action, doc),
			}
		)

	return {
		"actions": available,
		"can_change_status": may_change_status,
		# The recovery hatch is a role, so the browser must be told about it rather than
		# inferring it from can_change_status -- which is a different, per-pipeline rule.
		"is_admin": is_admin(),
	}


def fields_with_defaults(action: dict, doc) -> list[dict]:
	"""The action's fields, with any per-deal defaults resolved from the live document.

	Most actions have a static field list. Some -- Log Coaching Call -- need a default
	computed from the deal itself (its canonical completed-call total), so the coach sees
	the current server-owned value rather than something the browser supplies. The base
	field dicts are never mutated; only shallow copies carry the override.
	"""
	resolver = action.get("field_defaults")
	if not resolver:
		return action["fields"]

	overrides = resolver(doc) or {}
	return [
		{**field, "default": overrides[field["fieldname"]]}
		if field["fieldname"] in overrides
		else field
		for field in action["fields"]
	]


def is_available(action: dict, status: str | None) -> bool:
	"""An action with no `from_states` may be taken from any status."""
	from_states = action.get("from_states")
	return not from_states or status in from_states


def is_permitted(action: dict, may_change_status: bool) -> bool:
	"""Admin-only actions, and anything that moves the status, need the privilege.

	`changes_status` is declared on every action rather than inferred from `to_state`.
	Branching actions decide their target from the submitted data, so their `to_state` is
	empty until the form comes back -- inferring from it would classify them as harmless
	and let them through on a restricted pipeline.
	"""
	if action.get("admin_only") or action.get("changes_status"):
		return may_change_status
	return True


@frappe.whitelist()
def execute_action(deal: str, action: str, data: str | dict | None = None) -> dict:
	"""Run an action against a deal.

	Re-checks the from-state and the role rather than trusting the caller, because this is
	a whitelisted endpoint and the browser is not a security boundary.

	Everything is applied to one in-memory document and saved once, so a failure part-way
	leaves nothing behind. The old wizard fired several sequential requests and could
	half-apply an action if one of them failed.
	"""
	frappe.has_permission(DEAL_DOCTYPE, "write", deal, throw=True)

	doc = frappe.get_doc(DEAL_DOCTYPE, deal)
	spec = find_action(doc.pipeline_type, action)

	if not spec:
		frappe.throw(
			_("Unknown action {0} for pipeline {1}.").format(action, doc.pipeline_type or "-"),
			frappe.DoesNotExistError,
		)

	if not is_available(spec, doc.status):
		frappe.throw(
			_('"{0}" is not available while the status is "{1}".').format(
				_(spec["label"]), _(doc.status or "")
			),
			frappe.ValidationError,
			title=_("Action not available"),
		)

	if not is_permitted(spec, can_change_status(doc.pipeline_type)):
		frappe.throw(
			_('Only users with the Admin role can perform "{0}".').format(_(spec["label"])),
			frappe.PermissionError,
			title=_("Not permitted"),
		)

	values = parse_data(data)
	validate_required(spec, values)

	# Conditional, action-specific rules (e.g. Next Coaching Call Date is required unless
	# this is the last call). Runs before the flag is armed and before any write, so a
	# rejected submission leaves nothing behind, and a direct API call meets the same rule
	# the form's `mandatory_depends_on` enforces in the browser.
	validator = spec.get("validate")
	if validator:
		validator(doc, values)

	# Tells `guard_transition` this write is an action rather than a bare status set.
	# Scoped to this document's name, not just truthy, so the exemption cannot leak onto
	# another CRM Deal saved inside the same request. Cleared in `finally` so a throw
	# cannot leave it armed for the rest of the request.
	frappe.flags.txb_action = doc.name
	try:
		spec["handler"](doc, values)

		to_state = resolve_to_state(spec, values)
		if to_state:
			doc.status = to_state

		doc.save()
	finally:
		frappe.flags.txb_action = None

	return {"deal": doc.name, "status": doc.status}


@frappe.whitelist()
def log_reach(lead: str, status: str | None = None, activity: str | dict | None = None) -> dict:
	"""Move a Lead into "Contacted" and record the reach that justifies it, atomically.

	Entering Contacted is never a bare status flip (TXB-128): the browser posts a Log a
	reach here, and the status changes only if the reach is valid. Required summary and
	follow-up context are re-checked server-side -- the browser is not a security boundary
	-- so a direct API call cannot slip a blank reach past the dialog. The reach lands on
	the Lead's timeline and the status is set on one in-memory document saved once, so a
	validation failure leaves both untouched and cancelling in the browser (nothing posted)
	leaves the status unchanged.

	`status` is accepted for symmetry with the browser payload but ignored: the reach only
	ever unlocks Contacted, and trusting a caller-supplied target would let the endpoint set
	an arbitrary status without its own gate.
	"""
	frappe.has_permission(LEAD_DOCTYPE, "write", lead, throw=True)

	values = parse_data(activity)
	validate_reach(values)

	doc = frappe.get_doc(LEAD_DOCTYPE, lead)

	# Tells `require_reach_for_contacted` this status->Contacted write is the reach save
	# rather than a bare status set. Scoped to this document's name, not just truthy, so the
	# exemption cannot leak onto another CRM Lead saved inside the same request. Cleared in
	# `finally` so a throw cannot leave it armed for the rest of the request.
	frappe.flags.txb_action = doc.name
	try:
		# Note first, status second, under one savepoint: the two writes commit together or
		# not at all. The reach is stored as a native FCRM Note linked to the Lead (TXB-164) so
		# it surfaces under the Notes tab, which is sourced exclusively from linked notes -- the
		# retired Info comment never appeared there. A persistence failure on either write rolls
		# the savepoint back, so a status-save throw cannot strand a partial note and the prior
		# Lead status is left untouched.
		frappe.db.savepoint(REACH_SAVEPOINT)
		try:
			note = frappe.get_doc(
				{
					"doctype": "FCRM Note",
					"title": _("Log a reach"),
					"content": reach_note_html(values),
					"reference_doctype": LEAD_DOCTYPE,
					"reference_docname": doc.name,
				}
			)
			note.insert()
			doc.status = CONTACTED_STATUS
			doc.save()
		except Exception:
			frappe.db.rollback(save_point=REACH_SAVEPOINT)
			raise
	finally:
		frappe.flags.txb_action = None

	return {"lead": doc.name, "status": doc.status, "note": note.name}


def validate_reach(values: dict):
	"""Required summary and follow-up context, with whitespace-only treated as blank.

	Mirrors `validate_required`'s emptiness rule (see `_is_blank`) so a reach submitted
	straight to the API meets exactly what the Log a reach dialog enforces in the browser.
	"""
	labels = {
		"summary": _("Reach summary"),
		"follow_up_context": _("Follow-up context"),
	}
	missing = [labels[field] for field in REACH_REQUIRED_FIELDS if _is_blank(values.get(field))]
	if missing:
		frappe.throw(
			_("{0} is required.").format(", ".join(missing)),
			frappe.MandatoryError,
			title=_("Log a reach"),
		)


def reach_note_html(values: dict) -> str:
	"""Render the reach as the body of its FCRM Note, preserving every submitted field.

	The note always carries explicit Reach summary, Follow-up context and Follow-up date
	labels so the card reads the same however it was created. The optional follow-up date is
	shown verbatim when supplied and as a clear not-set marker when blank, rather than being
	dropped. Every user-supplied value is escaped so a summary is recorded as text rather than
	interpreted as markup.
	"""
	escape = frappe.utils.escape_html
	follow_up_date = values.get("follow_up_date")
	if follow_up_date and str(follow_up_date).strip():
		follow_up_date_value = escape(str(follow_up_date).strip())
	else:
		follow_up_date_value = f"<i>{_('Not set')}</i>"
	return "".join(
		[
			f"<div><b>{_('Reach summary')}:</b> {escape(values['summary'].strip())}</div>",
			f"<div><b>{_('Follow-up context')}:</b> {escape(values['follow_up_context'].strip())}</div>",
			f"<div><b>{_('Follow-up date')}:</b> {follow_up_date_value}</div>",
		]
	)


@frappe.whitelist()
def schedule_follow_up(lead: str, status: str | None = None, activity: str | dict | None = None) -> dict:
	"""Move a Lead into "Follow-up" and record the follow-up that justifies it, atomically (TXB-210).

	Entering Follow-up is never a bare status flip: the browser posts the follow-up here, and the
	status changes only if it is complete. A follow-up date-time and context are both required and
	re-checked server-side -- the browser is not a security boundary -- so a direct API call (or a
	Kanban drag) cannot slip an empty follow-up past the dialog. The follow-up is stored as a native
	FCRM Note linked to the Lead so it surfaces under the Notes tab, and the status is set on one
	in-memory document; both share one savepoint so a persistence failure on either rolls both back,
	leaving no partial note and the prior status untouched. Cancelling in the browser posts nothing,
	so the status is likewise unchanged.

	`status` is accepted for symmetry with the browser payload but ignored: this endpoint only ever
	unlocks Follow-up, and trusting a caller-supplied target would let it set an arbitrary status
	without its own gate.
	"""
	frappe.has_permission(LEAD_DOCTYPE, "write", lead, throw=True)

	values = parse_data(activity)
	validate_follow_up(values)

	doc = frappe.get_doc(LEAD_DOCTYPE, lead)

	# Tells `require_follow_up_context` this status->Follow-up write is the follow-up save rather
	# than a bare status set. Scoped to this document's name, not just truthy, so the exemption
	# cannot leak onto another CRM Lead saved inside the same request. Cleared in `finally` so a
	# throw cannot leave it armed for the rest of the request.
	frappe.flags.txb_action = doc.name
	try:
		# Note first, status second, under one savepoint: the two writes commit together or not at
		# all, so a status-save throw cannot strand a partial note and the prior status is untouched.
		frappe.db.savepoint(FOLLOW_UP_SAVEPOINT)
		try:
			note = frappe.get_doc(
				{
					"doctype": "FCRM Note",
					"title": _("Follow-up scheduled"),
					"content": follow_up_note_html(values),
					"reference_doctype": LEAD_DOCTYPE,
					"reference_docname": doc.name,
				}
			)
			note.insert()
			doc.status = FOLLOW_UP_STATUS
			doc.save()
		except Exception:
			frappe.db.rollback(save_point=FOLLOW_UP_SAVEPOINT)
			raise
	finally:
		frappe.flags.txb_action = None

	return {"lead": doc.name, "status": doc.status, "note": note.name}


def validate_follow_up(values: dict):
	"""Require the follow-up date-time and context, with whitespace-only treated as blank.

	Mirrors `validate_required`'s emptiness rule (see `_is_blank`) so a follow-up submitted straight
	to the API meets exactly what the Follow-up dialog enforces in the browser.
	"""
	labels = {
		"follow_up_date": _("Follow-up date and time"),
		"follow_up_context": _("Follow-up context"),
	}
	missing = [labels[field] for field in FOLLOW_UP_REQUIRED_FIELDS if _is_blank(values.get(field))]
	if missing:
		frappe.throw(
			_("{0} is required.").format(", ".join(missing)),
			frappe.MandatoryError,
			title=_("Schedule a follow-up"),
		)


def follow_up_note_html(values: dict) -> str:
	"""Render the follow-up as the body of its FCRM Note, escaping every user-supplied value."""
	escape = frappe.utils.escape_html
	return "".join(
		[
			f"<div><b>{_('Follow-up date')}:</b> {escape(str(values['follow_up_date']).strip())}</div>",
			f"<div><b>{_('Follow-up context')}:</b> {escape(values['follow_up_context'].strip())}</div>",
		]
	)


@frappe.whitelist()
def set_nurture(lead: str, status: str | None = None, activity: str | dict | None = None) -> dict:
	"""Move a Lead into "Nurture" and record the nurture plan that justifies it, atomically (TXB-210).

	Entering Nurture is never a bare status flip: the browser posts the nurture plan here, and the
	status changes only if it is complete. A nurture context and a next action are both required and
	re-checked server-side -- the browser is not a security boundary -- so a direct API call (or a
	Kanban drag) cannot slip an empty plan past the dialog; a next-action date-time is optional and
	recorded verbatim when supplied, as a clear not-set marker when blank. The plan is stored as a
	native FCRM Note linked to the Lead so it surfaces under the Notes tab, and the status is set on
	one in-memory document; both share one savepoint so a persistence failure on either rolls both
	back, leaving no partial note and the prior status untouched. Cancelling in the browser posts
	nothing, so the status is likewise unchanged.

	`status` is accepted for symmetry with the browser payload but ignored: this endpoint only ever
	unlocks Nurture, and trusting a caller-supplied target would let it set an arbitrary status
	without its own gate.
	"""
	frappe.has_permission(LEAD_DOCTYPE, "write", lead, throw=True)

	values = parse_data(activity)
	validate_nurture(values)

	doc = frappe.get_doc(LEAD_DOCTYPE, lead)

	# Tells `require_nurture_context` this status->Nurture write is the nurture save rather than a
	# bare status set. Scoped to this document's name, not just truthy, so the exemption cannot leak
	# onto another CRM Lead saved inside the same request. Cleared in `finally` so a throw cannot
	# leave it armed for the rest of the request.
	frappe.flags.txb_action = doc.name
	try:
		# Note first, status second, under one savepoint: the two writes commit together or not at
		# all, so a status-save throw cannot strand a partial note and the prior status is untouched.
		frappe.db.savepoint(NURTURE_SAVEPOINT)
		try:
			note = frappe.get_doc(
				{
					"doctype": "FCRM Note",
					"title": _("Nurture plan"),
					"content": nurture_note_html(values),
					"reference_doctype": LEAD_DOCTYPE,
					"reference_docname": doc.name,
				}
			)
			note.insert()
			doc.status = NURTURE_STATUS
			doc.save()
		except Exception:
			frappe.db.rollback(save_point=NURTURE_SAVEPOINT)
			raise
	finally:
		frappe.flags.txb_action = None

	return {"lead": doc.name, "status": doc.status, "note": note.name}


def validate_nurture(values: dict):
	"""Require the nurture context and next action, with whitespace-only treated as blank.

	Mirrors `validate_required`'s emptiness rule (see `_is_blank`) so a nurture plan submitted
	straight to the API meets exactly what the Nurture dialog enforces in the browser. The
	next-action date-time is optional and not checked here.
	"""
	labels = {
		"nurture_context": _("Nurture context"),
		"next_action": _("Next action"),
	}
	missing = [labels[field] for field in NURTURE_REQUIRED_FIELDS if _is_blank(values.get(field))]
	if missing:
		frappe.throw(
			_("{0} is required.").format(", ".join(missing)),
			frappe.MandatoryError,
			title=_("Nurture the lead"),
		)


def nurture_note_html(values: dict) -> str:
	"""Render the nurture plan as the body of its FCRM Note, preserving every submitted field.

	The note always carries explicit Nurture context, Next action and Next action date labels so the
	card reads the same however it was created. The optional next-action date is shown verbatim when
	supplied and as a clear not-set marker when blank, rather than being dropped. Every user-supplied
	value is escaped so it is recorded as text rather than interpreted as markup.
	"""
	escape = frappe.utils.escape_html
	next_action_date = values.get("next_action_date")
	if next_action_date and str(next_action_date).strip():
		next_action_date_value = escape(str(next_action_date).strip())
	else:
		next_action_date_value = f"<i>{_('Not set')}</i>"
	return "".join(
		[
			f"<div><b>{_('Nurture context')}:</b> {escape(values['nurture_context'].strip())}</div>",
			f"<div><b>{_('Next action')}:</b> {escape(values['next_action'].strip())}</div>",
			f"<div><b>{_('Next action date')}:</b> {next_action_date_value}</div>",
		]
	)


@frappe.whitelist()
def schedule_discovery(lead: str, status: str | None = None, activity: str | dict | None = None) -> dict:
	"""Move a Lead into "Discovery meeting set" and record the meeting that justifies it, atomically.

	Entering Discovery meeting set is never a bare status flip (TXB-129): the browser posts the
	scheduling details here, and the status changes only if they are complete. Required date,
	time and type -- plus a manual link for a Virtual meeting or an address for an Onsite one --
	are re-checked server-side, because the browser is not a security boundary, so a direct API
	call (or the Kanban drag) cannot slip an incomplete schedule past the dialog. The scheduling
	activity lands on the Lead's timeline and the status is set on one in-memory document saved
	once, so a validation failure records neither and cancelling in the browser (nothing posted)
	leaves the status unchanged. No calendar entry, invitation, email or generated link is
	created -- only the details the user supplied are recorded.

	`status` is accepted for symmetry with the browser payload but ignored: this endpoint only
	ever unlocks Discovery meeting set, and trusting a caller-supplied target would let it set an
	arbitrary status without its own gate.
	"""
	frappe.has_permission(LEAD_DOCTYPE, "write", lead, throw=True)

	values = parse_data(activity)
	validate_discovery(values)

	doc = frappe.get_doc(LEAD_DOCTYPE, lead)

	# Tells `require_discovery_details` this status->Discovery write is the scheduled save rather
	# than a bare status set. Scoped to this document's name, not just truthy, so the exemption
	# cannot leak onto another CRM Lead saved inside the same request. Cleared in `finally` so a
	# throw cannot leave it armed for the rest of the request.
	frappe.flags.txb_action = doc.name
	try:
		# Timeline first, status second, one save: both share the request transaction, so a
		# validation throw inside save() rolls the scheduling comment back with the status.
		doc.add_comment("Info", discovery_timeline_html(values))
		# TXB-209: the same scheduling details also upsert the one canonical Event linked to this
		# Lead, so the meeting is visible on the Lead's Events/Activity surface. It shares this
		# request transaction, so a save() throw rolls the Event back with the comment and status;
		# a re-submit or reschedule mutates the same Event rather than creating a duplicate.
		sync_meeting_event(
			reference_doctype=LEAD_DOCTYPE,
			reference_docname=doc.name,
			flow=DISCOVERY_MEETING_FLOW,
			subject=_("Discovery Meeting"),
			starts_on=discovery_starts_on(values),
			meeting_type=values.get("meeting_type"),
			link=values.get("meeting_link"),
			address=values.get("meeting_address"),
		)
		doc.status = DISCOVERY_STATUS
		doc.save()
	finally:
		frappe.flags.txb_action = None

	return {"lead": doc.name, "status": doc.status}


def discovery_starts_on(values: dict) -> str:
	"""Combine the submitted meeting date and time into one datetime for the Event.

	Both are already required by `validate_discovery`, so this only ever runs on a complete
	schedule. The time half tolerates the browser's HH:MM or HH:MM:SS formatting.
	"""
	date = str(values.get("meeting_date", "")).strip()
	time = str(values.get("meeting_time", "")).strip()
	return f"{date} {time}".strip()


def validate_discovery(values: dict):
	"""Require date, time and type, plus the location detail for the chosen type.

	Whitespace-only counts as blank (see `_is_blank`), matching what the Schedule Discovery
	meeting dialog enforces in the browser. A Virtual meeting requires a manually entered link;
	an Onsite meeting requires an address. An unknown/blank type fails on the base requirements
	first, so the conditional check only ever runs for a real type.
	"""
	labels = {
		"meeting_date": _("Meeting date"),
		"meeting_time": _("Meeting time"),
		"meeting_type": _("Meeting type"),
		"meeting_link": _("Meeting link"),
		"meeting_address": _("Meeting address"),
	}

	missing = [labels[field] for field in DISCOVERY_REQUIRED_FIELDS if _is_blank(values.get(field))]

	meeting_type = values.get("meeting_type")
	if meeting_type == DISCOVERY_TYPE_VIRTUAL and _is_blank(values.get("meeting_link")):
		missing.append(labels["meeting_link"])
	elif meeting_type == DISCOVERY_TYPE_ONSITE and _is_blank(values.get("meeting_address")):
		missing.append(labels["meeting_address"])

	if missing:
		frappe.throw(
			_("{0} is required.").format(", ".join(missing)),
			frappe.MandatoryError,
			title=_("Schedule Discovery meeting"),
		)


def discovery_timeline_html(values: dict) -> str:
	"""Render the scheduled meeting as a timeline entry.

	The user-supplied values are escaped so they are recorded as text rather than interpreted as
	markup. Only the location detail matching the chosen type is shown.
	"""
	escape = frappe.utils.escape_html
	rows = [
		f"<div><b>{_('Discovery meeting scheduled')}</b></div>",
		f"<div><i>{_('Date')}:</i> {escape(str(values.get('meeting_date', '')).strip())}"
		f" <i>{_('Time')}:</i> {escape(str(values.get('meeting_time', '')).strip())}</div>",
		f"<div><i>{_('Type')}:</i> {escape(str(values.get('meeting_type', '')).strip())}</div>",
	]
	if values.get("meeting_type") == DISCOVERY_TYPE_VIRTUAL:
		rows.append(f"<div><i>{_('Link')}:</i> {escape(str(values.get('meeting_link', '')).strip())}</div>")
	elif values.get("meeting_type") == DISCOVERY_TYPE_ONSITE:
		rows.append(
			f"<div><i>{_('Address')}:</i> {escape(str(values.get('meeting_address', '')).strip())}</div>"
		)
	return "".join(rows)


def parse_data(data: str | dict | None) -> dict:
	if isinstance(data, str):
		data = json.loads(data)
	return data or {}


def validate_required(spec: dict, values: dict):
	"""None, empty string, and whitespace-only text count as missing.

	A falsy check would reject a required Int of 0 or a required Check left unticked --
	and zero participants is exactly what gets recorded on a workshop being marked lost --
	so only None/empty are missing for non-text values. For a text answer a run of spaces
	is not a real answer either: a required Topic submitted as "   " is rejected here,
	before the handler runs, so a direct API call cannot slip a blank Topic past the form.

	Fields whose requiredness depends on another answer -- Next Coaching Call Date, which
	is required only while "this is the last call" is unticked -- are enforced by the
	action's own `validate` hook (see execute_action), because that condition is expressed
	as the browser's `eval:` string and belongs to the action that owns it, not to this
	generic emptiness check.
	"""
	missing = [
		field["label"]
		for field in spec["fields"]
		if field.get("reqd") and _is_blank(values.get(field["fieldname"]))
	]
	if missing:
		frappe.throw(
			_("{0} is required.").format(", ".join(_(label) for label in missing)),
			frappe.MandatoryError,
		)


def _is_blank(value) -> bool:
	"""Missing for a required field: None, empty, or whitespace-only text.

	A number (including 0) or a boolean is never blank; only strings are stripped, so a
	required Int of 0 or an unticked Check still counts as answered.
	"""
	if value is None or value == "":
		return True
	return isinstance(value, str) and not value.strip()

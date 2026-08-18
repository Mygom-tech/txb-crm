"""Take Action endpoints.

The server owns the transition table, so the list a user is offered and the rules applied
when they act come from the same place. The previous implementation decided visibility in
the browser and enforced nothing, which is how a check written as `adminOnly` ended up
protecting nothing at all.
"""

import json

import frappe
from frappe import _

from crm.txb.permissions import can_change_status, is_admin
from crm.txb.pipelines.actions import find_action, get_actions, resolve_to_state

DEAL_DOCTYPE = "CRM Deal"
LEAD_DOCTYPE = "CRM Lead"

# TXB-128: the canonical status a Log a reach unlocks, and the required reach fields. The
# retired "Qualifying call" status is folded onto Contacted by a migration patch, so this
# is the only status the reach gate ever targets.
CONTACTED_STATUS = "Contacted"
REACH_REQUIRED_FIELDS = ("summary", "follow_up_context")


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
	# Timeline first, status second, one save: both share the request transaction, so a
	# validation throw inside save() rolls the reach comment back with the status.
	doc.add_comment("Info", reach_timeline_html(values))
	doc.status = CONTACTED_STATUS
	doc.save()

	return {"lead": doc.name, "status": doc.status}


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


def reach_timeline_html(values: dict) -> str:
	"""Render the reach as a timeline entry, preserving the summary, context and follow-up.

	An optional follow-up date is only shown when supplied; the required fields are escaped
	so a summary is recorded as text rather than interpreted as markup.
	"""
	escape = frappe.utils.escape_html
	rows = [
		f"<div><b>{_('Reach logged')}</b></div>",
		f"<div>{escape(values['summary'].strip())}</div>",
		f"<div><i>{_('Follow-up')}:</i> {escape(values['follow_up_context'].strip())}</div>",
	]
	follow_up_date = values.get("follow_up_date")
	if follow_up_date and str(follow_up_date).strip():
		rows.append(
			f"<div><i>{_('Follow-up date')}:</i> {escape(str(follow_up_date).strip())}</div>"
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

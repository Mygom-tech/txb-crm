"""Take Action endpoints.

The server owns the transition table, so the list a user is offered and the rules applied
when they act come from the same place. The previous implementation decided visibility in
the browser and enforced nothing, which is how a check written as `adminOnly` ended up
protecting nothing at all.
"""

import json

import frappe
from frappe import _

from crm.txb.permissions import can_change_status
from crm.txb.pipelines.actions import find_action, get_actions

DEAL_DOCTYPE = "CRM Deal"


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
				"fields": action["fields"],
			}
		)

	return {"actions": available, "can_change_status": may_change_status}


def is_available(action: dict, status: str | None) -> bool:
	"""An action with no `from_states` may be taken from any status."""
	from_states = action.get("from_states")
	return not from_states or status in from_states


def is_permitted(action: dict, may_change_status: bool) -> bool:
	"""Admin-only actions, and anything that moves the status, need the privilege.

	`admin_only` is checked as well as `to_state` so an action that is restricted for
	business reasons stays restricted even if it stops moving the status.
	"""
	if action.get("admin_only") or action.get("to_state"):
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

	spec["handler"](doc, values)

	if spec["to_state"]:
		doc.status = spec["to_state"]

	doc.save()

	return {"deal": doc.name, "status": doc.status}


def parse_data(data: str | dict | None) -> dict:
	if isinstance(data, str):
		data = json.loads(data)
	return data or {}


def validate_required(spec: dict, values: dict):
	missing = [
		field["label"]
		for field in spec["fields"]
		if field.get("reqd") and not values.get(field["fieldname"])
	]
	if missing:
		frappe.throw(
			_("{0} is required.").format(", ".join(_(label) for label in missing)),
			frappe.MandatoryError,
		)

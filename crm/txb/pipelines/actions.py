"""The pipeline state machine: which actions move a deal between which statuses.

Lifted out of the `CRM Wizard Framework` Form Script, which held it as DOM-injected
JavaScript in the database. The transition table was already there in data form -- states
an action may start from, a target status, and an `adminOnly` flag -- it simply had no
enforcement behind it, and no way to review a change to it.

Each action declares:

    name           stable identifier used by the API
    label          what the user sees
    from_states    statuses the action may be taken from; empty means any
    to_state       status the deal ends in; None when it does not move, or when the
                   target depends on the answers (see to_state_map)
    to_state_map   {fieldname: {value: status}} for actions whose outcome is chosen in
                   the form -- e.g. a workshop that may end won, rescheduling or lost
    changes_status declared outright, never inferred. A branching action has no
                   to_state until the form comes back, so inferring from it would mark
                   the action harmless and let it past the role check
    admin_only     restricted to the CRM "Admin" role (Frappe's System Manager)
    fields         form fields in Frappe fieldtype vocabulary, so the existing
                   FieldLayout dialog renders them with no bespoke component. A field
                   may carry `deal_field` to be copied onto the deal, and `depends_on`
                   to appear only when relevant
    handler        applies the effects, inside the caller's transaction

Ordering matters: actions are offered in the order listed.
"""

from crm.txb.constants import (
	PIPELINE_DELIVERING_COACHING,
	PIPELINE_INDIVIDUAL_SESSION,
	PIPELINE_SELLING_TRAINING,
	PIPELINE_WORKSHOP,
)
from crm.txb.pipelines.delivering_coaching import DELIVERING_COACHING_ACTIONS
from crm.txb.pipelines.individual_session import INDIVIDUAL_SESSION_ACTIONS
from crm.txb.pipelines.selling_training import SELLING_TRAINING_ACTIONS
from crm.txb.pipelines.workshop import WORKSHOP_ACTIONS

PIPELINE_ACTIONS = {
	PIPELINE_DELIVERING_COACHING: DELIVERING_COACHING_ACTIONS,
	PIPELINE_WORKSHOP: WORKSHOP_ACTIONS,
	PIPELINE_INDIVIDUAL_SESSION: INDIVIDUAL_SESSION_ACTIONS,
	PIPELINE_SELLING_TRAINING: SELLING_TRAINING_ACTIONS,
}


def get_actions(pipeline_type: str | None):
	return PIPELINE_ACTIONS.get(pipeline_type, ())


def find_action(pipeline_type: str | None, name: str):
	for action in get_actions(pipeline_type):
		if action["name"] == name:
			return action
	return None


def resolve_to_state(action: dict, data: dict) -> str | None:
	"""The status this action lands on, given what the user filled in.

	A fixed `to_state` wins. Otherwise `to_state_map` picks the target from a field's
	value, which is how one action can end won, rescheduling or lost.

	Returning None is legitimate: some branches leave the status alone, and some handlers
	set it themselves because it comes with a reason (see `mark_lost`).
	"""
	if action.get("to_state"):
		return action["to_state"]

	for fieldname, targets in (action.get("to_state_map") or {}).items():
		target = targets.get(data.get(fieldname))
		if target:
			return target

	return None

"""The transition graph, derived from the action registry.

`PIPELINE_ACTIONS` already declares which statuses an action may start from and which it
may land on. That is a state machine, and this module simply reads it as one. Nothing here
is hand-authored, so the graph cannot drift from the actions that implement it -- which is
the failure the server-script migration spent four PRs undoing.
"""

from crm.txb.constants import PIPELINE_STATUSES
from crm.txb.pipelines.actions import PIPELINE_ACTIONS, find_action, get_actions


def action_targets(action: dict) -> list[str]:
	"""Every status this action can land on, in declaration order, deduped."""
	targets = []

	if action.get("to_state"):
		targets.append(action["to_state"])

	for mapping in (action.get("to_state_map") or {}).values():
		targets.extend(mapping.values())

	return list(dict.fromkeys(target for target in targets if target))


def action_sources(pipeline_type: str | None, action: dict) -> list[str]:
	"""Statuses the action may start from. Empty `from_states` means any of them."""
	return list(action.get("from_states") or PIPELINE_STATUSES.get(pipeline_type, []))


def get_transitions(pipeline_type: str | None) -> dict:
	"""`{from_status: {to_status: [action_name, ...]}}` for one pipeline.

	Actions that do not move the status are excluded: Log Coaching Call is a legitimate
	thing to do to a deal, but it is not an edge between two columns.
	"""
	graph: dict = {}

	for action in get_actions(pipeline_type):
		if not action.get("changes_status"):
			continue

		targets = action_targets(action)
		for source in action_sources(pipeline_type, action):
			for target in targets:
				# An action that can land where it started is not a transition. Reaching
				# "Lost" from "Lost" is not a move anyone can make on a board.
				if target == source:
					continue
				graph.setdefault(source, {}).setdefault(target, []).append(action["name"])

	return graph


def get_transition_map() -> dict:
	"""The graph for every pipeline that has a state machine."""
	return {pipeline: get_transitions(pipeline) for pipeline in PIPELINE_ACTIONS}


def candidates(pipeline_type: str | None, from_status: str | None, to_status: str) -> list[dict]:
	"""The action specs that move a deal along this edge. More than one is normal."""
	names = get_transitions(pipeline_type).get(from_status, {}).get(to_status, [])
	return [find_action(pipeline_type, name) for name in names]


def is_allowed(pipeline_type: str | None, from_status: str | None, to_status: str) -> bool:
	"""Whether this edge exists. A status that does not move is always allowed."""
	if from_status == to_status:
		return True

	return to_status in get_transitions(pipeline_type).get(from_status, {})

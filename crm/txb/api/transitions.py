"""The transition graph, served to the browser.

One endpoint, one source of truth -- the same arrangement as
`crm.txb.api.pipelines.get_pipeline_statuses`. The payload is a few KB and is fetched once
per board, so the kanban can decide which columns to grey without a round trip per drag.

This is UX only. `crm.txb.permissions.guard_transition` is what actually enforces the
graph; the browser is not a security boundary.
"""

import frappe

from crm.txb.permissions import can_change_status
from crm.txb.pipelines.actions import PIPELINE_ACTIONS, find_action, get_actions
from crm.txb.pipelines import transitions


@frappe.whitelist()
def get_transition_map() -> dict:
	"""Every pipeline's edges, labelled, plus whether this user may move statuses at all."""
	labelled = {}

	for pipeline, graph in transitions.get_transition_map().items():
		labelled[pipeline] = {
			source: {
				target: [
					{"name": name, "label": find_action(pipeline, name)["label"]}
					for name in names
				]
				for target, names in targets.items()
			}
			for source, targets in graph.items()
		}

		# Actions declaring no `from_states` apply from ANY status -- including one outside
		# this pipeline's list, which real data contains. `is_allowed` was given the same
		# fallback server-side; without this key the browser would grey every column for
		# such a deal and then refuse a status it had just offered.
		universal = {}
		for action in get_actions(pipeline):
			if not action.get("changes_status") or action.get("from_states"):
				continue
			for target in transitions.action_targets(action):
				universal.setdefault(target, []).append(
					{"name": action["name"], "label": action["label"]}
				)

		if universal:
			labelled[pipeline]["*"] = universal

	return {
		"transitions": labelled,
		"can_change_status": {
			pipeline: can_change_status(pipeline) for pipeline in PIPELINE_ACTIONS
		},
	}

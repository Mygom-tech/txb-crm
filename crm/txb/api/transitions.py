"""The transition graph, served to the browser.

One endpoint, one source of truth -- the same arrangement as
`crm.txb.api.pipelines.get_pipeline_statuses`. The payload is a few KB and is fetched once
per board, so the kanban can decide which columns to grey without a round trip per drag.

This is UX only. `crm.txb.permissions.guard_transition` is what actually enforces the
graph; the browser is not a security boundary.
"""

import frappe

from crm.txb.permissions import can_change_status
from crm.txb.pipelines.actions import PIPELINE_ACTIONS, find_action
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

	return {
		"transitions": labelled,
		"can_change_status": {
			pipeline: can_change_status(pipeline) for pipeline in PIPELINE_ACTIONS
		},
	}

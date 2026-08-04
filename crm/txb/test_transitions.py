# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-110: the transition graph derived from the action registry."""

from frappe.tests.utils import FrappeTestCase

from crm.txb.constants import (
	PIPELINE_DELIVERING_COACHING,
	PIPELINE_INDIVIDUAL_SESSION,
	PIPELINE_STATUSES,
	PIPELINE_WORKSHOP,
)
from crm.txb.pipelines.actions import PIPELINE_ACTIONS
from crm.txb.pipelines.transitions import (
	action_targets,
	candidates,
	get_transition_map,
	get_transitions,
	is_allowed,
)


class TestTransitionGraph(FrappeTestCase):
	def test_a_fixed_target_appears_as_an_edge(self):
		graph = get_transitions(PIPELINE_DELIVERING_COACHING)
		self.assertIn("Waiting on Review", graph["Submitted"])
		self.assertIn("move_to_review", graph["Submitted"]["Waiting on Review"])

	def test_a_branching_target_appears_as_an_edge(self):
		graph = get_transitions(PIPELINE_WORKSHOP)
		self.assertIn("Workshop ran", graph["Workshop set"])
		self.assertIn("Workshop rescheduling in progress", graph["Workshop set"])

	def test_empty_from_states_expands_to_every_status(self):
		"""`cancel_workshop` declares no from_states, so it is offered from all of them."""
		graph = get_transitions(PIPELINE_WORKSHOP)
		for status in PIPELINE_STATUSES[PIPELINE_WORKSHOP]:
			if status == "Lost":
				continue
			self.assertIn("cancel_workshop", graph[status]["Lost"], status)

	def test_self_loops_are_not_transitions(self):
		"""An action that can land where it started is not a move between columns."""
		graph = get_transitions(PIPELINE_WORKSHOP)
		self.assertNotIn("Lost", graph.get("Lost", {}))

	def test_action_targets_collects_both_shapes(self):
		action = {
			"to_state": None,
			"to_state_map": {"outcome": {"a": "Won", "b": "Session Run"}},
		}
		self.assertEqual(sorted(action_targets(action)), ["Session Run", "Won"])

	def test_action_targets_dedupes(self):
		action = {"to_state": "Lost", "to_state_map": {"x": {"a": "Lost"}}}
		self.assertEqual(action_targets(action), ["Lost"])

	def test_is_allowed_matches_the_graph(self):
		self.assertTrue(
			is_allowed(PIPELINE_INDIVIDUAL_SESSION, "Submitted", "Session Set")
		)
		self.assertFalse(
			is_allowed(PIPELINE_INDIVIDUAL_SESSION, "Submitted", "Session Run")
		)

	def test_is_allowed_permits_a_no_op(self):
		self.assertTrue(is_allowed(PIPELINE_WORKSHOP, "Sold", "Sold"))

	def test_unknown_pipeline_has_no_edges(self):
		self.assertEqual(get_transitions("Not A Pipeline"), {})

	def test_candidates_returns_specs(self):
		found = candidates(PIPELINE_WORKSHOP, "Workshop set", "Lost")
		names = sorted(spec["name"] for spec in found)
		self.assertEqual(
			names, ["cancel_workshop", "run_workshop", "workshop_not_interested"]
		)

	def test_the_map_covers_every_pipeline(self):
		self.assertEqual(sorted(get_transition_map()), sorted(PIPELINE_ACTIONS))

	def test_non_status_changing_actions_are_excluded(self):
		"""Log Coaching Call moves nothing, so it must not appear as an edge."""
		graph = get_transitions(PIPELINE_DELIVERING_COACHING)
		for targets in graph.values():
			for names in targets.values():
				self.assertNotIn("log_coaching_call", names)


class TestNoDeadEnds(FrappeTestCase):
	"""Every status must have a way out, or enforcing the graph traps deals.

	Before TXB-110 added recovery transitions there were four dead ends: Individual
	Session "Follow-up" and "Lost", Workshop "Lost", and Selling Training "Training not
	interested". A mis-clicked "Not Interested" was unrecoverable without a database edit.
	"""

	def test_every_status_has_an_outgoing_transition(self):
		dead_ends = []
		for pipeline in PIPELINE_ACTIONS:
			graph = get_transitions(pipeline)
			for status in PIPELINE_STATUSES.get(pipeline, []):
				if not graph.get(status):
					dead_ends.append((pipeline, status))

		self.assertEqual(dead_ends, [])

	def test_a_lost_individual_session_can_be_reopened(self):
		self.assertTrue(is_allowed(PIPELINE_INDIVIDUAL_SESSION, "Lost", "Submitted"))

	def test_a_lost_workshop_can_be_reopened(self):
		self.assertTrue(is_allowed(PIPELINE_WORKSHOP, "Lost", "Workshop submitted"))

	def test_a_follow_up_bap_can_be_rebooked(self):
		"""Rescheduling parks a BAP in Follow-up; Book a BAP is how it comes back."""
		self.assertTrue(
			is_allowed(PIPELINE_INDIVIDUAL_SESSION, "Follow-up", "Session Set")
		)


class TestTransitionApi(FrappeTestCase):
	def test_the_endpoint_returns_labelled_edges(self):
		from crm.txb.api.transitions import get_transition_map as api_map

		payload = api_map()
		edge = payload["transitions"][PIPELINE_WORKSHOP]["Workshop submitted"]["VCS call set"]

		self.assertEqual(edge, [{"name": "set_vcs_call", "label": "Set VCS Call"}])

	def test_the_endpoint_reports_the_role_rule(self):
		from crm.txb.api.transitions import get_transition_map as api_map

		payload = api_map()
		self.assertIn(PIPELINE_DELIVERING_COACHING, payload["can_change_status"])
		self.assertTrue(payload["can_change_status"][PIPELINE_WORKSHOP])

	def test_available_actions_expose_the_branch_map(self):
		"""The browser pre-fills a branch from the dropped column, so it needs the map."""
		from crm.txb.pipelines.actions import find_action

		spec = find_action(PIPELINE_WORKSHOP, "run_workshop")
		self.assertIn("ws_outcome", spec["to_state_map"])

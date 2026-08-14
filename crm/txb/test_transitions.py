# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-110: the transition graph derived from the action registry."""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.txb.constants import (
	ADMIN_ROLE,
	FIELD_WORKSHOP_SCHEDULED_AT,
	PIPELINE_DELIVERING_COACHING,
	PIPELINE_INDIVIDUAL_SESSION,
	PIPELINE_STATUSES,
	PIPELINE_WORKSHOP,
	STATUS_WORKSHOP_SET,
)
from crm.txb.pipelines.actions import PIPELINE_ACTIONS
from crm.txb.pipelines.individual_session import BAP_TYPES, LOCATION_TYPES
from crm.txb.pipelines.transitions import (
	action_targets,
	candidates,
	get_transition_map,
	get_transitions,
	is_allowed,
)
from crm.txb.test_permissions import ensure_user

COACH = "txb-coach@example.com"
ADMIN = "txb-admin@example.com"


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

	def test_a_universal_action_applies_from_an_off_list_status(self):
		"""Real data holds a Workshop deal sitting in "Active", not a Workshop status.

		`is_available` offers Cancel Workshop there because the action declares no
		from_states. `is_allowed` must agree, or the action is offered and then refused.
		"""
		self.assertTrue(is_allowed(PIPELINE_WORKSHOP, "Active", "Lost"))
		self.assertFalse(is_allowed(PIPELINE_WORKSHOP, "Active", "Workshop set"))


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

	def test_the_endpoint_serves_universal_edges_under_a_star_key(self):
		"""Actions with no from_states apply from any status, including off-list ones.

		A Workshop deal really does sit at "Active", which is not a Workshop status. The
		server allows Cancel Workshop from there; the client must be told so, or it greys
		every column and then refuses a status it just offered.
		"""
		from crm.txb.api.transitions import get_transition_map as api_map

		universal = api_map()["transitions"][PIPELINE_WORKSHOP]["*"]
		names = sorted(action["name"] for action in universal["Lost"])

		self.assertEqual(names, ["cancel_workshop", "workshop_not_interested"])

	def test_pipelines_without_universal_actions_have_no_star_key(self):
		self.assertNotIn("*", get_transition_map()[PIPELINE_DELIVERING_COACHING])

	def test_the_endpoint_reports_whether_the_caller_is_admin(self):
		from crm.txb.api.transitions import get_transition_map as api_map

		self.assertIn("is_admin", api_map())


class TestTransitionEnforcement(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(COACH, ["Sales User"])
		ensure_user(ADMIN, ["Sales User", ADMIN_ROLE])
		frappe.db.commit()  # nosemgrep -- roles must outlive per-test rollback

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.flags.txb_action = None
		frappe.db.rollback()

	def make_deal(self, status, pipeline=PIPELINE_INDIVIDUAL_SESSION, owner=None):
		"""`owner` matters only for execute_action, which is the one status path that
		enforces has_deal_permission (crm/permissions/org_hierarchy.py) and has no
		ignore_permissions escape. A coach may act only on deals they own or are
		assigned, so a test of that path must own the deal.
		"""
		values = {"doctype": "CRM Deal", "pipeline_type": pipeline, "status": status}
		if owner:
			values["deal_owner"] = owner
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def test_an_off_graph_move_is_refused(self):
		deal = self.make_deal("Submitted")
		frappe.set_user(COACH)
		deal.reload()
		deal.status = "Session Run"  # not reachable from Submitted

		with self.assertRaisesRegex(frappe.ValidationError, "cannot move from"):
			deal.save(ignore_permissions=True)

	def test_an_on_graph_move_still_needs_the_action(self):
		"""Submitted -> Session Set is a legal edge, but only Book a BAP may make it."""
		deal = self.make_deal("Submitted")
		frappe.set_user(COACH)
		deal.reload()
		deal.status = "Session Set"

		with self.assertRaisesRegex(frappe.ValidationError, "Take Action"):
			deal.save(ignore_permissions=True)

	def test_an_on_graph_move_through_an_action_is_allowed(self):
		deal = self.make_deal("Submitted")
		frappe.set_user(COACH)
		deal.reload()
		deal.status = "Session Set"
		frappe.flags.txb_action = deal.name
		deal.save(ignore_permissions=True)

		self.assertEqual(
			frappe.db.get_value("CRM Deal", deal.name, "status"), "Session Set"
		)

	def test_an_admin_may_move_off_graph(self):
		"""The documented recovery hatch: a mis-click stays fixable in the UI."""
		deal = self.make_deal("Submitted")
		frappe.set_user(ADMIN)
		deal.reload()
		deal.status = "Session Run"
		deal.save(ignore_permissions=True)

		self.assertEqual(
			frappe.db.get_value("CRM Deal", deal.name, "status"), "Session Run"
		)

	def test_available_actions_report_admin(self):
		from crm.txb.api.actions import get_available_actions

		# get_available_actions enforces frappe.has_permission (read), which for a
		# Sales User is owner/assignment-scoped -- see has_deal_permission -- so COACH
		# must own this deal or the call raises PermissionError before is_admin is reached.
		deal = self.make_deal("Submitted", owner=COACH)
		frappe.set_user(COACH)
		self.assertFalse(get_available_actions(deal.name)["is_admin"])

	def test_inserts_are_exempt(self):
		frappe.set_user(COACH)
		deal = self.make_deal("Session Set")
		self.assertTrue(deal.name)

	def test_a_deal_with_no_state_machine_is_untouched(self):
		"""A deal in a pipeline with no registered actions must keep working.

		`pipeline_type` is a Select whose options carry no leading blank and whose
		`default` is null, so Frappe's `_set_defaults` fills in the FIRST option --
		"Individual Session" -- on any insert that omits it. The blank must therefore be
		set explicitly, or this test silently exercises the Individual Session state
		machine instead of the exemption it is meant to cover.

		"Discovery" and "Demo/Making" are real CRM Deal Status records belonging to no
		pipeline, so the move has no edge in any graph. It must still succeed: a deal
		with no state machine has no transitions to enforce.
		"""
		deal = frappe.get_doc(
			{"doctype": "CRM Deal", "pipeline_type": "", "status": "Discovery"}
		).insert(ignore_permissions=True)
		frappe.set_user(COACH)
		deal.reload()
		deal.status = "Demo/Making"
		deal.save(ignore_permissions=True)

		self.assertEqual(
			frappe.db.get_value("CRM Deal", deal.name, "status"), "Demo/Making"
		)

	def test_editing_other_fields_is_untouched(self):
		deal = self.make_deal("Submitted")
		frappe.set_user(COACH)
		deal.reload()
		deal.session_notes = "unchanged status"
		deal.save(ignore_permissions=True)

		self.assertEqual(
			frappe.db.get_value("CRM Deal", deal.name, "session_notes"),
			"unchanged status",
		)

	def test_execute_action_arms_and_clears_the_origin_flag(self):
		"""The flag is the entire origin mechanism and nothing else exercises it.

		A typo in the flag name would leave every other test green while locking every
		non-Admin out of every status change in production.
		"""
		from crm.txb.api.actions import execute_action

		deal = self.make_deal("Submitted", owner=COACH)
		frappe.set_user(COACH)

		execute_action(
			deal.name,
			"book_bap",
			{
				"bap_type": BAP_TYPES[0],
				"bap_set_by": COACH,
				"bap_date_set": "2026-08-10",
				"bap_datetime": "2026-08-10 10:00:00",
				"bap_location_type": LOCATION_TYPES[0],
			},
		)

		self.assertEqual(
			frappe.db.get_value("CRM Deal", deal.name, "status"), "Session Set"
		)
		self.assertFalse(frappe.flags.get("txb_action"))

	def test_the_origin_flag_is_cleared_when_an_action_fails(self):
		"""Proves the `finally`, rather than trusting it by inspection.

		A flag left armed by a throw would silently exempt every later write in the same
		request from the origin check.
		"""
		from crm.txb.api.actions import execute_action
		from crm.txb.pipelines.actions import find_action

		spec = find_action(PIPELINE_INDIVIDUAL_SESSION, "book_bap")
		original = spec["handler"]

		def boom(deal, values):
			raise frappe.ValidationError("boom")

		spec["handler"] = boom
		try:
			deal = self.make_deal("Submitted", owner=COACH)
			frappe.set_user(COACH)
			with self.assertRaisesRegex(frappe.ValidationError, "boom"):
				execute_action(
					deal.name,
					"book_bap",
					{
						"bap_type": BAP_TYPES[0],
						"bap_set_by": COACH,
						"bap_date_set": "2026-08-10",
						"bap_datetime": "2026-08-10 10:00:00",
						"bap_location_type": LOCATION_TYPES[0],
					},
				)
		finally:
			spec["handler"] = original

		self.assertFalse(frappe.flags.get("txb_action"))


class TestWorkshopScheduleInvariant(FrappeTestCase):
	"""TXB-149: a Workshop deal may not rest in "Workshop set" with no scheduled datetime.

	The retired `Workshop Datetime Modal` Form Script prompted for this in the browser and
	enforced nothing. The rule now lives in `require_workshop_schedule`, so every write path
	-- the native action, a bare status set and the REST API -- reaches it. These tests
	exercise the guard directly rather than through any one surface, because that is the
	point of moving it server-side.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(COACH, ["Sales User"])
		ensure_user(ADMIN, ["Sales User", ADMIN_ROLE])
		frappe.db.commit()  # nosemgrep -- roles must outlive per-test rollback

	def setUp(self):
		if not frappe.get_meta("CRM Deal").has_field(FIELD_WORKSHOP_SCHEDULED_AT):
			self.skipTest(f"{FIELD_WORKSHOP_SCHEDULED_AT} is not installed on this site")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.flags.txb_action = None
		frappe.db.rollback()

	def make_deal(self, status, owner=None):
		values = {"doctype": "CRM Deal", "pipeline_type": PIPELINE_WORKSHOP, "status": status}
		if owner:
			values["deal_owner"] = owner
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def test_a_bare_write_to_workshop_set_without_a_schedule_is_refused(self):
		"""The action flag is armed so the state-machine guard passes and only the schedule
		invariant is left to reject the write -- i.e. an action-shaped write that failed to
		capture the datetime is caught, not just an off-graph jump."""
		deal = self.make_deal("VCS call run")
		frappe.set_user(COACH)
		deal.reload()
		deal.status = STATUS_WORKSHOP_SET
		frappe.flags.txb_action = deal.name

		with self.assertRaisesRegex(frappe.ValidationError, "scheduled"):
			deal.save(ignore_permissions=True)

	def test_a_write_carrying_the_schedule_succeeds(self):
		deal = self.make_deal("VCS call run")
		frappe.set_user(COACH)
		deal.reload()
		deal.set(FIELD_WORKSHOP_SCHEDULED_AT, "2026-09-01 10:00:00")
		deal.status = STATUS_WORKSHOP_SET
		frappe.flags.txb_action = deal.name
		deal.save(ignore_permissions=True)

		self.assertEqual(
			frappe.db.get_value("CRM Deal", deal.name, "status"), STATUS_WORKSHOP_SET
		)

	def test_the_admin_hatch_may_set_workshop_set_by_hand(self):
		"""Admins keep the documented direct-write hatch for edges the graph omits."""
		deal = self.make_deal("VCS call run")
		frappe.set_user(ADMIN)
		deal.reload()
		deal.status = STATUS_WORKSHOP_SET
		deal.save(ignore_permissions=True)

		self.assertEqual(
			frappe.db.get_value("CRM Deal", deal.name, "status"), STATUS_WORKSHOP_SET
		)

	def test_the_native_set_workshop_action_schedules_and_moves(self):
		"""The described UI transition: Set Workshop captures the datetime and lands the
		deal in "Workshop set" through the native action flow."""
		from crm.txb.api.actions import execute_action

		deal = self.make_deal("VCS call run", owner=COACH)
		frappe.set_user(COACH)

		execute_action(
			deal.name,
			"set_workshop",
			{"ws_datetime": "2026-09-01 10:00:00", "ws_name": "Q3 Workshop"},
		)

		row = frappe.db.get_value(
			"CRM Deal", deal.name, ["status", FIELD_WORKSHOP_SCHEDULED_AT], as_dict=True
		)
		self.assertEqual(row["status"], STATUS_WORKSHOP_SET)
		self.assertTrue(row[FIELD_WORKSHOP_SCHEDULED_AT])

	def test_running_a_vcs_call_with_a_confirmed_date_still_schedules(self):
		"""A VCS call confirmed with a date jumps to "Workshop set"; this valid transition
		must keep succeeding, so it now captures the datetime the invariant requires."""
		from crm.txb.api.actions import execute_action

		deal = self.make_deal("VCS call set", owner=COACH)
		frappe.set_user(COACH)

		execute_action(
			deal.name,
			"run_vcs_call",
			{
				"vcs_notes": "Confirmed on the call",
				"ws_confirmed": "Yes",
				"confirmed_ws_date": "2026-09-01 10:00:00",
			},
		)

		row = frappe.db.get_value(
			"CRM Deal", deal.name, ["status", FIELD_WORKSHOP_SCHEDULED_AT], as_dict=True
		)
		self.assertEqual(row["status"], STATUS_WORKSHOP_SET)
		self.assertTrue(row[FIELD_WORKSHOP_SCHEDULED_AT])

	def test_the_invariant_is_scoped_to_the_workshop_set_status(self):
		"""A Workshop deal elsewhere in its pipeline is untouched by the rule."""
		deal = self.make_deal("VCS call run")
		frappe.set_user(COACH)
		deal.reload()
		deal.workshop_name = "Unrelated edit"
		deal.save(ignore_permissions=True)

		self.assertEqual(
			frappe.db.get_value("CRM Deal", deal.name, "workshop_name"), "Unrelated edit"
		)


class TestTransitionMatrix(FrappeTestCase):
	def test_the_matrix_lists_every_pipeline_and_edge(self):
		from crm.txb.transition_matrix import render_matrix

		text = render_matrix()
		self.assertIn("## Workshop", text)
		self.assertIn("Workshop submitted", text)
		self.assertIn("Set VCS Call", text)
		self.assertIn("| Admin only |", text)

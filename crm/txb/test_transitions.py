# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-110: the transition graph derived from the action registry."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.txb.constants import (
	ADMIN_ROLE,
	FIELD_DELIVERY_DEAL,
	FIELD_SALES_SOURCE_DEAL,
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


class TestLogCoachingCall(FrappeTestCase):
	"""Log Coaching Call shows the canonical completed-call count and enforces its rules.

	TXB-152 added the read-only Total Completed Calls count and the mandatory Topic; TXB-153
	makes Next Coaching Call Date conditional on the last-call checkbox. These exercise the
	server-owned schema through `get_available_actions` and the authoritative rules through
	`execute_action`, because the browser is not a boundary: a direct API call must see the
	same read-only count, the same Topic rejection, and the same conditional next-date rule
	the generated form presents.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(COACH, ["Sales User"])
		frappe.db.commit()  # nosemgrep -- roles must outlive per-test rollback

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.flags.txb_action = None
		frappe.db.rollback()

	def make_deal(self, owner=None, total_completed_calls=None):
		values = {
			"doctype": "CRM Deal",
			"pipeline_type": PIPELINE_DELIVERING_COACHING,
			"status": "Active",
		}
		if owner:
			values["deal_owner"] = owner
		if total_completed_calls is not None:
			values["total_completed_calls"] = total_completed_calls
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def log_call_fields(self, deal_name):
		from crm.txb.api.actions import get_available_actions

		actions = get_available_actions(deal_name)["actions"]
		spec = next(a for a in actions if a["name"] == "log_coaching_call")
		return spec["fields"]

	def note_count(self, deal_name):
		from crm.txb.pipelines.common import DEAL_DOCTYPE, NOTE_DOCTYPE

		return frappe.db.count(
			NOTE_DOCTYPE,
			{"reference_doctype": DEAL_DOCTYPE, "reference_docname": deal_name},
		)

	def task_count(self, deal_name, title=None):
		from crm.txb.pipelines.common import DEAL_DOCTYPE, TASK_DOCTYPE

		filters = {"reference_doctype": DEAL_DOCTYPE, "reference_docname": deal_name}
		if title is not None:
			filters["title"] = title
		return frappe.db.count(TASK_DOCTYPE, filters)

	def test_completed_calls_is_read_only_and_sits_immediately_above_topic(self):
		deal = self.make_deal(owner=COACH, total_completed_calls=3)
		frappe.set_user(COACH)

		fields = self.log_call_fields(deal.name)
		names = [f["fieldname"] for f in fields]
		self.assertEqual(names.index("completed_calls") + 1, names.index("topic"))

		completed = next(f for f in fields if f["fieldname"] == "completed_calls")
		self.assertTrue(completed["read_only"])
		self.assertEqual(completed["default"], 3)
		# The label is shown to the coach verbatim; it must read exactly this.
		self.assertEqual(completed["label"], "Total Completed Calls")

	def test_completed_calls_defaults_to_zero_when_the_deal_total_is_unset(self):
		deal = self.make_deal(owner=COACH)
		frappe.set_user(COACH)

		completed = next(
			f for f in self.log_call_fields(deal.name) if f["fieldname"] == "completed_calls"
		)
		self.assertEqual(completed["default"], 0)

	def test_topic_is_marked_required_in_the_generated_schema(self):
		deal = self.make_deal(owner=COACH)
		frappe.set_user(COACH)

		topic = next(f for f in self.log_call_fields(deal.name) if f["fieldname"] == "topic")
		self.assertTrue(topic["reqd"])

	def test_a_whitespace_only_topic_is_rejected_without_touching_the_deal(self):
		"""A direct API call cannot slip a blank Topic past the form, and nothing persists."""
		from crm.txb.api.actions import execute_action

		deal = self.make_deal(owner=COACH, total_completed_calls=2)
		frappe.set_user(COACH)

		with self.assertRaises(frappe.MandatoryError):
			execute_action(
				deal.name,
				"log_coaching_call",
				{
					"call_status": "Completed",
					"delivery_date": "2026-08-17",
					"topic": "   ",
					"call_notes": "Discussed goals",
				},
			)

		# No deal, note, or count change survives the rejected submission.
		self.assertEqual(
			frappe.db.get_value("CRM Deal", deal.name, "total_completed_calls"), 2
		)
		self.assertEqual(self.note_count(deal.name), 0)

	def test_a_valid_log_records_the_call_and_advances_the_canonical_count(self):
		from crm.txb.api.actions import execute_action

		deal = self.make_deal(owner=COACH, total_completed_calls=1)
		frappe.set_user(COACH)

		execute_action(
			deal.name,
			"log_coaching_call",
			{
				"call_status": "Completed",
				"delivery_date": "2026-08-17",
				"topic": "Leadership styles",
				"call_notes": "Reviewed DISC results",
			},
		)

		row = frappe.db.get_value(
			"CRM Deal", deal.name, ["status", "total_completed_calls"], as_dict=True
		)
		# The status-neutral flow is preserved and the count refreshes from canonical state.
		self.assertEqual(row["status"], "Active")
		self.assertEqual(row["total_completed_calls"], 2)
		self.assertEqual(self.note_count(deal.name), 1)

	# ── TXB-153: Next Coaching Call Date is conditional on the last-call checkbox ──

	def test_next_call_date_is_conditionally_visible_and_mandatory_in_the_schema(self):
		"""The schema carries the same eval the modal uses: shown + required until last call."""
		deal = self.make_deal(owner=COACH)
		frappe.set_user(COACH)

		next_call = next(
			f for f in self.log_call_fields(deal.name) if f["fieldname"] == "next_call_date"
		)
		self.assertEqual(next_call["depends_on"], "eval:!doc.is_last_call")
		self.assertEqual(next_call["mandatory_depends_on"], "eval:!doc.is_last_call")

	def test_a_non_last_call_without_a_next_date_is_rejected_atomically(self):
		"""Unticked last-call + no next date is refused before any write, exactly as the modal."""
		from crm.txb.api.actions import execute_action

		deal = self.make_deal(owner=COACH, total_completed_calls=4)
		frappe.set_user(COACH)

		with self.assertRaises(frappe.MandatoryError):
			execute_action(
				deal.name,
				"log_coaching_call",
				{
					"call_status": "Completed",
					"delivery_date": "2026-08-17",
					"topic": "Leadership styles",
					"call_notes": "Reviewed DISC results",
					"is_last_call": 0,
				},
			)

		# The rejection is atomic: no deal, note, task, or count change survives.
		self.assertEqual(
			frappe.db.get_value("CRM Deal", deal.name, "total_completed_calls"), 4
		)
		self.assertEqual(self.note_count(deal.name), 0)
		self.assertEqual(self.task_count(deal.name), 0)

	def test_a_non_last_call_with_a_next_date_logs_and_schedules_the_follow_up(self):
		from crm.txb.api.actions import execute_action

		deal = self.make_deal(owner=COACH, total_completed_calls=1)
		frappe.set_user(COACH)

		execute_action(
			deal.name,
			"log_coaching_call",
			{
				"call_status": "Completed",
				"delivery_date": "2026-08-17",
				"topic": "Leadership styles",
				"call_notes": "Reviewed DISC results",
				"is_last_call": 0,
				"next_call_date": "2026-08-24 10:00:00",
			},
		)

		row = frappe.db.get_value(
			"CRM Deal", deal.name, ["status", "total_completed_calls"], as_dict=True
		)
		self.assertEqual(row["status"], "Active")
		self.assertEqual(row["total_completed_calls"], 2)
		self.assertEqual(self.note_count(deal.name), 1)
		# A next-call follow-up task is created when it is not the last call.
		self.assertEqual(self.task_count(deal.name, title="Next Coaching Call"), 1)

	def test_a_last_call_needs_no_next_date_and_schedules_no_follow_up(self):
		from crm.txb.api.actions import execute_action

		deal = self.make_deal(owner=COACH, total_completed_calls=5)
		frappe.set_user(COACH)

		execute_action(
			deal.name,
			"log_coaching_call",
			{
				"call_status": "Completed",
				"delivery_date": "2026-08-17",
				"topic": "Wrap up",
				"call_notes": "Final session",
				"is_last_call": 1,
			},
		)

		row = frappe.db.get_value(
			"CRM Deal",
			deal.name,
			["status", "total_completed_calls", "custom_last_coaching_call"],
			as_dict=True,
		)
		# Status-neutral, the canonical count still advances, and no follow-up task is made.
		self.assertEqual(row["status"], "Active")
		self.assertEqual(row["total_completed_calls"], 6)
		self.assertEqual(row["custom_last_coaching_call"], "Yes")
		self.assertEqual(self.note_count(deal.name), 1)
		self.assertEqual(self.task_count(deal.name, title="Next Coaching Call"), 0)


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


class TestLogReachNote(FrappeTestCase):
	"""TXB-164: Log a reach is persisted as one native FCRM Note linked to the Lead.

	The Notes tab is sourced exclusively from linked ``FCRM Note`` rows
	(``crm/api/activities.py``), so the reach has to be a real Note rather than the ``Info``
	comment the earlier TXB-157/TXB-163 delivery wrote -- that comment could never surface
	under Notes. These pin the corrected contract: exactly one linked Note with labelled,
	escaped content; no substitute Info comment; a rejected required field writes nothing; and
	a status-save failure rolls the note back so nothing partial survives.
	"""

	NOTE_DOCTYPE = "FCRM Note"

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.flags.txb_action = None
		frappe.db.rollback()

	def make_lead(self, status="New"):
		return frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": "Reach", "status": status}
		).insert(ignore_permissions=True)

	def linked_notes(self, lead_name):
		return frappe.db.get_all(
			self.NOTE_DOCTYPE,
			filters={"reference_doctype": "CRM Lead", "reference_docname": lead_name},
			fields=["name", "title", "content"],
		)

	def info_comment_count(self, lead_name):
		return frappe.db.count(
			"Comment",
			{
				"reference_doctype": "CRM Lead",
				"reference_name": lead_name,
				"comment_type": "Info",
			},
		)

	def test_a_valid_reach_creates_one_linked_note_with_labelled_escaped_content(self):
		"""Exactly one Note, correctly referenced and titled, preserving every submitted field."""
		from crm.txb.api.actions import CONTACTED_STATUS, log_reach

		lead = self.make_lead()

		result = log_reach(
			lead.name,
			activity={
				"summary": "<b>Called the CEO</b>",
				"follow_up_context": "Send the deck",
				"follow_up_date": "2026-09-01",
			},
		)

		notes = self.linked_notes(lead.name)
		self.assertEqual(len(notes), 1)
		note = notes[0]
		self.assertEqual(note["title"], "Log a reach")
		self.assertEqual(result["note"], note["name"])
		self.assertEqual(result["status"], CONTACTED_STATUS)

		content = note["content"]
		# Every field is surfaced under its own explicit label.
		self.assertIn("Reach summary", content)
		self.assertIn("Follow-up context", content)
		self.assertIn("Follow-up date", content)
		# The submitted values are preserved, and user text is escaped rather than rendered.
		self.assertIn("&lt;b&gt;Called the CEO&lt;/b&gt;", content)
		self.assertNotIn("<b>Called the CEO", content)
		self.assertIn("Send the deck", content)
		self.assertIn("2026-09-01", content)

		# The status moved to Contacted and no Info comment stands in for the Note.
		self.assertEqual(frappe.db.get_value("CRM Lead", lead.name, "status"), CONTACTED_STATUS)
		self.assertEqual(self.info_comment_count(lead.name), 0)

	def test_an_absent_follow_up_date_renders_a_clear_not_set_marker(self):
		"""The optional date is never dropped: a blank one is rendered as a not-set value."""
		from crm.txb.api.actions import log_reach

		lead = self.make_lead()

		log_reach(
			lead.name,
			activity={"summary": "Left a voicemail", "follow_up_context": "Retry Monday"},
		)

		note = self.linked_notes(lead.name)[0]
		self.assertIn("Follow-up date", note["content"])
		self.assertIn("Not set", note["content"])

	def test_a_blank_reach_is_rejected_and_writes_nothing(self):
		"""A missing required field is refused before any write: no Note, no comment, no status."""
		from crm.txb.api.actions import log_reach

		lead = self.make_lead()

		with self.assertRaises(frappe.MandatoryError):
			log_reach(
				lead.name,
				activity={"summary": "   ", "follow_up_context": "Retry Monday"},
			)

		self.assertEqual(self.linked_notes(lead.name), [])
		self.assertEqual(self.info_comment_count(lead.name), 0)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead.name, "status"), "New")

	def test_a_status_save_failure_rolls_the_note_back(self):
		"""If the Contacted save fails, the note insert is rolled back with it -- nothing partial."""
		from crm.txb.api import actions
		from crm.txb.api.actions import log_reach

		lead = self.make_lead()

		# Force the status write to fail after the note insert by targeting a status that does
		# not exist, so `doc.save()` raises on the invalid CRM Lead Status link.
		original = actions.CONTACTED_STATUS
		actions.CONTACTED_STATUS = "Nonexistent Reach Status"
		try:
			with self.assertRaises(Exception):
				log_reach(
					lead.name,
					activity={"summary": "Called the CEO", "follow_up_context": "Send the deck"},
				)
		finally:
			actions.CONTACTED_STATUS = original

		# The savepoint rolled both writes back: no note, no substitute comment, status unchanged.
		self.assertEqual(self.linked_notes(lead.name), [])
		self.assertEqual(self.info_comment_count(lead.name), 0)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead.name, "status"), "New")


class TestCoachingHandover(FrappeTestCase):
	"""TXB-126: a Won sales Opportunity hands over to one linked Delivering Coaching deal.

	These cover the whole idempotent handover service (crm.txb.pipelines.common):

	- both source pipelines (Individual Session via run_bap, Workshop via workshop_won)
	  create one Submitted delivery deal carrying Organization, Contacts/primary flags, the
	  source owner, handover notes and the durable sales-source reference, with both deals
	  linked to each other;
	- a repeated Won reuses that one delivery deal (sequential retry) and a lost duplicate-key
	  race recovers the winner instead of spawning a second;
	- the insert and both links share the caller's transaction;
	- Workshop QR attendee candidates keep their own records and are neither consumed nor
	  overwritten by the aggregate handover.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.skip = not frappe.get_meta("CRM Deal").has_field(FIELD_SALES_SOURCE_DEAL)

	def setUp(self):
		if self.skip:
			self.skipTest(f"{FIELD_SALES_SOURCE_DEAL} is not installed on this site")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.flags.txb_action = None
		frappe.db.rollback()

	# ── helpers ──────────────────────────────────────────────────────────────────────

	def make_contact(self, first_name):
		return frappe.get_doc(
			{"doctype": "Contact", "first_name": first_name}
		).insert(ignore_permissions=True)

	def make_source(self, pipeline, status, *, owner="Administrator"):
		"""A source Opportunity with an Organization and two Contacts, one primary."""
		organization = frappe.get_doc(
			{"doctype": "CRM Organization", "organization_name": frappe.generate_hash("Org", 8)}
		).insert(ignore_permissions=True)

		primary = self.make_contact("Primary")
		secondary = self.make_contact("Secondary")

		deal = frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"pipeline_type": pipeline,
				"status": status,
				"organization": organization.name,
				"deal_owner": owner,
				"contacts": [
					{"contact": primary.name, "is_primary": 1},
					{"contact": secondary.name, "is_primary": 0},
				],
			}
		)
		return deal.insert(ignore_permissions=True)

	def delivery_deals(self, source_name):
		return frappe.get_all(
			"CRM Deal",
			filters={FIELD_SALES_SOURCE_DEAL: source_name},
			fields=["name", "status", "pipeline_type", "organization", "deal_owner"],
		)

	def source_notes(self, source_name):
		"""Every FCRM Note attached to the source deal, newest content included."""
		return frappe.get_all(
			"FCRM Note",
			filters={"reference_doctype": "CRM Deal", "reference_docname": source_name},
			fields=["title", "content"],
		)

	# ── one linked delivery deal per Won source ──────────────────────────────────────

	def test_won_individual_session_creates_one_linked_delivery_deal(self):
		from crm.txb.pipelines.individual_session import run_bap

		source = self.make_source(PIPELINE_INDIVIDUAL_SESSION, "Session Run")
		run_bap(source, {"outcome": "Won - proceed to coaching"})
		source.save(ignore_permissions=True)

		created = self.delivery_deals(source.name)
		self.assertEqual(len(created), 1)
		delivery = frappe.get_doc("CRM Deal", created[0].name)

		self.assertEqual(delivery.pipeline_type, PIPELINE_DELIVERING_COACHING)
		self.assertEqual(delivery.status, "Submitted")
		self.assertEqual(delivery.organization, source.organization)
		self.assertEqual(delivery.deal_owner, source.deal_owner)
		self.assertEqual(delivery.get(FIELD_SALES_SOURCE_DEAL), source.name)
		self.assertIn(source.name, delivery.custom_delivery_notes)

		# Contacts carried across with their primary flags preserved.
		self.assertEqual(len(delivery.contacts), 2)
		primary = [c.contact for c in delivery.contacts if c.is_primary]
		self.assertEqual(len(primary), 1)

		# Both deals navigate to each other.
		self.assertEqual(
			frappe.db.get_value("CRM Deal", source.name, FIELD_DELIVERY_DEAL), delivery.name
		)

	def test_won_workshop_creates_one_linked_delivery_deal(self):
		from crm.txb.pipelines.workshop import workshop_won

		source = self.make_source(PIPELINE_WORKSHOP, "Workshop ran")
		workshop_won(source, {"coaching_notes": "Ready for delivery"})
		source.save(ignore_permissions=True)

		created = self.delivery_deals(source.name)
		self.assertEqual(len(created), 1)
		delivery = frappe.get_doc("CRM Deal", created[0].name)
		self.assertEqual(delivery.status, "Submitted")
		self.assertEqual(delivery.get(FIELD_SALES_SOURCE_DEAL), source.name)
		self.assertIn("Ready for delivery", delivery.custom_delivery_notes)
		self.assertEqual(
			frappe.db.get_value("CRM Deal", source.name, FIELD_DELIVERY_DEAL), delivery.name
		)

	# ── canonical action/status surface: Won / Sold create and document the handover ──

	def test_session_won_action_creates_and_documents_the_handover(self):
		"""Completing an Individual Session as Won through the real action surface creates one
		Submitted delivery deal and writes a source note carrying the submitted handover
		information plus a navigable reference to that target."""
		from crm.txb.api.actions import execute_action

		source = self.make_source(PIPELINE_INDIVIDUAL_SESSION, "Session Run")

		execute_action(
			source.name,
			"session_won",
			{"won_notes": "Signed today", "coaching_notes": "Ready for delivery"},
		)

		self.assertEqual(frappe.db.get_value("CRM Deal", source.name, "status"), "Won")

		created = self.delivery_deals(source.name)
		self.assertEqual(len(created), 1)
		self.assertEqual(created[0].status, "Submitted")

		note = "\n".join(n.content for n in self.source_notes(source.name))
		self.assertIn("Signed today", note)
		self.assertIn("Ready for delivery", note)
		# Navigable reference to the delivery target, not a bare identifier.
		self.assertIn(f'/crm/deals/{created[0].name}', note)

	def test_workshop_sold_action_creates_and_documents_the_handover(self):
		"""Completing a Workshop as Sold through the real action surface (label "Sold",
		to_state "Sold") creates one Submitted delivery deal and documents the handover with a
		navigable reference on the source."""
		from crm.txb.api.actions import execute_action
		from crm.txb.pipelines.workshop import WORKSHOP_WON

		# The action names the terminal sales status it lands on.
		self.assertEqual(WORKSHOP_WON["label"], "Sold")
		self.assertEqual(WORKSHOP_WON["to_state"], "Sold")

		source = self.make_source(PIPELINE_WORKSHOP, "Workshop ran")

		execute_action(source.name, "workshop_won", {"coaching_notes": "Ready for delivery"})

		self.assertEqual(frappe.db.get_value("CRM Deal", source.name, "status"), "Sold")

		created = self.delivery_deals(source.name)
		self.assertEqual(len(created), 1)
		self.assertEqual(created[0].status, "Submitted")

		note = "\n".join(n.content for n in self.source_notes(source.name))
		self.assertIn("Ready for delivery", note)
		self.assertIn(f'/crm/deals/{created[0].name}', note)

	def test_repeated_sold_action_write_reuses_the_one_delivery_deal(self):
		"""A retried Sold handover -- re-running the handover authority after the action has
		already landed -- reuses the one aggregate delivery deal instead of spawning a second,
		and re-points the reverse link at the survivor."""
		from crm.txb.api.actions import execute_action
		from crm.txb.pipelines.common import create_coaching_deal

		source = self.make_source(PIPELINE_WORKSHOP, "Workshop ran")

		execute_action(source.name, "workshop_won", {"coaching_notes": "Ready for delivery"})
		first = self.delivery_deals(source.name)
		self.assertEqual(len(first), 1)

		# A retry of the idempotent handover authority (double click / replayed request).
		source.reload()
		retry = create_coaching_deal(source)
		source.save(ignore_permissions=True)

		self.assertEqual(retry, first[0].name)
		self.assertEqual(len(self.delivery_deals(source.name)), 1)

	# ── idempotency: retries and races reuse the one delivery deal ───────────────────

	def test_sequential_retry_reuses_the_same_delivery_deal(self):
		from crm.txb.pipelines.common import create_coaching_deal

		source = self.make_source(PIPELINE_INDIVIDUAL_SESSION, "Session Run")

		first = create_coaching_deal(source)
		second = create_coaching_deal(source)

		self.assertEqual(first, second)
		self.assertEqual(len(self.delivery_deals(source.name)), 1)

	def test_duplicate_key_race_recovers_and_reuses_the_winner(self):
		"""Force the query-before-insert to miss so the insert path runs against an existing
		aggregate: the unique sales-source constraint raises, and the service recovers the
		winner instead of creating a second delivery deal or surfacing the conflict."""
		from crm.txb.pipelines import common

		source = self.make_source(PIPELINE_INDIVIDUAL_SESSION, "Session Run")

		winner = common.insert_coaching_deal(source, "")  # the concurrent writer's deal

		# First call (pre-insert check) sees nothing and forces our insert; the recovery call
		# inside the except clause then finds the winner the unique constraint pointed us at.
		with patch.object(common, "find_coaching_deal", side_effect=[None, winner]) as finder:
			result = common.create_coaching_deal(source)

		self.assertEqual(result, winner)
		self.assertEqual(finder.call_count, 2)
		self.assertEqual(len(self.delivery_deals(source.name)), 1)

	def test_reverse_link_is_repaired_when_missing(self):
		"""An existing forward handover whose reverse link was lost self-heals on the next run
		without creating a second delivery deal."""
		from crm.txb.pipelines.common import create_coaching_deal

		source = self.make_source(PIPELINE_INDIVIDUAL_SESSION, "Session Run")

		existing = create_coaching_deal(source)
		source.db_set(FIELD_DELIVERY_DEAL, None)
		source.reload()

		result = create_coaching_deal(source)
		source.save(ignore_permissions=True)

		self.assertEqual(result, existing)
		self.assertEqual(len(self.delivery_deals(source.name)), 1)
		self.assertEqual(
			frappe.db.get_value("CRM Deal", source.name, FIELD_DELIVERY_DEAL), existing
		)

	# ── transaction safety ───────────────────────────────────────────────────────────

	def test_insert_and_links_share_the_callers_transaction(self):
		"""The handover writes inside the caller's action transaction: a rollback of that
		transaction takes the delivery deal and the reverse link with it, leaving nothing
		half-applied."""
		from crm.txb.pipelines.common import create_coaching_deal

		source = self.make_source(PIPELINE_INDIVIDUAL_SESSION, "Session Run")

		frappe.db.savepoint("txb_test_txn")
		create_coaching_deal(source)
		source.save(ignore_permissions=True)
		self.assertEqual(len(self.delivery_deals(source.name)), 1)

		frappe.db.rollback(save_point="txb_test_txn")

		self.assertEqual(len(self.delivery_deals(source.name)), 0)
		self.assertIsNone(
			frappe.db.get_value("CRM Deal", source.name, FIELD_DELIVERY_DEAL)
		)

	# ── Workshop QR coexistence ──────────────────────────────────────────────────────

	def test_workshop_qr_candidate_coexists_with_the_aggregate_handover(self):
		"""A registration attendee candidate uses `custom_source_deal`, not the dedicated
		sales-source link, so the aggregate Won handover neither finds, consumes nor
		overwrites it -- both records survive independently."""
		from crm.txb.pipelines.common import create_coaching_deal

		source = self.make_source(PIPELINE_WORKSHOP, "Workshop ran")

		candidate = frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"pipeline_type": PIPELINE_DELIVERING_COACHING,
				"status": "Waiting on Review",
				"custom_source_deal": source.name,
				"first_name": "Attendee",
			}
		).insert(ignore_permissions=True)

		delivery = create_coaching_deal(source)

		# The candidate is untouched: same status, never adopted as the aggregate.
		self.assertNotEqual(delivery, candidate.name)
		row = frappe.db.get_value(
			"CRM Deal", candidate.name, ["status", FIELD_SALES_SOURCE_DEAL], as_dict=True
		)
		self.assertEqual(row.status, "Waiting on Review")
		self.assertIsNone(row.get(FIELD_SALES_SOURCE_DEAL))

		# Exactly one aggregate handover exists, and it is not the candidate.
		created = self.delivery_deals(source.name)
		self.assertEqual([c.name for c in created], [delivery])


class TestHandoverLinkFieldsPatch(FrappeTestCase):
	"""TXB-173: the sales <-> delivery handover links install through the standard Frappe patch
	path, idempotently and even after a partial prior run, and land as navigable read-only Link
	fields the existing Opportunity layout surfaces without manual site configuration.
	"""

	def test_the_patch_is_registered_in_the_standard_frappe_path(self):
		"""A normal `bench migrate` runs it: it is listed in the app's patches.txt."""
		patches = frappe.get_file_items(frappe.get_app_path("crm", "patches.txt"))
		self.assertIn("crm.patches.v1_0.add_handover_link_custom_fields", patches)

	def test_installed_links_are_navigable_read_only_fields(self):
		"""Meta coverage: after migrate the Opportunity carries both links as read-only,
		non-hidden Link(CRM Deal) fields -- clickable references, never editable relationships."""
		meta = frappe.get_meta("CRM Deal")
		for fieldname in (FIELD_SALES_SOURCE_DEAL, FIELD_DELIVERY_DEAL):
			if not meta.has_field(fieldname):
				self.skipTest(f"{fieldname} is not installed on this site")
			df = meta.get_field(fieldname)
			self.assertEqual(df.fieldtype, "Link")
			self.assertEqual(df.options, "CRM Deal")
			self.assertTrue(df.read_only)
			self.assertFalse(df.hidden)

	def test_only_the_missing_link_is_created_on_a_partial_site(self):
		"""A site that already has the sales-source link but lost the reverse link installs just
		the missing one -- the guard is per field, so migrate converges without erroring."""
		from crm.patches.v1_0 import add_handover_link_custom_fields as handover_patch

		class _Meta:
			def __init__(self, present):
				self._present = present

			def has_field(self, fieldname):
				return fieldname in self._present

		with patch.object(
			handover_patch.frappe, "get_meta", return_value=_Meta({FIELD_SALES_SOURCE_DEAL})
		), patch.object(handover_patch, "create_custom_fields") as create, patch.object(
			handover_patch.frappe, "clear_cache"
		):
			handover_patch.execute()

		created = [f["fieldname"] for f in create.call_args.args[0]["CRM Deal"]]
		self.assertEqual(created, [FIELD_DELIVERY_DEAL])

	def test_a_fully_installed_site_is_a_clean_no_op(self):
		"""Re-running the patch when both links exist creates nothing and clears no cache."""
		from crm.patches.v1_0 import add_handover_link_custom_fields as handover_patch

		class _Meta:
			def has_field(self, fieldname):
				return True

		with patch.object(
			handover_patch.frappe, "get_meta", return_value=_Meta()
		), patch.object(handover_patch, "create_custom_fields") as create, patch.object(
			handover_patch.frappe, "clear_cache"
		) as clear:
			handover_patch.execute()

		create.assert_not_called()
		clear.assert_not_called()

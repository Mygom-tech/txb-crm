# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-105: only the Admin role may change Delivering Coaching statuses.

The rule is enforced in `validate()` because every status-writing path reaches it -- the
status field, Kanban drag, Take Action and direct REST calls. These tests exercise the
rule directly rather than through any one of those paths, since that is the point.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.txb.constants import ADMIN_ROLE, PIPELINE_DELIVERING_COACHING
from crm.txb.permissions import can_change_status
from crm.txb.pipelines.actions import get_actions, resolve_to_state
from crm.txb.api.actions import is_available, is_permitted

COACH = "txb-coach@example.com"
ADMIN = "txb-admin@example.com"


def ensure_user(email: str, roles: list[str]):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)

	user = frappe.get_doc("User", email)
	user.add_roles(*roles)
	return user


class TestStatusPermission(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(COACH, ["Sales User"])
		ensure_user(ADMIN, ["Sales User", ADMIN_ROLE])
		frappe.db.commit()  # nosemgrep -- roles must outlive per-test rollback

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_admin_may_change_delivering_coaching_status(self):
		self.assertTrue(can_change_status(PIPELINE_DELIVERING_COACHING, ADMIN))

	def test_coach_may_not_change_delivering_coaching_status(self):
		self.assertFalse(can_change_status(PIPELINE_DELIVERING_COACHING, COACH))

	def test_other_pipelines_are_unrestricted(self):
		"""The rule is scoped to one pipeline; coaches keep working everywhere else."""
		for pipeline in ("Workshop", "Individual Session", "Selling Training", None):
			self.assertTrue(can_change_status(pipeline, COACH), pipeline)

	def test_administrator_is_always_allowed(self):
		self.assertTrue(can_change_status(PIPELINE_DELIVERING_COACHING, "Administrator"))

	def test_guard_blocks_a_coach_changing_status(self):
		deal = self.make_deal(status="Active")

		frappe.set_user(COACH)
		deal.reload()
		deal.status = "Inactive"

		with self.assertRaises(frappe.PermissionError):
			deal.save(ignore_permissions=True)

	def test_guard_blocks_the_duplicate_delivery_status_field(self):
		"""custom_delivery_status would otherwise be an unguarded second door."""
		deal = self.make_deal(status="Active")
		if not deal.meta.has_field("custom_delivery_status"):
			self.skipTest("custom_delivery_status is not installed on this site")

		frappe.set_user(COACH)
		deal.reload()
		deal.custom_delivery_status = "Inactive"

		with self.assertRaises(frappe.PermissionError):
			deal.save(ignore_permissions=True)

	def test_guard_allows_an_admin_changing_status(self):
		deal = self.make_deal(status="Active")

		frappe.set_user(ADMIN)
		deal.reload()
		deal.status = "Inactive"
		deal.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("CRM Deal", deal.name, "status"), "Inactive")

	def test_coach_may_still_edit_other_fields(self):
		"""The ticket is explicit that coaches' daily work must not break."""
		deal = self.make_deal(status="Active")

		frappe.set_user(COACH)
		deal.reload()
		deal.custom_coaching_call_notes = "Session went well"
		deal.save(ignore_permissions=True)

		self.assertEqual(
			frappe.db.get_value("CRM Deal", deal.name, "custom_coaching_call_notes"),
			"Session went well",
		)

	def test_coach_may_create_a_delivering_coaching_deal(self):
		"""Won sessions and workshops spawn these; blocking inserts would break that."""
		frappe.set_user(COACH)

		deal = frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"pipeline_type": PIPELINE_DELIVERING_COACHING,
				"status": "Submitted",
			}
		).insert(ignore_permissions=True)

		self.assertTrue(deal.name)

	def make_deal(self, status: str):
		return frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"pipeline_type": PIPELINE_DELIVERING_COACHING,
				"status": status,
			}
		).insert(ignore_permissions=True)


class TestActionVisibility(FrappeTestCase):
	"""What the Take Action menu offers must match what execute_action would allow."""

	def actions_for(self, status: str, may_change_status: bool):
		return [
			action["label"]
			for action in get_actions(PIPELINE_DELIVERING_COACHING)
			if is_available(action, status) and is_permitted(action, may_change_status)
		]

	def test_coach_only_ever_sees_log_coaching_call(self):
		self.assertEqual(self.actions_for("Active", False), ["Log Coaching Call"])

	def test_coach_sees_nothing_in_admin_only_states(self):
		for status in ("Submitted", "Waiting on Review", "Contract Cleared", "On Hold", "Inactive"):
			self.assertEqual(self.actions_for(status, False), [], status)

	def test_admin_sees_the_transitions_for_the_current_state(self):
		self.assertEqual(
			self.actions_for("Submitted", True), ["Move to Waiting on Review"]
		)
		self.assertEqual(self.actions_for("Waiting on Review", True), ["Clear Contract"])
		self.assertIn("Put on Hold", self.actions_for("Active", True))

	def test_actions_are_hidden_outside_their_from_states(self):
		self.assertNotIn("Clear Contract", self.actions_for("Active", True))
		self.assertNotIn("Reactivate", self.actions_for("Active", True))

	def test_log_coaching_call_does_not_move_the_status(self):
		"""Which is exactly why it is the one action coaches keep."""
		log_call = next(
			a for a in get_actions(PIPELINE_DELIVERING_COACHING) if a["name"] == "log_coaching_call"
		)
		self.assertIsNone(log_call["to_state"])
		self.assertFalse(log_call["admin_only"])

	def test_every_other_delivering_coaching_action_is_restricted(self):
		for action in get_actions(PIPELINE_DELIVERING_COACHING):
			if action["name"] == "log_coaching_call":
				continue
			self.assertTrue(action["admin_only"], action["name"])

	def test_every_action_declares_whether_it_changes_status(self):
		"""Never inferred: a branching action has no to_state until the form returns."""
		for action in get_actions(PIPELINE_DELIVERING_COACHING):
			self.assertIn("changes_status", action, action["name"])

	def test_a_branching_action_is_still_gated(self):
		"""Regression: inferring from to_state would let this through on a coach.

		A branching action carries no fixed to_state -- its target comes from the answers --
		so the permission check must key on `changes_status`, not on to_state being set.
		"""
		branching = {
			"name": "negotiation_result",
			"label": "Negotiation Result",
			"from_states": ["Training negotiations"],
			"to_state": None,
			"to_state_map": {"decision": {"Lost": "Training not interested"}},
			"changes_status": True,
			"admin_only": False,
			"fields": [],
		}

		self.assertFalse(is_permitted(branching, may_change_status=False))
		self.assertTrue(is_permitted(branching, may_change_status=True))

	def test_resolve_to_state_picks_the_target_from_the_answers(self):
		branching = {
			"to_state": None,
			"to_state_map": {
				"decision": {
					"Agreement reached": "Contract signed",
					"Follow-up needed": "Training negotiations",
					"Lost": "Training not interested",
				}
			},
		}

		self.assertEqual(
			resolve_to_state(branching, {"decision": "Agreement reached"}), "Contract signed"
		)
		self.assertEqual(
			resolve_to_state(branching, {"decision": "Lost"}), "Training not interested"
		)
		self.assertIsNone(resolve_to_state(branching, {"decision": "Something else"}))
		self.assertIsNone(resolve_to_state(branching, {}))

	def test_resolve_to_state_prefers_a_fixed_target(self):
		self.assertEqual(
			resolve_to_state({"to_state": "Active", "to_state_map": {"x": {"y": "Other"}}}, {"x": "y"}),
			"Active",
		)

	def test_every_from_state_is_selectable_in_its_pipeline(self):
		"""An action whose from-state is not in PIPELINE_STATUSES can never be reached.

		This caught a real dead end: "Training submitted" is Selling Training's entry
		status, but both Form Script copies of the status map omitted it, so the status
		could not be selected and "Set Discovery Meeting" was unreachable.
		"""
		from crm.txb.constants import PIPELINE_STATUSES
		from crm.txb.pipelines.actions import PIPELINE_ACTIONS

		unreachable = [
			(pipeline, action["name"], state)
			for pipeline, actions in PIPELINE_ACTIONS.items()
			for action in actions
			for state in (action.get("from_states") or [])
			if state not in PIPELINE_STATUSES.get(pipeline, [])
		]

		self.assertEqual(unreachable, [])

	def test_every_target_state_is_selectable_in_its_pipeline(self):
		"""The converse: an action must not park a deal in a status the UI cannot show."""
		from crm.txb.constants import PIPELINE_STATUSES
		from crm.txb.pipelines.actions import PIPELINE_ACTIONS

		# "Lost" is shared terminal state set with a reason rather than a pipeline status.
		shared_terminal = {"Lost"}

		stranded = []
		for pipeline, actions in PIPELINE_ACTIONS.items():
			allowed = set(PIPELINE_STATUSES.get(pipeline, [])) | shared_terminal
			for action in actions:
				targets = [action.get("to_state")] + [
					target
					for mapping in (action.get("to_state_map") or {}).values()
					for target in mapping.values()
				]
				stranded += [
					(pipeline, action["name"], t) for t in targets if t and t not in allowed
				]

		self.assertEqual(stranded, [])

	def test_log_coaching_call_stays_unrestricted(self):
		log_call = next(
			a for a in get_actions(PIPELINE_DELIVERING_COACHING) if a["name"] == "log_coaching_call"
		)
		self.assertTrue(is_permitted(log_call, may_change_status=False))

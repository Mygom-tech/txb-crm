# Kanban Drag-and-Drop Through Take Action — Implementation Plan (TXB-110)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a kanban drag open the matching Take Action modal, grey out illegal columns, and commit the status only when that modal saves — governed by one transition graph derived from the existing action registry and enforced server-side.

**Architecture:** The transition graph is *derived* from `PIPELINE_ACTIONS`, never hand-authored. A whitelisted endpoint serves it to the browser for greying; `validate()` enforces it for every write path including REST. The frontend fills in the `requestKanbanTransition` seam that `specs/kanban-transition-confirm.md` deliberately left open, and the detail-page status dropdown routes through the same flow.

**Tech Stack:** Frappe v15 (Python 3.11), Vue 3 + frappe-ui, vuedraggable 4.1 / sortablejs 1.15, pytest via `bench run-tests`, vitest + happy-dom.

**Spec:** `specs/kanban-take-action-transitions.md`

## Global Constraints

- Branch: `feature/TXB-110-kanban-transitions`. Never push to `main`.
- Never run `bench migrate` or Prisma-style migrations. Backend changes here need no schema change.
- Tabs for indentation in Python (this app's style), 2-space in JS/Vue.
- Run `npx prettier@3.2.5 --write` on changed frontend files. **Pinned version** — a newer prettier reformats unrelated code.
- Frontend tests: `cd frontend && yarn test:run`. Backend: `bench --site localhost run-tests --module crm.txb.<module>`.
- No `any`-style escape hatches: declare targets, never infer them (the `changes_status` lesson from PR #15).
- Admin role means Frappe's `System Manager`; the constant is `crm.txb.constants.ADMIN_ROLE`.
- Every new pure JS util must be added to `frontend/vitest.config.js` `coverage.include`.

---

# Phase 0 — Make the backend test suite runnable

### Task 0: Migrate the txb tests to the v15 test base class

`crm/txb/test_permissions.py`, `test_doc_events.py` and `test_registration_token.py` all import `frappe.tests.IntegrationTestCase`. That name does not exist in Frappe v15 — this bench is 15.116.0, and v15 provides `frappe.tests.utils.FrappeTestCase`. `bench run-tests` therefore fails at import for every one of them, so no backend task in this plan can be verified until this is fixed.

`docs/deployment-guide.md` records that production is deliberately on v15, so the fix is to move the tests to the v15 API — **not** to upgrade the bench.

**Files:**
- Modify: `crm/txb/test_permissions.py:12`
- Modify: `crm/txb/test_doc_events.py:13`
- Modify: `crm/txb/test_registration_token.py:5`

**Interfaces:**
- Produces: all three modules run under `bench --site localhost run-tests`. Every later task in this plan depends on that.

- [ ] **Step 1: Confirm the failure**

Run from the bench root (`/home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be`):

```bash
bench --site localhost run-tests --module crm.txb.test_permissions
```

Expected: `ImportError: cannot import name 'IntegrationTestCase' from 'frappe.tests'`.

- [ ] **Step 2: Swap the base class in all three modules**

In each of the three files, replace the import:

```python
from frappe.tests.utils import FrappeTestCase
```

and every `class Foo(IntegrationTestCase):` with `class Foo(FrappeTestCase):`.

`FrappeTestCase` rolls back at class cleanup rather than per test, so the explicit `frappe.db.rollback()` these modules already call in `tearDown` is what provides per-test isolation. Keep it.

- [ ] **Step 3: Run all three modules**

```bash
bench --site localhost run-tests --module crm.txb.test_permissions
bench --site localhost run-tests --module crm.txb.test_doc_events
bench --site localhost run-tests --module crm.txb.test_registration_token
```

Expected: all import cleanly and run.

**If any test now fails on its assertions** — as opposed to failing to import — that is a real, previously invisible failure. Do not change the assertion to make it pass. Record each failure verbatim in the report with the test name and the output, fix it only if the fix is obviously a test-side mistake (e.g. a status string that does not exist), and escalate anything that looks like a genuine product bug via `DONE_WITH_CONCERNS`.

- [ ] **Step 4: Commit**

```bash
git add crm/txb/test_permissions.py crm/txb/test_doc_events.py crm/txb/test_registration_token.py
git commit -m "test(txb): run under the v15 FrappeTestCase base class"
```

---

# Phase 1 — Backend: derive and enforce the graph

### Task 1: Declare the Lost targets that handlers hide

Five actions reach `Lost` inside their handler via `mark_lost`, leaving `to_state: None`. A target the registry cannot see cannot be derived or enforced.

**Files:**
- Modify: `crm/txb/pipelines/workshop.py` (`CANCEL_WORKSHOP`, `WORKSHOP_NOT_INTERESTED`, `RUN_WORKSHOP`)
- Modify: `crm/txb/pipelines/individual_session.py` (`CANCEL_BAP`, `NOT_INTERESTED`)
- Test: `crm/txb/test_permissions.py` (existing `TestActionVisibility`)

**Interfaces:**
- Produces: every action with `changes_status: True` now declares at least one target via `to_state` or `to_state_map`. Task 2 relies on this.

- [ ] **Step 1: Write the failing test**

Add to `class TestActionVisibility` in `crm/txb/test_permissions.py`:

```python
	def test_every_status_changing_action_declares_a_target(self):
		"""A target set only inside a handler is invisible to the transition graph.

		`mark_lost` used to be the only record that these actions reach "Lost", which
		meant the derived graph could not know about them -- the same blind spot that
		let `changes_status` be inferred wrongly from `to_state` before PR #15.
		"""
		from crm.txb.pipelines.actions import PIPELINE_ACTIONS

		undeclared = [
			(pipeline, action["name"])
			for pipeline, actions in PIPELINE_ACTIONS.items()
			for action in actions
			if action.get("changes_status")
			and not action.get("to_state")
			and not (action.get("to_state_map") or {})
		]

		self.assertEqual(undeclared, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site localhost run-tests --module crm.txb.test_permissions --test test_every_status_changing_action_declares_a_target`
Expected: FAIL listing 4 actions — `cancel_workshop`, `workshop_not_interested`, `cancel_bap`, `not_interested`.

- [ ] **Step 3: Declare the targets**

In `crm/txb/pipelines/workshop.py`, `CANCEL_WORKSHOP` and `WORKSHOP_NOT_INTERESTED`:

```python
	# Declared, not left to the handler: an invisible target cannot be enforced by the
	# transition graph. `cancel_workshop` still sets the reason via mark_lost, and
	# execute_action then assigns the same value -- idempotent.
	"to_state": "Lost",
```

(replacing `"to_state": None,  # handler sets Lost together with its reason`)

In `crm/txb/pipelines/individual_session.py`, `CANCEL_BAP` and `NOT_INTERESTED`: the same replacement.

In `crm/txb/pipelines/workshop.py`, `RUN_WORKSHOP`, complete the map:

```python
	"to_state_map": {
		"ws_outcome": {
			"Won - proceed to coaching": "Workshop ran",
			"Follow-up needed": "Workshop rescheduling in progress",
			# Declared so the graph knows this branch exists; mark_lost still writes
			# the reason alongside it.
			"Lost": "Lost",
		}
	},
```

- [ ] **Step 4: Run the full module to verify nothing regressed**

Run: `bench --site localhost run-tests --module crm.txb.test_permissions`
Expected: PASS, including the pre-existing `test_every_target_state_is_selectable_in_its_pipeline`, which now covers these five actions.

- [ ] **Step 5: Commit**

```bash
git add crm/txb/pipelines/workshop.py crm/txb/pipelines/individual_session.py crm/txb/test_permissions.py
git commit -m "refactor(txb): declare the Lost targets that handlers were hiding"
```

---

### Task 2: Derive the transition graph

**Files:**
- Create: `crm/txb/pipelines/transitions.py`
- Test: `crm/txb/test_transitions.py`

**Interfaces:**
- Consumes: `PIPELINE_ACTIONS`, `get_actions`, `find_action` from `crm.txb.pipelines.actions`; `PIPELINE_STATUSES` from `crm.txb.constants`.
- Produces:
  - `action_targets(action: dict) -> list[str]`
  - `action_sources(pipeline_type: str | None, action: dict) -> list[str]`
  - `get_transitions(pipeline_type: str | None) -> dict[str, dict[str, list[str]]]` — `{from: {to: [action_name, …]}}`
  - `get_transition_map() -> dict[str, dict]` — keyed by pipeline
  - `candidates(pipeline_type, from_status, to_status) -> list[dict]` — action specs
  - `is_allowed(pipeline_type, from_status, to_status) -> bool`

- [ ] **Step 1: Write the failing test**

Create `crm/txb/test_transitions.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site localhost run-tests --module crm.txb.test_transitions`
Expected: FAIL — `ModuleNotFoundError: crm.txb.pipelines.transitions`.

- [ ] **Step 3: Write the implementation**

Create `crm/txb/pipelines/transitions.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bench --site localhost run-tests --module crm.txb.test_transitions`
Expected: PASS (12 tests).

- [ ] **Step 5: Commit**

```bash
git add crm/txb/pipelines/transitions.py crm/txb/test_transitions.py
git commit -m "feat(txb): derive the transition graph from the action registry"
```

---

### Task 3: Recovery transitions — remove the dead ends

Enforcing the graph as it stands would trap deals. Four states have no way out.

**Files:**
- Modify: `crm/txb/pipelines/individual_session.py` (extend `BOOK_BAP`, add `REOPEN`)
- Modify: `crm/txb/pipelines/workshop.py` (add `REOPEN`)
- Modify: `crm/txb/pipelines/selling_training.py` (add `REOPEN`)
- Test: `crm/txb/test_transitions.py`

**Interfaces:**
- Consumes: `add_note`, `lines` from `crm.txb.pipelines.common`; `get_transitions` from Task 2.
- Produces: an action named `reopen` in each of the three pipelines, and `"Follow-up"` added to `book_bap`'s `from_states`.

- [ ] **Step 1: Write the failing test**

Add to `crm/txb/test_transitions.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site localhost run-tests --module crm.txb.test_transitions --test test_every_status_has_an_outgoing_transition`
Expected: FAIL listing exactly **three** dead ends — `('Workshop', 'Lost')`, `('Individual Session', 'Lost')`, `('Selling Training', 'Training not interested')`.

Individual Session `Follow-up` is deliberately **not** in that list. Since Task 1 declared `to_state: "Lost"` on `cancel_bap` / `not_interested` (which have empty `from_states`, so they apply from every status), `Follow-up` does have one outgoing edge — to `Lost`. It is still a trap in practice: the only way out of a rescheduled BAP is to lose it. That is why this task also extends `book_bap`, and why `test_a_follow_up_bap_can_be_rebooked` exists as a separate assertion. Both tests must pass at the end; only the dead-end one fails now.

- [ ] **Step 3: Extend `book_bap` and add the reopen handler**

In `crm/txb/pipelines/individual_session.py`, change `BOOK_BAP`:

```python
	# "Follow-up" is where a rescheduled BAP lands. Booking is exactly how it comes back,
	# and the form already records everything re-booking needs -- so this is a from_state,
	# not a new action.
	"from_states": ["Submitted", "Follow-up"],
```

Add the handler above `INDIVIDUAL_SESSION_ACTIONS` in the same file:

```python
def reopen(deal, data: dict):
	"""Return a lost opportunity to the top of the pipeline.

	The lost reason is cleared, otherwise the deal carries a stale explanation for a
	state it is no longer in.
	"""
	deal.lost_reason = None
	if deal.meta.has_field("lost_reason_detail"):
		deal.lost_reason_detail = ""

	add_note(deal, "Opportunity Reopened", lines(f"Reason: {data.get('reopen_reason', '')}"))
```

And the action spec:

```python
REOPEN = {
	"name": "reopen",
	"label": "Reopen",
	"from_states": ["Lost"],
	"to_state": "Submitted",
	"changes_status": True,
	"admin_only": False,
	"handler": reopen,
	"fields": [
		{
			"fieldname": "reopen_reason",
			"label": "Why is this being reopened?",
			"fieldtype": "Small Text",
			"reqd": 1,
		},
	],
}
```

Append `REOPEN` to `INDIVIDUAL_SESSION_ACTIONS`.

Confirm `add_note` and `lines` are imported in this file; add them to the existing `from crm.txb.pipelines.common import ...` if not.

- [ ] **Step 4: Add the same action to Workshop and Selling Training**

In `crm/txb/pipelines/workshop.py` — identical handler and spec, except:

```python
	"from_states": ["Lost"],
	"to_state": "Workshop submitted",
```

In `crm/txb/pipelines/selling_training.py` — identical, except:

```python
	"from_states": [STATUS_NOT_INTERESTED],
	"to_state": "Training submitted",
```

Append `REOPEN` to `WORKSHOP_ACTIONS` and `SELLING_TRAINING_ACTIONS`.

The handler body is repeated in all three files rather than shared: each pipeline module owns its own handlers today, and the three bodies are three lines that will diverge if reopening ever needs to record something pipeline-specific. If a fourth appears, move it to `common.py`.

- [ ] **Step 5: Run the tests**

Run: `bench --site localhost run-tests --module crm.txb.test_transitions`
Then: `bench --site localhost run-tests --module crm.txb.test_permissions`
Expected: both PASS. `test_every_from_state_is_selectable_in_its_pipeline` and `test_every_target_state_is_selectable_in_its_pipeline` now cover the new actions.

- [ ] **Step 6: Commit**

```bash
git add crm/txb/pipelines/ crm/txb/test_transitions.py
git commit -m "feat(txb): add reopen actions and re-book from Follow-up, removing every dead end"
```

---

### Task 4: Serve the graph to the browser

**Files:**
- Create: `crm/txb/api/transitions.py`
- Modify: `crm/txb/api/actions.py` (add `to_state_map` to the payload)
- Test: `crm/txb/test_transitions.py`

**Interfaces:**
- Produces: whitelisted `crm.txb.api.transitions.get_transition_map()` returning
  `{"transitions": {pipeline: {from: {to: [{"name","label"}, …]}}}, "can_change_status": {pipeline: bool}}`.
- Produces: `get_available_actions` entries gain `"to_state_map"`, which Task 6's `prefillFor` needs.

- [ ] **Step 1: Write the failing test**

Add to `crm/txb/test_transitions.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site localhost run-tests --module crm.txb.test_transitions`
Expected: FAIL — `ModuleNotFoundError: crm.txb.api.transitions`.

- [ ] **Step 3: Write the endpoint**

Create `crm/txb/api/transitions.py`:

```python
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
```

In `crm/txb/api/actions.py`, inside `get_available_actions`, extend the appended dict:

```python
			available.append(
				{
					"name": action["name"],
					"label": action["label"],
					"to_state": action["to_state"],
					# The board pre-selects a branch value from the column the card was
					# dropped on, which it can only do if it can see the mapping.
					"to_state_map": action.get("to_state_map") or {},
					"fields": action["fields"],
				}
			)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bench --site localhost run-tests --module crm.txb.test_transitions`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add crm/txb/api/transitions.py crm/txb/api/actions.py crm/txb/test_transitions.py
git commit -m "feat(txb): serve the transition graph and expose branch maps to the client"
```

---

### Task 5: Enforce the graph in `validate()`

**Files:**
- Modify: `crm/txb/permissions.py`
- Modify: `crm/txb/api/actions.py` (set the origin flag)
- Modify: `crm/hooks.py:201-212` (register the guard)
- Test: `crm/txb/test_transitions.py`

**Interfaces:**
- Consumes: `is_allowed` (Task 2).
- Produces: `is_admin(user=None) -> bool`, `guard_transition(doc, method=None)`, and the request-scoped flag `frappe.flags.txb_action`.

- [ ] **Step 1: Write the failing test**

Add to `crm/txb/test_transitions.py`:

```python
import frappe

from crm.txb.constants import ADMIN_ROLE
from crm.txb.test_permissions import ensure_user

COACH = "txb-coach@example.com"
ADMIN = "txb-admin@example.com"


class TestTransitionEnforcement(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(COACH, ["Sales User"])
		ensure_user(ADMIN, ["Sales User", ADMIN_ROLE])
		frappe.db.commit()  # nosemgrep -- roles must outlive per-test rollback

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.flags.txb_action = False
		frappe.db.rollback()

	def make_deal(self, status, pipeline=PIPELINE_INDIVIDUAL_SESSION):
		return frappe.get_doc(
			{"doctype": "CRM Deal", "pipeline_type": pipeline, "status": status}
		).insert(ignore_permissions=True)

	def test_an_off_graph_move_is_refused(self):
		deal = self.make_deal("Submitted")
		frappe.set_user(COACH)
		deal.reload()
		deal.status = "Session Run"  # not reachable from Submitted

		with self.assertRaises(frappe.ValidationError):
			deal.save(ignore_permissions=True)

	def test_an_on_graph_move_still_needs_the_action(self):
		"""Submitted -> Session Set is a legal edge, but only Book a BAP may make it."""
		deal = self.make_deal("Submitted")
		frappe.set_user(COACH)
		deal.reload()
		deal.status = "Session Set"

		with self.assertRaises(frappe.ValidationError):
			deal.save(ignore_permissions=True)

	def test_an_on_graph_move_through_an_action_is_allowed(self):
		deal = self.make_deal("Submitted")
		frappe.set_user(COACH)
		deal.reload()
		deal.status = "Session Set"
		frappe.flags.txb_action = True
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site localhost run-tests --module crm.txb.test_transitions`
Expected: FAIL — the off-graph and bare-write cases save successfully because nothing guards them yet.

- [ ] **Step 3: Write the guard**

Append to `crm/txb/permissions.py` (and extend its imports):

```python
from crm.txb.pipelines.actions import PIPELINE_ACTIONS
from crm.txb.pipelines.transitions import is_allowed


def is_admin(user: str | None = None) -> bool:
	"""The CRM's "Admin" role, plus the Administrator account itself."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True

	return ADMIN_ROLE in frappe.get_roles(user)


def guard_transition(doc, method=None):
	"""Reject a status change that the pipeline's state machine does not describe.

	Two things are enforced, in the order a user would want to hear them:

	1. the edge must exist -- you cannot jump from Submitted to Session Run;
	2. the write must come from `execute_action` -- because a *legal* edge written bare
	   skips the handler, and a deal reaching "Session Set" with no BAP type, no date and
	   no note is exactly the inconsistency this ticket exists to remove.

	Admins are exempt from both. That is the documented recovery hatch (TXB-110 decision
	2): without it, a mis-clicked "Not Interested" would need a database edit.

	Inserts are exempt, as in `guard_status_change` -- won sessions and workshops spawn
	Delivering Coaching deals, and blocking that breaks the handover.
	"""
	if doc.is_new():
		return

	if not doc.has_value_changed("status"):
		return

	# A pipeline with no registered actions has no state machine to enforce. Stock deals
	# and any future pipeline must keep working untouched.
	if not PIPELINE_ACTIONS.get(doc.pipeline_type):
		return

	if is_admin():
		return

	previous = doc.get_doc_before_save()
	from_status = previous.status if previous else None

	if not is_allowed(doc.pipeline_type, from_status, doc.status):
		frappe.throw(
			_('A {0} opportunity cannot move from "{1}" to "{2}".').format(
				_(doc.pipeline_type), _(from_status or ""), _(doc.status or "")
			),
			frappe.ValidationError,
			title=_("Transition not allowed"),
		)

	if not frappe.flags.get("txb_action"):
		frappe.throw(
			_('Change the status of a {0} opportunity through Take Action, so the details that go with the change are recorded.').format(
				_(doc.pipeline_type)
			),
			frappe.ValidationError,
			title=_("Use Take Action"),
		)
```

- [ ] **Step 4: Set the origin flag in `execute_action`**

In `crm/txb/api/actions.py`, wrap the mutating half of `execute_action`. Replace from `values = parse_data(data)` through `doc.save()` with:

```python
	values = parse_data(data)
	validate_required(spec, values)

	# Tells `guard_transition` this write is an action rather than a bare status set. A
	# request-scoped flag, cleared in `finally` so a throw cannot leave it armed for the
	# rest of the request.
	frappe.flags.txb_action = True
	try:
		spec["handler"](doc, values)

		to_state = resolve_to_state(spec, values)
		if to_state:
			doc.status = to_state

		doc.save()
	finally:
		frappe.flags.txb_action = False
```

- [ ] **Step 5: Register the hook**

In `crm/hooks.py`, the `"CRM Deal"` entry:

```python
	"CRM Deal": {
		# Guards every status-writing path: status field, Kanban, Take Action and the API.
		# guard_status_change is the role rule (TXB-105); guard_transition is the state
		# machine (TXB-110). Role first, so a coach on Delivering Coaching hears the more
		# specific message.
		"validate": [
			"crm.txb.permissions.guard_status_change",
			"crm.txb.permissions.guard_transition",
		],
```

- [ ] **Step 6: Run the tests**

Run: `bench --site localhost run-tests --module crm.txb.test_transitions`
Then: `bench --site localhost run-tests --module crm.txb.test_permissions`
Then: `bench --site localhost run-tests --module crm.txb.test_doc_events`
Expected: all PASS. If `test_permissions` fails on a coach status change, check that `guard_status_change` still runs first.

- [ ] **Step 7: Commit**

```bash
git add crm/txb/permissions.py crm/txb/api/actions.py crm/hooks.py crm/txb/test_transitions.py
git commit -m "feat(txb): enforce the transition graph in validate(), closing the API path"
```

---

### Task 6: Generate the QA transition matrix

The ticket's Definition of Done asks QA for a source → target matrix per pipeline. Generate it from the registry rather than maintaining it by hand.

**Files:**
- Create: `crm/txb/transition_matrix.py`
- Test: `crm/txb/test_transitions.py`

**Interfaces:**
- Produces: `render_matrix() -> str` (markdown), and a `bench execute` entry point.

- [ ] **Step 1: Write the failing test**

Add to `crm/txb/test_transitions.py`:

```python
class TestTransitionMatrix(FrappeTestCase):
	def test_the_matrix_lists_every_pipeline_and_edge(self):
		from crm.txb.transition_matrix import render_matrix

		text = render_matrix()
		self.assertIn("## Workshop", text)
		self.assertIn("Workshop submitted", text)
		self.assertIn("Set VCS Call", text)
		self.assertIn("| Admin only |", text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site localhost run-tests --module crm.txb.test_transitions --test test_the_matrix_lists_every_pipeline_and_edge`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write it**

Create `crm/txb/transition_matrix.py`:

```python
"""The transition matrix, rendered for QA.

Generated from the registry so it cannot drift from what the code enforces. Run with:

    bench --site localhost execute crm.txb.transition_matrix.write --kwargs "{'path': 'docs/transition-matrix.md'}"
"""

from crm.txb.pipelines.actions import PIPELINE_ACTIONS, find_action
from crm.txb.pipelines.transitions import get_transitions


def render_matrix() -> str:
	out = ["# Opportunity transition matrix", ""]
	out.append("Generated from `crm.txb.pipelines.actions`. Do not edit by hand.")
	out.append("")

	for pipeline in PIPELINE_ACTIONS:
		out.append(f"## {pipeline}")
		out.append("")
		out.append("| From | To | Action | Admin only |")
		out.append("| --- | --- | --- | --- |")

		graph = get_transitions(pipeline)
		for source in sorted(graph):
			for target in sorted(graph[source]):
				for name in graph[source][target]:
					spec = find_action(pipeline, name)
					admin = "yes" if spec.get("admin_only") else "no"
					out.append(f"| {source} | {target} | {spec['label']} | {admin} |")

		out.append("")

	return "\n".join(out)


def write(path: str = "transition-matrix.md"):
	with open(path, "w") as handle:
		handle.write(render_matrix())

	return path
```

- [ ] **Step 4: Run test and generate the artifact**

Run: `bench --site localhost run-tests --module crm.txb.test_transitions`
Expected: PASS.

Then: `bench --site localhost execute crm.txb.transition_matrix.write --kwargs "{'path': 'apps/crm/docs/transition-matrix.md'}"`
Expected: the file exists and lists all four pipelines.

- [ ] **Step 5: Commit**

```bash
git add crm/txb/transition_matrix.py crm/txb/test_transitions.py docs/transition-matrix.md
git commit -m "feat(txb): generate the QA transition matrix from the registry"
```

---

# Phase 2 — Frontend: shared transition logic

### Task 7: Pure transition helpers

**Files:**
- Create: `frontend/src/utils/dealTransitions.js`
- Create: `frontend/tests/unit/dealTransitions.test.js`
- Modify: `frontend/vitest.config.js` (coverage include)

**Interfaces:**
- Produces:
  - `allowedTargets(transitions, pipeline, from) -> string[]`
  - `candidateActions(transitions, pipeline, from, to, available) -> object[]`
  - `prefillFor(action, to) -> object`
  - `canDropOn(transitions, pipeline, from, to, canChangeStatus) -> boolean`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/unit/dealTransitions.test.js`:

```js
import { describe, it, expect } from 'vitest'
import {
  allowedTargets,
  candidateActions,
  canDropOn,
  prefillFor,
} from '@/utils/dealTransitions'

const TRANSITIONS = {
  Workshop: {
    'Workshop set': {
      'Workshop ran': [{ name: 'run_workshop', label: 'Run Workshop' }],
      Lost: [
        { name: 'run_workshop', label: 'Run Workshop' },
        { name: 'cancel_workshop', label: 'Cancel Workshop' },
      ],
    },
  },
}

const AVAILABLE = [
  { name: 'run_workshop', label: 'Run Workshop', fields: [] },
  { name: 'cancel_workshop', label: 'Cancel Workshop', fields: [] },
  { name: 'reschedule_workshop', label: 'Reschedule', fields: [] },
]

describe('allowedTargets', () => {
  it('lists the reachable statuses', () => {
    expect(allowedTargets(TRANSITIONS, 'Workshop', 'Workshop set').sort()).toEqual([
      'Lost',
      'Workshop ran',
    ])
  })

  it('returns nothing for an unknown pipeline or status', () => {
    expect(allowedTargets(TRANSITIONS, 'Nope', 'Workshop set')).toEqual([])
    expect(allowedTargets(TRANSITIONS, 'Workshop', 'Nope')).toEqual([])
    expect(allowedTargets(undefined, 'Workshop', 'Workshop set')).toEqual([])
  })
})

describe('candidateActions', () => {
  it('keeps only the actions the server currently offers', () => {
    const found = candidateActions(
      TRANSITIONS,
      'Workshop',
      'Workshop set',
      'Lost',
      AVAILABLE,
    )
    expect(found.map((a) => a.name)).toEqual(['run_workshop', 'cancel_workshop'])
  })

  it('drops an edge action the server has filtered out by role', () => {
    const found = candidateActions(TRANSITIONS, 'Workshop', 'Workshop set', 'Lost', [
      AVAILABLE[1],
    ])
    expect(found.map((a) => a.name)).toEqual(['cancel_workshop'])
  })

  it('returns nothing for an edge that does not exist', () => {
    expect(
      candidateActions(TRANSITIONS, 'Workshop', 'Workshop set', 'Sold', AVAILABLE),
    ).toEqual([])
  })
})

describe('prefillFor', () => {
  const runWorkshop = {
    to_state_map: {
      ws_outcome: {
        'Won - proceed to coaching': 'Workshop ran',
        'Follow-up needed': 'Workshop rescheduling in progress',
        Lost: 'Lost',
      },
    },
  }

  it('pre-selects the branch value that reaches the dropped column', () => {
    expect(prefillFor(runWorkshop, 'Workshop ran')).toEqual({
      ws_outcome: 'Won - proceed to coaching',
    })
  })

  it('pre-fills nothing when two values reach the same target', () => {
    const runBap = {
      to_state_map: {
        outcome: {
          'Won - proceed to coaching': 'Won',
          'Follow-up needed': 'Session Run',
          'Not interested': 'Session Run',
        },
      },
    }
    expect(prefillFor(runBap, 'Session Run')).toEqual({})
    expect(prefillFor(runBap, 'Won')).toEqual({ outcome: 'Won - proceed to coaching' })
  })

  it('pre-fills nothing for an action with a fixed target', () => {
    expect(prefillFor({ to_state: 'Sold' }, 'Sold')).toEqual({})
    expect(prefillFor(null, 'Sold')).toEqual({})
  })
})

describe('canDropOn', () => {
  it('refuses every column when the user may not change the status', () => {
    expect(
      canDropOn(TRANSITIONS, 'Workshop', 'Workshop set', 'Workshop ran', false),
    ).toBe(false)
  })

  it('allows a reachable column', () => {
    expect(
      canDropOn(TRANSITIONS, 'Workshop', 'Workshop set', 'Workshop ran', true),
    ).toBe(true)
  })

  it('refuses an unreachable column', () => {
    expect(canDropOn(TRANSITIONS, 'Workshop', 'Workshop set', 'Sold', true)).toBe(false)
  })

  it('allows the column the card came from', () => {
    expect(
      canDropOn(TRANSITIONS, 'Workshop', 'Workshop set', 'Workshop set', true),
    ).toBe(true)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && yarn vitest run tests/unit/dealTransitions.test.js`
Expected: FAIL — cannot resolve `@/utils/dealTransitions`.

- [ ] **Step 3: Write the implementation**

Create `frontend/src/utils/dealTransitions.js`:

```js
/**
 * Which statuses a deal may move to, and which action makes each move.
 *
 * The graph comes from the backend (crm.txb.api.transitions.get_transition_map), which
 * derives it from the same action registry that execute_action enforces. These helpers
 * are pure so they can be tested without a browser or a Frappe site.
 *
 * Nothing here is a security boundary — guard_transition is.
 */

/**
 * Statuses reachable from `from` in this pipeline.
 *
 * @param {Object} transitions  {pipeline: {from: {to: [{name,label}]}}}
 * @returns {string[]}
 */
export function allowedTargets(transitions, pipeline, from) {
  return Object.keys(transitions?.[pipeline]?.[from] || {})
}

/**
 * The actions that make this move AND that the server is currently offering.
 *
 * Intersecting with `available` matters: the graph is static, but
 * get_available_actions has already filtered by role, so an edge whose only action is
 * admin-only disappears for a coach instead of failing at submit time.
 *
 * @param {Array} available - from crm.txb.api.actions.get_available_actions
 * @returns {Array} the full action objects, in graph order
 */
export function candidateActions(transitions, pipeline, from, to, available) {
  const names = (transitions?.[pipeline]?.[from]?.[to] || []).map((a) => a.name)
  const byName = new Map((available || []).map((a) => [a.name, a]))

  return names.filter((name) => byName.has(name)).map((name) => byName.get(name))
}

/**
 * Branch values that land the deal in `to`, so dropping on a column pre-selects the
 * answer that column implies (TXB-110 decision 3).
 *
 * When more than one value reaches the same target nothing is pre-filled — `run_bap`
 * reaches "Session Run" from both "Follow-up needed" and "Not interested", and picking
 * one silently is the guessing this design rejected.
 *
 * @returns {Object} {fieldname: value}, possibly empty
 */
export function prefillFor(action, to) {
  const prefill = {}

  for (const [fieldname, targets] of Object.entries(action?.to_state_map || {})) {
    const values = Object.entries(targets)
      .filter(([, target]) => target === to)
      .map(([value]) => value)

    if (values.length === 1) prefill[fieldname] = values[0]
  }

  return prefill
}

/**
 * Whether a card dragged from `from` may be dropped on `to`.
 *
 * A user who may not change the status at all (a coach on Delivering Coaching, per
 * TXB-105) is refused every column.
 */
export function canDropOn(transitions, pipeline, from, to, canChangeStatus) {
  if (!canChangeStatus) return false
  if (from === to) return true

  return allowedTargets(transitions, pipeline, from).includes(to)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && yarn vitest run tests/unit/dealTransitions.test.js`
Expected: PASS (14 tests).

- [ ] **Step 5: Add to coverage and format**

In `frontend/vitest.config.js`, add to `coverage.include` — note `pipelineStatuses.js` and `takeAction.js` were omitted when they were added, so include them too:

```js
        'src/utils/kanbanTransitions.js',
        'src/utils/pipelineStatuses.js',
        'src/utils/takeAction.js',
        'src/utils/dealTransitions.js',
```

Run: `cd frontend && npx prettier@3.2.5 --write src/utils/dealTransitions.js tests/unit/dealTransitions.test.js vitest.config.js`

- [ ] **Step 6: Commit**

```bash
git add frontend/src/utils/dealTransitions.js frontend/tests/unit/dealTransitions.test.js frontend/vitest.config.js
git commit -m "feat(deals): pure helpers for the deal transition graph"
```

---

### Task 8: Transition store

**Files:**
- Create: `frontend/src/stores/transitions.js`

**Interfaces:**
- Consumes: nothing from earlier frontend tasks.
- Produces: `transitionsStore()` exposing `transitionMap` (a `createResource`), `transitions` (computed object) and `canChangeStatusFor(pipeline)`.

- [ ] **Step 1: Write the store**

Create `frontend/src/stores/transitions.js`, mirroring `stores/statuses.js`:

```js
import { computed } from 'vue'
import { defineStore } from 'pinia'
import { createResource } from 'frappe-ui'

/**
 * The opportunity transition graph, fetched once and shared.
 *
 * Mirrors the pipelineStatuses resource in stores/statuses.js: one source of truth,
 * served by the backend, cached by frappe-ui's module-level resource cache so every
 * board and every deal page reads the same answer.
 */
export const transitionsStore = defineStore('crm-transitions', () => {
  const transitionMap = createResource({
    url: 'crm.txb.api.transitions.get_transition_map',
    cache: 'deal-transitions',
    initialData: { transitions: {}, can_change_status: {} },
    auto: true,
  })

  const transitions = computed(() => transitionMap.data?.transitions || {})

  /**
   * Whether this user may move statuses in a pipeline at all (TXB-105).
   * Unknown pipelines are unrestricted, matching the backend default.
   */
  function canChangeStatusFor(pipeline) {
    return transitionMap.data?.can_change_status?.[pipeline] !== false
  }

  return { transitionMap, transitions, canChangeStatusFor }
})
```

- [ ] **Step 2: Verify it loads**

Run: `cd frontend && yarn build`
Expected: build succeeds with no unresolved import.

- [ ] **Step 3: Format and commit**

```bash
cd frontend && npx prettier@3.2.5 --write src/stores/transitions.js
cd .. && git add frontend/src/stores/transitions.js
git commit -m "feat(deals): shared store for the transition graph"
```

---

### Task 9: Route a deal status drop through Take Action

**Files:**
- Modify: `frontend/src/utils/kanbanTransitions.js`
- Modify: `frontend/tests/unit/kanbanTransitions.test.js`

**Interfaces:**
- Consumes: `candidateActions`, `prefillFor` (Task 7); `runAction`, `actionFields`, `requiredFieldnames` (`@/utils/takeAction`); `createDialog` (`@/utils/dialogs`).
- Produces: `requestKanbanTransition(ctx)` now resolves `{ proceed: boolean, alreadySaved: boolean, finalStatus: string }`. `ctx` gains `pipelineType` and `transitions` and `available`.
- Produces: `chooseAction(candidates, to) -> Promise<object|null>` (exported for tests).

- [ ] **Step 1: Write the failing test**

The file already mocks `@/utils/dialogs` with `vi.mock` and reads options through a local `lastDialogOptions()`. Reuse both — do not invent a new mechanism.

Extend the existing import to include `chooseAction`:

```js
import {
  requestKanbanTransition,
  confirmKanbanTransition,
  chooseAction,
} from '@/utils/kanbanTransitions'
```

Append to `frontend/tests/unit/kanbanTransitions.test.js` (the module-level `ctx` is a `CRM Lead`, which is exactly the non-deal board this must not disturb):

```js
describe('requestKanbanTransition — non-deal boards keep the confirm', () => {
  beforeEach(() => {
    createDialog.mockReset()
  })

  it('wraps the confirm result in the outcome shape', async () => {
    const promise = requestKanbanTransition(ctx)
    lastDialogOptions()
      .actions.find((a) => a.label === 'OK')
      .onClick({ close: vi.fn() })

    await expect(promise).resolves.toEqual({
      proceed: true,
      alreadySaved: false,
      finalStatus: 'Contacted',
    })
  })

  it('reports refusal when cancelled', async () => {
    const promise = requestKanbanTransition(ctx)
    lastDialogOptions()
      .actions.find((a) => a.label === 'Cancel')
      .onClick({ close: vi.fn() })

    await expect(promise).resolves.toMatchObject({
      proceed: false,
      alreadySaved: false,
    })
  })
})

describe('chooseAction', () => {
  beforeEach(() => {
    createDialog.mockReset()
  })

  it('does not ask when only one action applies', async () => {
    const only = { name: 'set_vcs_call', label: 'Set VCS Call' }
    await expect(chooseAction([only], 'VCS call set')).resolves.toBe(only)
    expect(createDialog).not.toHaveBeenCalled()
  })

  it('asks which action when several reach the same status', async () => {
    const candidates = [
      { name: 'cancel_workshop', label: 'Cancel Workshop' },
      { name: 'workshop_not_interested', label: 'Mark as "Not Interested"' },
    ]
    const promise = chooseAction(candidates, 'Lost')

    const options = lastDialogOptions()
    expect(options.actions.map((a) => a.label)).toEqual([
      'Cancel Workshop',
      'Mark as "Not Interested"',
    ])

    options.actions[1].onClick({ close: vi.fn() })
    await expect(promise).resolves.toBe(candidates[1])
  })

  it('resolves null when dismissed', async () => {
    const promise = chooseAction(
      [
        { name: 'a', label: 'A' },
        { name: 'b', label: 'B' },
      ],
      'Lost',
    )
    lastDialogOptions().onDismiss()
    await expect(promise).resolves.toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && yarn vitest run tests/unit/kanbanTransitions.test.js`
Expected: FAIL — the current implementation resolves a bare `true`, not an object.

- [ ] **Step 3: Rewrite `requestKanbanTransition`**

Replace the body of `frontend/src/utils/kanbanTransitions.js`'s `requestKanbanTransition` and add the deal path. Keep `confirmKanbanTransition` exactly as it is.

```js
import { createDialog } from '@/utils/dialogs'
import { candidateActions, prefillFor } from '@/utils/dealTransitions'
import { runAction } from '@/utils/takeAction'

const DEAL_DOCTYPE = 'CRM Deal'

/**
 * Decide — and for deals, perform — a kanban column transition.
 *
 * Deal status boards run the Take Action flow: pick the action (asking when more than
 * one applies), open its form pre-filled from the dropped column, and let the server
 * commit. Every other board keeps the plain confirm it has today.
 *
 * @param {Object} ctx - { doctype, itemName, fieldname, fieldLabel, from, to,
 *                         pipelineType, transitions, available }
 * @returns {Promise<{proceed: boolean, alreadySaved: boolean, finalStatus: string}>}
 *   `alreadySaved` tells the caller not to write the field itself — execute_action
 *   already did. `finalStatus` is where the deal actually ended up, which for a
 *   branching action may not be the column it was dropped on.
 */
export async function requestKanbanTransition(ctx) {
  if (ctx.doctype === DEAL_DOCTYPE && ctx.fieldname === 'status') {
    return dealStatusTransition(ctx)
  }

  const proceed = await confirmKanbanTransition(ctx)
  return { proceed, alreadySaved: false, finalStatus: ctx.to }
}

async function dealStatusTransition(ctx) {
  const refused = { proceed: false, alreadySaved: false, finalStatus: ctx.from }

  const candidates = candidateActions(
    ctx.transitions,
    ctx.pipelineType,
    ctx.from,
    ctx.to,
    ctx.available,
  )

  // The drag guard should have refused this drop, so reaching here means the board and
  // the server disagree — refuse rather than guess.
  if (!candidates.length) return refused

  const action = await chooseAction(candidates, ctx.to)
  if (!action) return refused

  const result = await runAction(ctx.itemName, action, {
    defaults: prefillFor(action, ctx.to),
  })
  if (!result) return refused

  return { proceed: true, alreadySaved: true, finalStatus: result.status }
}

/**
 * Which action the user meant. One candidate needs no question; more than one is asked
 * rather than guessed — dropping a workshop on "Lost" can mean Run Workshop, Cancel
 * Workshop or Not Interested, and picking silently would hide two of them.
 *
 * @returns {Promise<Object|null>} null when dismissed
 */
export function chooseAction(candidates, to) {
  if (candidates.length === 1) return Promise.resolve(candidates[0])

  return new Promise((resolve) => {
    createDialog({
      title: __('Choose an action'),
      message: __('More than one action moves this opportunity to "{0}".', [__(to)]),
      onDismiss: () => resolve(null),
      actions: candidates.map((action) => ({
        label: __(action.label),
        onClick: ({ close }) => {
          resolve(action)
          close()
        },
      })),
    })
  })
}
```

- [ ] **Step 4: Let `runAction` accept defaults**

In `frontend/src/utils/takeAction.js`, change the signature and pass them through:

```js
export async function runAction(deal, action, { today, defaults } = {}) {
  const isoToday = today || new Date().toISOString().split('T')[0]

  const data = await renderFieldLayoutDialog({
    title: __(action.label),
    fields: actionFields(action, isoToday),
    required: requiredFieldnames(action),
    // A kanban drop pre-selects the branch value implied by the column, leaving it
    // editable — the user may change their mind, and the card follows the result.
    defaults: defaults || {},
    submitLabel: __('Confirm'),
    cancelLabel: __('Cancel'),
  })
```

`renderFieldLayoutDialog` already documents a `defaults` option, so no change is needed there.

- [ ] **Step 5: Run the tests**

Run: `cd frontend && yarn vitest run tests/unit/kanbanTransitions.test.js tests/unit/takeAction.test.js`
Expected: PASS.

- [ ] **Step 6: Format and commit**

```bash
cd frontend && npx prettier@3.2.5 --write src/utils/kanbanTransitions.js src/utils/takeAction.js tests/unit/kanbanTransitions.test.js
cd .. && git add frontend/src/utils/kanbanTransitions.js frontend/src/utils/takeAction.js frontend/tests/unit/kanbanTransitions.test.js
git commit -m "feat(deals): run the Take Action flow when a deal is dropped on a new status"
```

---

# Phase 3 — Kanban board

### Task 10: Honour `alreadySaved` and `finalStatus` in ViewControls

**Files:**
- Modify: `frontend/src/components/ViewControls.vue:1041-1130` (`handleKanbanTransition`)

**Interfaces:**
- Consumes: the object returned by `requestKanbanTransition` (Task 9); `transitionsStore` (Task 8).
- Produces: `ctx` now carries `pipelineType`, `transitions`, `available`.

- [ ] **Step 1: Fetch what the deal path needs**

This goes **inside** the existing `try` block, immediately after the `fieldLabel` assignment and before the `requestKanbanTransition` call. It must not sit above the `try`: a failed fetch there would leave the card visually moved with no revert, which is precisely the silent-failure bug `specs/kanban-transition-confirm.md` was written to kill.

```js
    const isDealStatus = props.doctype === 'CRM Deal' && fieldname === 'status'

    // The card row carries pipeline_type (see Task 11); the available actions are
    // per-deal and already filtered by status and role on the server.
    const card = findCard(data.item)
    let available = []
    if (isDealStatus) {
      const response = await call('crm.txb.api.actions.get_available_actions', {
        deal: data.item,
      })
      available = response?.actions || []
    }
```

Add the `findCard` helper next to `handleKanbanTransition`:

```js
// The dragged row, re-resolved by name in the live column data — never a captured
// reference, for the same reason revertCardMove re-resolves (Load More and view
// switches replace the column arrays wholesale).
function findCard(itemName) {
  for (const column of list.value?.data?.data || []) {
    const found = (column.data || []).find((row) => row.name === itemName)
    if (found) return found
  }
  return null
}
```

Import the store alongside the existing imports:

```js
import { transitionsStore } from '@/stores/transitions'
```

and near the other store destructuring:

```js
const { transitions } = transitionsStore()
```

- [ ] **Step 2: Pass the context and honour the result**

Replace the `requestKanbanTransition` call and the `set_value` that follows:

```js
    const outcome = await requestKanbanTransition({
      doctype: props.doctype,
      itemName: data.item,
      fieldname,
      fieldLabel,
      from: data.from,
      to: data.to,
      pipelineType: card?.pipeline_type,
      transitions: transitions.value,
      available,
    })

    if (!outcome.proceed) {
      revert()
      return
    }

    // execute_action already committed the change; writing it again would be a second
    // save and would race the first.
    if (!outcome.alreadySaved) {
      await call('frappe.client.set_value', {
        doctype: props.doctype,
        name: data.item,
        fieldname,
        value: data.to,
      })
    }

    // A branching action may land the deal somewhere other than the dropped column.
    // The server's answer wins.
    if (outcome.finalStatus && outcome.finalStatus !== data.to) {
      revert()
      list.value.reload()
      toast.success(
        __('Moved to {0}', [__(outcome.finalStatus)]),
      )
      return
    }
```

- [ ] **Step 3: Verify the build**

Run: `cd frontend && yarn build`
Expected: succeeds.

- [ ] **Step 4: Run the existing suite**

Run: `cd frontend && yarn test:run`
Expected: all green — `kanbanRevert` and `kanbanTransitions` tests must still pass.

- [ ] **Step 5: Format and commit**

```bash
cd frontend && npx prettier@3.2.5 --write src/components/ViewControls.vue
cd .. && git add frontend/src/components/ViewControls.vue
git commit -m "feat(deals): let the action's result decide where the card lands"
```

---

### Task 11: Grey out and refuse illegal columns

**Files:**
- Modify: `frontend/src/components/Kanban/KanbanView.vue`
- Modify: `frontend/src/pages/Deals.vue:30` (pass the guard)
- Modify: `crm/fcrm/doctype/crm_deal/crm_deal.py` (ensure `pipeline_type` is in kanban rows)

**Interfaces:**
- Consumes: `canDropOn` (Task 7), `transitionsStore` (Task 8).
- Produces: `KanbanView` accepts a `transitionGuard` prop — `({ from, to, card }) => boolean`. Default `null` means allow everything, so Leads and Tasks are untouched.

- [ ] **Step 1: Make sure the card knows its pipeline**

`crm/api/doc.py:393-400` appends `kanban_fields` to `rows`, and `rows` is what `frappe.get_list` fetches while `kanban_fields` is what the card *displays*. `pipeline_type` must be fetched but not displayed, so it belongs in `rows` — **not** in `default_kanban_settings`, which would add a stray line to every card.

In `crm/api/doc.py`, immediately after the existing loop:

```python
		for field in kanban_fields:
			if field not in rows:
				rows.append(field)

		# The board needs each card's pipeline to know which transitions apply to it.
		# Fetched, not displayed -- `kanban_fields` is what the card renders.
		if doctype == "CRM Deal" and "pipeline_type" not in rows:
			rows.append("pipeline_type")
```

Verify from the browser console on a Deals kanban board that each card row carries `pipeline_type`.

- [ ] **Step 2: Add the guard plumbing to KanbanView**

In `frontend/src/components/Kanban/KanbanView.vue`, extend the props:

```js
const props = defineProps({
  // ...existing props...
  /**
   * Optional drop guard: ({ from, to, card }) => boolean.
   * Default null allows everything, so Leads and Tasks are unaffected.
   */
  transitionGuard: { type: Function, default: null },
})
```

Add the drag state and helpers to `<script setup>`:

```js
// Which card is in flight, so columns can decide whether they will accept it.
const dragging = ref(null)

function onDragStart(evt) {
  const from = evt.from?.dataset?.column
  const itemName = evt.item?.dataset?.name
  const card = columns.value
    .find((col) => col.column.name === from)
    ?.data?.find((row) => row.name === itemName)

  dragging.value = { from, card }
}

function allowDrop(to) {
  if (!props.transitionGuard || !dragging.value) return true
  return props.transitionGuard({ ...dragging.value, to })
}

// Sortable reads `group` once per option update, so the object must be stable —
// rebuilding it every render would thrash the option on each drag frame. The `put`
// closure reads the reactive `dragging`, so a stable object still gives live answers.
const groupCache = new Map()

function dragGroup(column) {
  const key = column.column.name
  if (!groupCache.has(key)) {
    groupCache.set(key, { name: 'fields', put: () => allowDrop(key) })
  }
  return groupCache.get(key)
}

function columnRefused(column) {
  return Boolean(dragging.value) && !allowDrop(column.column.name)
}
```

Import `ref` if it is not already imported.

- [ ] **Step 3: Wire it into the template**

On the **card** `Draggable` (the inner one, around line 73), replace `group="fields"` and add `@start`:

```vue
            <Draggable
              :list="column.data"
              :group="dragGroup(column)"
              item-key="name"
              class="flex flex-col gap-3.5 flex-1"
              :delay="isTouchScreenDevice() ? 200 : 0"
              :data-column="column.column.name"
              @start="onDragStart"
              @end="updateColumn"
            >
```

On the column wrapper `div` (around line 13), add the refused styling:

```vue
        <div
          v-if="!column.column.delete"
          class="flex flex-col gap-2.5 min-w-72 w-72 hover:bg-surface-gray-2 rounded-lg p-2.5 transition-opacity"
          :class="{ 'opacity-40 pointer-events-none': columnRefused(column) }"
        >
```

In `updateColumn`, clear the drag state first so the styling never sticks:

```js
function updateColumn(d, fetchNewColumns = false) {
  dragging.value = null

  let toColumn = d?.to?.dataset.column
```

- [ ] **Step 4: Supply the guard from Deals.vue**

In `frontend/src/pages/Deals.vue`, add to the imports:

```js
import { canDropOn } from '@/utils/dealTransitions'
import { transitionsStore } from '@/stores/transitions'
```

and in `<script setup>`:

```js
const { transitions, canChangeStatusFor } = transitionsStore()

// Only a status board has transition rules; a board grouped by owner or any other
// field keeps plain drag-and-drop.
function dealTransitionGuard({ from, to, card }) {
  const pipeline = card?.pipeline_type
  if (!pipeline) return true

  return canDropOn(
    transitions.value,
    pipeline,
    from,
    to,
    canChangeStatusFor(pipeline),
  )
}
```

`Deals.vue` already reads the board's column field as `deals.value.params.column_field` (see `onNewClick`, ~line 489). Expose it as a computed:

```js
// Only a board grouped by status has transition rules.
const kanbanColumnField = computed(() => deals.value?.params?.column_field)
```

Bind it on the `<KanbanView>` tag, which already carries `v-if="route.params.viewType == 'kanban'"`:

```vue
    :transition-guard="
      kanbanColumnField === 'status' ? dealTransitionGuard : null
    "
```

- [ ] **Step 5: Verify manually**

Run: `cd frontend && yarn dev` (with `bench start` running).

Check, on a Workshop kanban board as a non-Admin:
- Start dragging a card in `Workshop set` → `Workshop ran`, `Workshop rescheduling in progress` and `Lost` stay bright; every other column dims and refuses the drop.
- Drop on a dimmed column → the card returns, nothing saves.
- On a **Delivering Coaching** board as a coach → every column dims; the card cannot move.
- On a **Leads** kanban board → no dimming at all, and the old confirm dialog still appears.

- [ ] **Step 6: Format and commit**

```bash
cd frontend && npx prettier@3.2.5 --write src/components/Kanban/KanbanView.vue src/pages/Deals.vue
cd .. && git add frontend/src/components/Kanban/KanbanView.vue frontend/src/pages/Deals.vue crm/fcrm/doctype/crm_deal/crm_deal.py
git commit -m "feat(deals): grey out and refuse kanban columns the state machine forbids"
```

---

# Phase 4 — Deal detail surfaces

### Task 12: Route the detail status dropdown through the flow

**Files:**
- Modify: `frontend/src/pages/Deal.vue` (`triggerStatusChange` ~line 844, `statuses` computed ~line 637)
- Modify: `frontend/src/pages/MobileDeal.vue` (same two spots, ~line 708 and ~line 380)

**Interfaces:**
- Consumes: `allowedTargets`, `candidateActions`, `prefillFor` (Task 7); `transitionsStore` (Task 8); `chooseAction` (Task 9); `runAction`.
- Produces: no new exports; behaviour change only.

- [ ] **Step 1: Add the shared handler to Deal.vue**

Add imports:

```js
import { allowedTargets, candidateActions, prefillFor } from '@/utils/dealTransitions'
import { chooseAction } from '@/utils/kanbanTransitions'
import { transitionsStore } from '@/stores/transitions'
```

and near the other stores:

```js
const { transitions } = transitionsStore()
const isAdmin = computed(() => dealActions.data?.is_admin === true)
```

Add `is_admin: false` to the `dealActions` resource's `initialData` so the default before the resource resolves is the *restrictive* one — an Admin briefly gets the action flow rather than a non-Admin briefly getting the free write.

Replace `triggerStatusChange`:

```js
// The action that owns a transition runs it — for everyone, Admins included. Whoever
// changes the status, the note, the task and the deal fields that belong with the change
// are recorded. A bare write would reach "Session Set" with no BAP details at all, and
// would reach "Lost" with no reason, where CRMDeal.validate_lost_reason simply throws.
//
// The Admin hatch is for moves the state machine does NOT describe: only when no action
// covers this edge does an Admin write directly. A non-Admin is refused there.
async function triggerStatusChange(value) {
  const candidates = candidateActions(
    transitions.value,
    doc.value?.pipeline_type,
    doc.value?.status,
    value,
    availableActions.value,
  )

  if (candidates.length) {
    const action = await chooseAction(candidates, value)
    if (!action) return
    await onTakeAction(action, prefillFor(action, value))
    return
  }

  if (!isAdmin.value) {
    toast.error(
      __('"{0}" cannot be reached from "{1}".', [__(value), __(doc.value?.status)]),
    )
    return
  }

  await triggerOnChange('status', value)
  setLostReason()
}
```

Extend `onTakeAction` to accept the defaults:

```js
async function onTakeAction(action, defaults) {
  try {
    const result = await runAction(props.dealId, action, { defaults })
    if (!result) return
```

- [ ] **Step 2: Restrict the offered statuses for non-Admins**

In the `statuses` computed, after the existing pipeline fallback:

```js
  // Non-Admins are offered only what the state machine can actually reach, so a user
  // never picks a status and then hears it was refused.
  if (!isAdmin.value) {
    const reachable = allowedTargets(
      transitions.value,
      doc.value?.pipeline_type,
      doc.value?.status,
    )
    if (reachable.length) {
      customStatuses = [doc.value.status, ...reachable]
    }
  }

  return statusOptions('deal', customStatuses, triggerStatusChange)
```

- [ ] **Step 3: Return `is_admin` from the server**

In `crm/txb/api/actions.py`, `get_available_actions`, extend the return:

```python
	return {
		"actions": available,
		"can_change_status": may_change_status,
		# The recovery hatch is a role, so the browser must be told about it rather than
		# inferring it from can_change_status -- which is a different, per-pipeline rule.
		"is_admin": is_admin(),
	}
```

and import `is_admin` alongside `can_change_status`.

Add the covering test to `crm/txb/test_transitions.py`:

```python
	def test_available_actions_report_admin(self):
		from crm.txb.api.actions import get_available_actions

		deal = self.make_deal("Submitted")
		frappe.set_user(COACH)
		self.assertFalse(get_available_actions(deal.name)["is_admin"])
```

(place it inside `TestTransitionEnforcement`, which already has `make_deal` and the users)

- [ ] **Step 4: Mirror both changes in MobileDeal.vue**

`MobileDeal.vue` has the same `triggerStatusChange`, the same `statuses` computed and the same `dealActions` resource. Apply the identical edits. Do not extract a composable in this task — the two files already duplicate this block, and unifying them is a separate refactor with its own review.

- [ ] **Step 5: Run tests and verify manually**

Run: `cd frontend && yarn test:run` — all green.
Run: `bench --site localhost run-tests --module crm.txb.test_transitions` — all green.

In the browser, as a non-Admin on an Individual Session deal in `Submitted`:
- The status dropdown offers only `Session Set` and `Lost` (plus the current status).
- Picking `Lost` shows the picker (Cancel a BAP / Not Interested).
- Picking `Session Set` opens *Book a BAP* directly.
- Cancelling leaves the status unchanged.
As an Admin: the dropdown offers every status in the pipeline and writes directly.

- [ ] **Step 6: Format and commit**

```bash
cd frontend && npx prettier@3.2.5 --write src/pages/Deal.vue src/pages/MobileDeal.vue
cd .. && git add frontend/src/pages/Deal.vue frontend/src/pages/MobileDeal.vue crm/txb/api/actions.py crm/txb/test_transitions.py
git commit -m "feat(deals): the detail status dropdown runs the owning action"
```

---

### Task 13: Route the side panel status field through the flow

**Files:**
- Modify: `frontend/src/components/SidePanelLayout.vue` (`fieldChange` ~line 656, `pipelineStatusFilters` ~line 591)

**Interfaces:**
- Consumes: `allowedTargets`, `candidateActions`, `prefillFor` (Task 7); `chooseAction` (Task 9); `runAction`; `transitionsStore` (Task 8).

- [ ] **Step 1: Restrict the link filter for non-Admins**

The side panel already narrows a `CRM Deal Status` link to the pipeline. Narrow it further to reachable statuses when the user is not an Admin. Replace the `pipelineStatusFilters` block:

```js
  // Restrict a deal's status to its own pipeline, and — for anyone without the recovery
  // hatch — to the statuses the state machine can actually reach from here.
  const isDealStatusField =
    field.fieldtype === 'Link' &&
    field.options === 'CRM Deal Status' &&
    props.doctype === 'CRM Deal'

  let pipelineStatusFilters = null
  if (isDealStatusField) {
    if (isDealAdmin.value) {
      pipelineStatusFilters = statusLinkFilters(
        doc.value?.pipeline_type,
        doc.value?.status,
        pipelineStatuses.data,
      )
    } else {
      const reachable = allowedTargets(
        transitions.value,
        doc.value?.pipeline_type,
        doc.value?.status,
      )
      pipelineStatusFilters = reachable.length
        ? { name: ['in', [doc.value.status, ...reachable]] }
        : statusLinkFilters(
            doc.value?.pipeline_type,
            doc.value?.status,
            pipelineStatuses.data,
          )
    }
  }
```

Add near `canChangeDealStatus`:

```js
const isDealAdmin = computed(() => dealActions.data?.is_admin === true)
```

and the imports:

```js
import { allowedTargets, candidateActions, prefillFor } from '@/utils/dealTransitions'
import { chooseAction } from '@/utils/kanbanTransitions'
import { runAction } from '@/utils/takeAction'
import { transitionsStore } from '@/stores/transitions'
```

plus `const { transitions } = transitionsStore()` next to the existing store call.

- [ ] **Step 2: Intercept the status write**

In `fieldChange(value, df)`, before the existing `await triggerOnChange(...)`:

```js
async function fieldChange(value, df) {
  // Changing a deal's status runs the action that owns the transition, exactly as a
  // kanban drop does — otherwise the same move records completely different data
  // depending on which control was used. This applies to Admins too: the hatch is for
  // edges the state machine does not describe, not for skipping the form.
  if (
    props.doctype === 'CRM Deal' &&
    df.fieldname === 'status' &&
    value !== doc.value?.status
  ) {
    const candidates = candidateActions(
      transitions.value,
      doc.value?.pipeline_type,
      doc.value?.status,
      value,
      dealActions.data?.actions || [],
    )

    if (candidates.length) {
      const action = await chooseAction(candidates, value)
      if (!action) return

      const result = await runAction(props.docname, action, {
        defaults: prefillFor(action, value),
      })
      if (!result) return

      dealActions.reload()
      emit('reload')
      return
    }

    // No action covers this edge. Only an Admin may write it bare.
    if (!isDealAdmin.value) return
  }

  // ...existing body...
}
```

- [ ] **Step 3: Verify manually**

With the dev server running, on an Individual Session deal in `Submitted` as a non-Admin:
- The side panel Status field offers only `Submitted`, `Session Set` and `Lost`.
- Selecting `Session Set` opens *Book a BAP*; cancelling leaves the field on `Submitted`.
- As an Admin, the field offers the whole pipeline and writes directly.

- [ ] **Step 4: Run the suites**

Run: `cd frontend && yarn test:run`
Expected: green.

- [ ] **Step 5: Format and commit**

```bash
cd frontend && npx prettier@3.2.5 --write src/components/SidePanelLayout.vue
cd .. && git add frontend/src/components/SidePanelLayout.vue
git commit -m "feat(deals): the side panel status field runs the owning action"
```

---

# Phase 5 — Close out

### Task 14: Full verification and spec update

**Files:**
- Modify: `specs/kanban-take-action-transitions.md` (status line)

- [ ] **Step 1: Run everything**

```bash
cd frontend && yarn test:run && yarn build
cd .. && bench --site localhost run-tests --module crm.txb.test_transitions
bench --site localhost run-tests --module crm.txb.test_permissions
bench --site localhost run-tests --module crm.txb.test_doc_events
bench --site localhost run-tests --module crm.txb.test_registration_token
```

Expected: all green.

- [ ] **Step 2: Check formatting and diff hygiene**

There is **no `yarn lint` script** in `frontend/package.json`. Linting in this repo is pre-commit-managed (`.pre-commit-config.yaml` runs oxlint, prettier@3.2.5 and eslint), and `pre-commit` is not installed on this machine, so the practical local check is prettier alone:

```bash
cd frontend && npx prettier@3.2.5 --check "src/**/*.{js,vue}"
cd .. && git diff --stat $(git merge-base develop HEAD)..HEAD
```

Expected: prettier reports no changes needed for the files this branch touched, and the diff touches only files this plan names.

Two traps:
- `--check` will report pre-existing formatting drift in files this branch never touched. Only fix files this branch actually changed; leave the rest alone.
- If `e2e/` or unrelated files appear in the diff, a wrong prettier version was used somewhere — revert those hunks and re-run with `prettier@3.2.5`. The pre-commit config pins that version; a newer one reformats unrelated code.

- [ ] **Step 3: Walk the manual matrix**

For each of the four pipelines, confirm:
- happy path with modal; branch drop where the outcome is changed mid-modal (card lands on the real status, toast names it);
- ambiguous drop shows the picker; a refused column is dimmed and rejects the drop;
- cancel at the picker and at the modal both revert the card;
- a server error reverts the card and toasts;
- coach vs Admin on Delivering Coaching;
- reopen from each terminal status;
- Leads and Tasks kanban boards behave exactly as before.

- [ ] **Step 4: Regenerate the matrix and update the spec**

```bash
bench --site localhost execute crm.txb.transition_matrix.write --kwargs "{'path': 'apps/crm/docs/transition-matrix.md'}"
```

Change the spec's header to `Status: implemented (2026-08-04)`.

- [ ] **Step 5: Commit and push**

```bash
git add specs/kanban-take-action-transitions.md docs/transition-matrix.md
git commit -m "docs: mark TXB-110 implemented and regenerate the transition matrix"
git push -u origin feature/TXB-110-kanban-transitions
```

- [ ] **Step 6: Open the PR**

```bash
gh pr create --base develop \
  --title "TXB-110: kanban drag-and-drop through Take Action, with enforced transitions" \
  --body "$(cat <<'EOF'
Dragging a deal card now opens the Take Action modal that owns the transition, and the
status is committed only when that modal saves. Illegal columns dim and refuse the drop.

## How the rules are defined

The transition graph is **derived** from `PIPELINE_ACTIONS`, not authored. `from_states`
and `to_state`/`to_state_map` already described a state machine; `crm/txb/pipelines/
transitions.py` reads it as one. No fifth copy of pipeline data — the server-script
migration spent four PRs deleting the first four.

## Enforcement

`guard_transition` runs in `validate()`, so kanban, the detail dropdown, the side panel,
Take Action and raw REST are all covered:

- the edge must exist in the graph;
- the write must originate from `execute_action`, because a *legal* edge written bare
  skips the handler — a deal reaching "Session Set" with no BAP type, no date and no note.

**Admins are exempt from both**, keeping a free in-pipeline Status dropdown. That is the
documented recovery hatch: without it a mis-clicked "Not Interested" needs a database edit.

## Dead ends removed

Enforcing the graph as it stood would have trapped deals in four states. Fixed here:
Individual Session `Follow-up` (Book a BAP now starts there) and `Lost`, Workshop `Lost`,
Selling Training `Training not interested` — the last three via a new `Reopen` action.
A test now fails if any status loses its way out.

## Also fixed

Five actions set `Lost` inside their handler with `to_state: None`. A target the registry
cannot see cannot be enforced — the same blind spot as the `changes_status` bug in #15.
They now declare it.

## ⚠️ Behaviour change

**This starts rejecting off-graph status changes that work today.** Every such move a
non-Admin currently makes stops working on deploy. Intended, but there is no gradual path.

Bulk edits and list-view inline edits still bypass the modal flow; they cannot make an
illegal move, but a non-Admin is simply refused. Follow-up ticket.

QA: `docs/transition-matrix.md` is generated from the registry.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01DeDbbexZuPyHuzZny3F7Vq
EOF
)"
```

---

## Deferred / not in this plan

- Extracting the duplicated status block shared by `Deal.vue` and `MobileDeal.vue` into a composable. Real, but a separate refactor with its own review.
- Bulk status changes and list-view inline edits still bypass the modal flow. They hit `guard_transition`, so they cannot make an *illegal* move, but a non-Admin will simply be refused. Worth a follow-up ticket.
- Configurable transition rules in the database. Deliberately refused — see the spec's *Out of scope*.

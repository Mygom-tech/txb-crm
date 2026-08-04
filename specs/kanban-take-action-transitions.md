# Kanban drag-and-drop through Take Action, with enforced transition rules (TXB-110)

Status: implemented (2026-08-04) — browser verification outstanding, see PR
Date: 2026-08-04
Branch: `feature/TXB-110-kanban-transitions`
Jira: [TXB-110](https://mygomtech.atlassian.net/browse/TXB-110)

## Context

Dragging a kanban card to another column currently writes the status directly. TXB-110
asks for the drag to become a Take Action trigger instead: illegal columns greyed out
during the drag, the matching modal on drop, the status committed only once that modal
saves, and the card rolled back on cancel or error — governed by one set of transition
rules shared with the deal detail page and enforced server-side so the REST API cannot be
used to sidestep them.

Two pieces of groundwork already exist and this feature is their intended consumer:

- **The transition table.** `PIPELINE_ACTIONS` (ported in TXB-105 / PR #15–#16) already
  declares `from_states` and `to_state` / `to_state_map` per action. That *is* a
  `current status → allowed targets` graph. The ticket's "create a centralized transition
  registry" is satisfied by deriving from this registry, not by authoring a second copy.
  Four duplicate copies of pipeline data were deleted during the server-script migration;
  a fifth is not being created.
- **The frontend seam.** `frontend/src/utils/kanbanTransitions.js` exposes
  `requestKanbanTransition(ctx)`, documented in `specs/kanban-transition-confirm.md` as
  the place where "future rules go INSIDE this function and must run BEFORE the confirm".

## Decisions (user-confirmed)

1. **Disambiguation by asking, never guessing.** When a drop target is reachable by more
   than one action, a picker lists the candidates and the user chooses; that action's modal
   then opens. A single-candidate transition skips the picker.
2. **The graph is enforced for everyone on every path** — kanban, detail page, Take Action
   and REST. **Admins retain a free Status dropdown within the pipeline** as a documented
   recovery hatch, so an off-graph correction never requires a database edit.
   *Revised 2026-08-04:* the hatch is narrower than first written. It covers only edges
   the state machine does **not** describe. Where an action owns the edge, an Admin runs
   that action like everyone else — see decision 4.
3. **A branch drop pre-fills but does not lock.** The dropped column pre-selects the
   matching branch value, so confirming unchanged does what the drop implied. The field
   stays editable; if the user changes it, the card lands where the action actually put it
   and a toast names the real status.
4. **The detail Status dropdown becomes a third trigger for the same flow.** For
   non-Admins it lists only graph-allowed targets, and picking one opens the same
   picker/modal a drag would, reverting the field on cancel. A bare status write can never
   skip an action's side effects.

   *Revised 2026-08-04, after testing:* **this applies to every role, Admins included,
   whenever an action owns the target edge.** The original wording let Admins skip
   straight to a bare write, which broke the hatch for the status that needs it most:
   moving a deal to `Lost` bare hits `CRMDeal.validate_lost_reason` and throws, because
   the reason is captured *by the action's form*. The rule is now:

   | Target edge | Admin | Non-Admin |
   | --- | --- | --- |
   | Has candidate action(s) | picker (if >1) → modal → `execute_action` | same |
   | No action describes it | bare write — the hatch | refused |

   **This table describes the UI, not the backend.** `guard_transition` exempts Admins
   from both the edge check and the origin check outright, so a direct API call by an
   Admin can still bare-write any status. That is deliberate and stays: the hatch exists
   for recovery, and an Admin repairing data in bulk — or a script doing it — must not be
   forced through a form dialog. Routing Admins through the modal is a UI decision made
   because it captures the data the action records; it is not a security boundary, and
   the backend is intentionally the looser of the two. Anything an Admin can do by API
   they could already do in the database.
5. **The dead ends are fixed as part of this ticket**, not documented and deferred (see
   *Recovery transitions*).

Decided without a question, as routine:

- The existing "Change {field} from X to Y?" confirm is **replaced** by the action modal on
  `CRM Deal` boards grouped by `status`. Two dialogs in sequence is not acceptable. Every
  other board — Leads, Tasks, boards grouped by owner or any other field — keeps today's
  confirm untouched.
- Transition rules engage only when `doctype === "CRM Deal"` **and** the column field is
  `status`. Nothing else changes behaviour.

## Prerequisite: declare the targets that handlers hide

Five actions reach `Lost` inside their handler through `mark_lost`, leaving `to_state`
as `None` with a comment. A target that is invisible to the registry cannot be derived and
cannot be enforced — the same failure mode as the `changes_status` bug fixed in PR #15,
where a check keyed on `to_state` classified branching actions as harmless.

| Action | Change |
| --- | --- |
| `cancel_workshop`, `workshop_not_interested` | `"to_state": "Lost"` |
| `cancel_bap`, `not_interested` | `"to_state": "Lost"` |
| `run_workshop` | add `"Lost": "Lost"` to the `ws_outcome` map (the option string is literally `"Lost"`) |

`mark_lost` continues to set `lost_reason`. `execute_action` then assigns the same status
value it already holds, so this is idempotent with no behaviour change — and the existing
`test_every_target_state_is_selectable_in_its_pipeline` begins covering these actions.

## Recovery transitions

Derived from the registry as it stands today, four states are dead ends. Enforcing the
graph without fixing them would trap deals and make Admins the bottleneck for a routine
mis-click.

| Pipeline | Dead end | Fix |
| --- | --- | --- |
| Individual Session | `Follow-up` → only `Lost` | **Extend `book_bap`**: add `"Follow-up"` to its `from_states`. No new action — the BAP form already records exactly what re-booking needs. |
| Individual Session | `Lost` | New `reopen` action → `Submitted` |
| Workshop | `Lost` | New `reopen` action → `Workshop submitted` |
| Selling Training | `Training not interested` | New `reopen` action → `Training submitted` |

Delivering Coaching has no dead end: `Inactive → Active` already exists via `Reactivate`.

The `reopen` action, one per affected pipeline, targeting that pipeline's entry status:

```
name           reopen
label          Reopen
from_states    [the pipeline's terminal status]
to_state       [the pipeline's entry status]
changes_status True
admin_only     False
fields         reopen_reason (Small Text, reqd)
handler        clears lost_reason (and lost_reason_detail), writes a note
```

**Assumption flagged for review:** `reopen` is available to every role, matching
`Cancel` and `Mark as "Not Interested"`, which are likewise unrestricted. Reopening
distorts pipeline reporting, so if it should be Admin-only, that is a one-line
`admin_only` change.

## Architecture

```
drag start ─ KanbanView.vue
   └─ transitionGuard(card) ──► dealTransitions.allowedTargets(map, pipeline, from)
        └─ per-column :group="{ put: guard }"  +  dimmed class on refused columns

drop ────── ViewControls.handleKanbanTransition
   └─ requestKanbanTransition(ctx)
        ├─ not (CRM Deal × status) ──► existing confirm dialog          [unchanged]
        └─ deal status transition
             ├─ candidateActions() ─ >1 ─► picker dialog
             ├─ prefillFor(action, to) ──► renderFieldLayoutDialog
             ├─ cancel ───────────────► { proceed: false }  → revertCardMove
             └─ submit ──► execute_action ──► { proceed: true, alreadySaved: true,
                                                finalStatus }
                                  └─ card placed in finalStatus column (may differ
                                     from the drop column; toast names it)
```

`finalStatus` and `alreadySaved` are why the return type changes from `boolean` to an
object: the server has already committed, so `ViewControls` must not call
`frappe.client.set_value` afterwards, and the card must land where the action chose rather
than where the pointer was released.

## Components

### `crm/txb/pipelines/transitions.py` (new)

Derives the graph. Nothing hand-authored.

- `get_transition_map()` → `{pipeline: {from_status: {to_status: [action_name, …]}}}`.
  `from_states: []` expands to every status in `PIPELINE_STATUSES` for that pipeline.
  Targets come from `to_state` plus every value in `to_state_map`.
- `candidates(pipeline, from_status, to_status)` → the action specs for that edge.
- `is_allowed(pipeline, from_status, to_status)` → bool.

### `crm/txb/api/transitions.py` (new)

- `get_transition_map()` — whitelisted. Returns the graph plus per-pipeline
  `can_change_status` for the calling user. A few KB; fetched once per board, mirroring
  `crm.txb.api.pipelines.get_pipeline_statuses`.

### `crm/txb/permissions.py` (extended)

`guard_transition(doc)` runs on the existing `validate()` hook, so kanban, dropdown, Take
Action and raw REST are all covered by one check.

| Caller | Rule |
| --- | --- |
| Insert | exempt — unchanged; coach handover legitimately creates deals |
| `Administrator` / Admin role | any status within the deal's pipeline (decision 2) |
| Everyone else | the edge must exist in the graph **and** the write must originate from `execute_action` |

The origin check is a request-scoped flag set by `execute_action` and cleared in its
`finally`. It is what makes a *legal but bare* write fail: `Submitted → Session Set` is a
valid edge, but writing it directly reaches `Session Set` with no BAP type, no date, no
location and no note. Patches and scheduled jobs run as `Administrator` and are exempt.

### `frontend/src/utils/dealTransitions.js` (new, pure — primary unit-test target)

- `allowedTargets(map, pipeline, from)` → `string[]`
- `candidateActions(map, pipeline, from, to)` → `[{ name, label }]`
- `prefillFor(action, to)` → `{ fieldname: value }`, by inverting `to_state_map`.
  **If more than one value maps to the target, nothing is pre-filled** — `run_bap` reaches
  `Session Run` from both `"Follow-up needed"` and `"Not interested"`, and guessing between
  them would contradict decision 1.

### `frontend/src/utils/kanbanTransitions.js` (extended)

One new branch for `CRM Deal` × `status`; all other boards keep the existing confirm.
Returns `{ proceed, finalStatus, alreadySaved }`.

### `frontend/src/components/Kanban/KanbanView.vue`

- New optional `transitionGuard` prop. Default allows everything, so Leads and Tasks are
  untouched.
- `@start` on the card `Draggable` records the source column and the dragged card.
- Each column binds `:group="{ name: 'fields', put: guard }"`. `sortablejs ^1.15` /
  `vuedraggable ^4.1` support a function-valued `put`, so refusal is native — no
  `MutationObserver`, no injected CSS, unlike the form scripts this app has been shedding.
- Refused columns get a dimmed, non-interactive class while a drag is in progress.
- **Delivering Coaching for a non-Admin is the degenerate case**: `can_change_status` is
  false, so *every* column is refused and the card cannot be dragged anywhere. This is the
  behaviour the ticket asks for, and it reuses the TXB-105 rule rather than restating it.
  Coaches keep opening the card and running `Log Coaching Call`, which does not move the
  status.

### Kanban row payload

`crm/api/doc.py` builds kanban rows from the list-view rows plus `kanban_fields`, so
`pipeline_type` is not guaranteed to be present. It is required client-side to compute
allowed targets at drag start. `pipeline_type` is added to the requested rows for
`CRM Deal` kanban views.

### `Deal.vue`, `MobileDeal.vue`, `SidePanelLayout.vue`

The status control routes through the same module for non-Admins (decision 4): options
limited to `allowedTargets`, selection opens the picker/modal, the field reverts on cancel.
No third copy of the logic.

## Error handling

- Cancel at the picker, cancel in the modal, validation failure, or a server error →
  `revertCardMove` puts the card back and the status is unchanged. This reuses the
  identity-based revert built in `specs/kanban-transition-confirm.md`, which re-resolves
  columns by name rather than trusting stale references.
- `execute_action` succeeds but the deal lands in a different status than the drop column →
  the card is moved to `finalStatus` and a toast names it. Not an error.
- `execute_action` succeeds and a later step fails (badge sync, ordering persist) → warning
  only, never a revert. The write is already committed; reverting would be a false
  negative. This rule is inherited from the existing post-save `try`/`catch`.
- A non-Admin attempting an off-graph change gets the server's message naming the current
  status and what is reachable from it.

## Testing

**Python**

- Derivation: every declared `to_state` / `to_state_map` value appears in the graph; empty
  `from_states` expands to the full status list.
- `guard_transition`: insert exempt; Admin off-graph allowed; non-Admin off-graph refused;
  non-Admin on-graph refused without the action flag; non-Admin on-graph allowed through
  `execute_action`; Delivering Coaching still Admin-only per TXB-105.
- **No dead ends**: every status in every pipeline has at least one outgoing transition.
  This test is what keeps the recovery transitions from silently regressing.
- **Generated transition matrix per pipeline**, written as a test artifact — this is the
  QA deliverable the ticket's Definition of Done asks for, produced from the registry
  instead of maintained by hand.

**Vitest**

- `allowedTargets`, `candidateActions`, `prefillFor` including the ambiguous-prefill case.
- `requestKanbanTransition` non-deal path unchanged (regression guard for Leads/Tasks).

**Manual** (dev site, per pipeline)

Happy path with modal; branch drop where the outcome is changed mid-modal; ambiguous drop
showing the picker; refused column greyed and non-droppable; cancel at picker and at modal;
server error; coach vs Admin on Delivering Coaching; detail dropdown triggering the flow;
Admin free dropdown; reopen from each terminal status.

## Out of scope

- Bulk status changes and list-view inline edits.
- Configurable transition rules stored in the database. Deliberately refused: the epic
  behind PRs #11–#16 removed exactly that, and the rules belong in reviewable, diffable,
  tested Python.
- Transition rules on Leads and Tasks.
- Telemetry on refused transitions.

## Red flags

1. **This starts rejecting status changes that work today.** Every off-graph move a
   non-Admin currently performs stops working on deploy. That is the ticket's intent, but
   there is no gradual path and support will hear about it in the first week.
2. **Boards mixing pipelines grey erratically.** Allowed targets are computed from the
   dragged card, so the greying changes depending on which card was grabbed. Correct, but
   it will be reported as a bug at least once.
3. **The `to_state` declarations and `book_bap` `from_states` touch merged code.**
   Idempotent and test-covered, but it is the Take Action registry shipped in #15/#16 and
   needs the same manual pass.
4. **`reopen` availability is an assumption** (see above) — confirm before QA.
5. **Reporting impact.** Reopened deals re-enter the funnel from its entry status, which
   will affect conversion metrics. Worth telling whoever owns the dashboards.

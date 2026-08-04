# Take Action port + Delivering Coaching status permission (TXB-105)

Status: implemented — all four pipelines ported (2026-08-04)
Date: 2026-08-04
Branch: `feature/TXB-105-take-action-port`
Jira: [TXB-105](https://mygomtech.atlassian.net/browse/TXB-105)

## Context

Only the Admin role may change the status of a Delivering Coaching opportunity. Coaches
keep everything else: logging coaching calls, editing other fields, notes and tasks.

### The rule already existed as data

`CRM Wizard Framework` — 1355 lines of DOM-injecting JavaScript stored as a Form Script —
carried a registry that is a transition table in all but name:

```js
{ label, config, stages: [...from-states], adminOnly }
```

Every Delivering Coaching action was already marked `adminOnly` **except** `Log Coaching
Call`, which is exactly what the ticket asks for. So the requirement was not
unimplemented. It was **unenforced**, for two reasons:

- `adminOnly` was checked as `frappe.session.user !== 'Administrator'` — the literal
  superuser *account*. Kristina is `kristina@txbconsulting.com`, so it hid those actions
  from the very person it was meant to empower, and granted nothing to anyone.
- Nothing checked server-side at all, so the status field, Kanban and a plain REST call
  were all open regardless of what the menu showed.

### "Admin" is a real role in this product

`Settings/Users.vue` offers Admin / Manager / Sales User, mapping to:

| CRM label | Frappe role |
|---|---|
| Admin | `System Manager` |
| Manager | `Sales Manager` |
| Sales User | `Sales User` |

Kristina holds `System Manager`; every coach holds only `Sales User`. No new role was
needed.

## Design

- **The transition table is server-owned** (`crm/txb/pipelines/actions.py`), following the
  same decision as `PIPELINE_STATUSES`: declarative, version-controlled, testable.
- **Enforcement lives in `validate()`** (`crm/txb/permissions.py`), because every
  status-writing path reaches it. One guard closes the status field, Kanban drag, Take
  Action and the API together.
- **One endpoint answers both questions.** `get_available_actions` returns the permitted
  actions *and* `can_change_status`, so what the UI shows and what the server enforces
  come from the same rule and cannot drift. That drift is precisely how `adminOnly` ended
  up protecting nothing.
- **`execute_action` re-checks** the from-state and the role rather than trusting the
  caller, and applies status, fields, notes and tasks in a **single save**. The old wizard
  fired several sequential PUTs and could half-apply.
- **Rendering reuses `formDialog`**, so action fields are ordinary Frappe field
  definitions and inherit validation and mandatory handling.

### Deliberate exemptions

- **Inserts are never guarded.** Coach flows create Delivering Coaching deals when an
  Individual Session or Workshop is won. Blocking that would break the daily work the
  ticket explicitly protects.
- **`custom_delivery_status` is guarded with `status`.** It duplicates `status` for this
  pipeline and is blank on 73 of 74 deals, so leaving it open was a side door. Removing
  the field is a separate ticket.
- **`Add an Attendee (TBD)`** had `config: null` and rendered as a dead menu entry. It was
  dropped rather than ported as a no-op.

## Verification

Run against real data with `ignore_permissions=True`, so only the new guard could block:

```
BLOCKED  | coach changes status -> Inactive
ALLOWED  | admin changes status -> Inactive
BLOCKED  | coach changes custom_delivery_status
ALLOWED  | coach edits non-status fields
ALLOWED  | coach changes a Workshop deal status      (other pipelines untouched)
ALLOWED  | coach creates a Delivering Coaching deal  (coach flows intact)
```

Action visibility:

```
Submitted / Waiting on Review / Contract Cleared / On Hold / Inactive → coach sees []
Active                                                               → coach sees ['Log Coaching Call']
```

`execute_action` end to end: coach logged a call (count 3→4, status unchanged, note
written), missing required fields rejected, `mark_inactive` refused for the coach,
`put_on_hold` as an admin moved the status and created the review task, and
`clear_contract` was refused from the wrong from-state. All rolled back.

Tests: `crm/txb/test_permissions.py` (rule + visibility) and
`frontend/tests/unit/takeAction.test.js` (pure helpers). 163 frontend tests pass.

Note the Python suite cannot run on a production-matching bench — `IntegrationTestCase` is
a Frappe v16 API and production runs v15. See `specs/server-script-migration.md`.

## All four pipelines are ported

| Pipeline | Actions |
|---|---|
| Delivering Coaching | 8 |
| Workshop | 8 |
| Individual Session | 6 |
| Selling Training | 9 |
| **Total** | **31** |

The wizard had 32; `Add an Attendee (TBD)` carried `config: null` and rendered as a dead
menu entry, so it was dropped rather than ported as a no-op.

`crm.patches.v1_0.disable_wizard_form_script` retires the script. It must ship in the same
deploy as the code — until the row is disabled both implementations run and two Take Action
menus appear.

### Shapes that appeared beyond Delivering Coaching

- **Branching transitions** (10 of 31): one action ending in several states depending on an
  answer, declared as `to_state_map` so every destination stays visible in the table.
  Selling Training is the most branch-heavy, with five.
- **Conditional fields**: the wizard's `showIf` becomes Frappe's native `depends_on`, which
  FieldLayout already evaluates, so the multi-page wizards collapse into single dialogs.
- **`dealField` → `deal_field`** on the field itself, so the answer-to-deal mapping is
  declared once instead of re-looped in every handler.
- **Cross-pipeline handover**: a won Workshop or Individual Session creates a Delivering
  Coaching deal, carrying organization and contacts. Allowed because the status guard
  exempts inserts.
- `run_discovery_meeting` read the acting user from a browser cookie to assign a task; on
  the server `frappe.session.user` is authoritative.

### Verified

All referenced statuses exist in `CRM Deal Status` — checked across every `from_states`,
`to_state` and `to_state_map` entry. That matters: the old form scripts referenced
`Training RFQ received`, which does not exist.

Executed live, then rolled back: Individual Session won → coaching deal created, follow-up
→ none; all three `negotiation_result` branches; `run_discovery_meeting` dual task
assignment; both `contract_signed` branches.

## Follow-up fixes

**Stale UI after an action.** `onTakeAction` called `reloadResources()` with no argument,
but that helper is parameterised by what changed, so neither branch ran and nothing
reloaded — notes never appeared and the document was never refetched, which also meant the
`watch` on `doc.status` could not fire and the menu stayed stale. Fixed by setting the
`reload` ref, which `Activities` already watches to reload both the feed and the document.

**Take Action was missing on mobile.** `MobileDeal.vue` still rendered the wizard's
injected menu through `document._actions`; disabling that script would have removed Take
Action from mobile entirely. It now has the same menu, refresh and status lock as desktop.

**`Convert Dialog - Pipeline Type` retired.** Pipeline Type and Status are native fields
in the convert dialog now. The script had replaced `window.fetch` for the whole session to
splice them into the request, built them as raw HTML via a `MutationObserver`, hidden the
real status field with injected CSS, and carried a fourth copy of the status map. The
redirect looks the pipeline's kanban board up by label instead of hardcoded view ids.

**`Training submitted` restored to Selling Training.** It is that pipeline's entry status
and exists in `CRM Deal Status`, but both Form Script copies of the map omitted it — so it
could not be selected and `Set Discovery Meeting`, which starts from it, was unreachable.
Guarded by tests asserting every `from_state` is selectable and no action strands a deal in
a status the UI cannot show.

## Remaining work

- **Kanban**: a non-Admin dragging a Delivering Coaching card gets a server error and the
  card reverts. Correct and enforced, but the drag could be prevented up front using the
  same `can_change_status` answer.
- Other form scripts remain in the database — `Pipeline Section Visibility` (overrides
  `history.pushState`, synchronous XHR), `Notes Tab Rename` (93 lines and a body-wide
  `MutationObserver` to relabel one tab), `Auto Refresh Call Count`, `Lead Creation
  Redirect`, `Workshop Datetime Modal`, `Contact_Create Opportunity`, `Disqualified Reason
  Prompt`, `Lead Owner Read-Only` (cosmetic only — the field is still writable via API),
  and three `<style>`-injection scripts. None reach into Vue internals any more.

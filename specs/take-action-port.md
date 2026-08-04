# Take Action port + Delivering Coaching status permission (TXB-105)

Status: phase 1 implemented (2026-08-04)
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

## Remaining work

- **Phases 2–4**: port Workshop (8), Individual Session (7) and Selling Training (9)
  actions. The pattern is proven; each pipeline is an independent slice.
- Until then the wizard form script still serves those three pipelines. It must not be
  disabled before they are ported.
- **Kanban**: a non-Admin dragging a Delivering Coaching card gets a server error and the
  card reverts. Correct and enforced, but the drag could be prevented up front using the
  same `can_change_status` answer.
- The wizard's `adminOnly` check remains broken for the unported pipelines. Harmless
  today — no other pipeline uses the flag — but it should die with the script.

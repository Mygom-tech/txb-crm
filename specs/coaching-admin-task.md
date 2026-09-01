# Coaching Admin Task (TXB-208)

When a new **Delivering Coaching** deal is created, one CRM Task is automatically
created for the Admin so the deal lands in her work list (contract prep, data check).

## Trigger

`after_insert` hook on `CRM Deal` (`crm.txb.doc_events.deal.create_coaching_admin_task`,
registered in `hooks.py`). Fires when:

- `pipeline_type == "Delivering Coaching"`, and
- `custom_source_deal` is empty.

Covered paths:

- Won/Sold sales handover (`create_coaching_deal`, TXB-126/173-176) — Individual Session
  and Workshop, including Lead → Deal conversions that later reach Won/Sold.
- Direct creation with the Delivering Coaching pipeline (Contact → Deal modal, API).

Excluded: Workshop QR registration candidate deals (they carry `custom_source_deal`,
one per attendee — a task per registrant would bury the Admin).

## The task

- Title/description: `{first_name}, įkrito naujas delivering coaching deal'as - …`
  (assignee's first name; description carries a `/crm/deals/{name}` link, title carries
  the organization/deal suffix added by `add_task`).
- `reference_doctype`/`reference_docname` point at the delivery deal, so the task shows
  in the deal's Tasks tab and Activity feed.
- `assigned_to`: `crm.txb.api.ownership.approver()` — FCRM Settings
  `custom_claim_approver`, falling back to the longest-standing enabled Admin. Same
  authority as Claim Request tasks. Assignment auto-creates the ToDo + in-app
  notification (no notification when the assignee triggered the insert herself —
  `notify_user` skips self-notifications).
- Status `Backlog`, priority `Medium` (via `crm.txb.pipelines.common.add_task`).

## Idempotency

No dedup bookkeeping: `after_insert` fires once per row, and the handover already
reuses the canonical delivery deal on retries/races (unique `custom_sales_source_deal`),
so a retried Won never re-inserts and never re-tasks. A handover rolled back by its
savepoint takes the task with it (same transaction).

Failures to create the task are logged (`frappe.log_error`) and never abort deal
creation.

## Tests

`crm/txb/test_transitions.py::TestCoachingAdminTask` — handover creates exactly one
linked task with a working link, retry creates no second task, direct DC insert tasks,
registration candidates and other pipelines don't.

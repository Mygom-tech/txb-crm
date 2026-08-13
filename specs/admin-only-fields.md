# Admin-only fields (Delivery Coach)

## The rule

`custom_delivery_coach` on CRM Deal may only be written by the **Admin** role
(`System Manager`) or the `Administrator` account. Everyone else — coaches, managers,
sales users — sees it, but cannot change it.

## Where it is enforced

`crm.txb.permissions.guard_admin_only_fields`, wired into CRM Deal's `validate` in
`hooks.py`. `validate()` is the one place every write path reaches: the side panel, the
all-fields modal, list bulk edit, Kanban, Take Action and a raw REST `set_value`. Guarding
one screen would have left the API open, which is precisely how the `Lead Owner Read-Only`
Form Script "protected" `lead_owner` for years while a PATCH sailed through.

`restrict_admin_only_field` renders the field read-only for non-Admins. That is
**cosmetic** — it exists so the UI does not invite an edit the server will refuse. It is
called from `crm_fields_layout` beside `restrict_owner_field`, so one call covers the
desktop pages, the mobile pages, the all-fields modal and the side panel.

## Why a table, not an `if`

```python
ADMIN_ONLY_FIELDS = {"CRM Deal": (FIELD_DELIVERY_COACH,)}
```

The next "only an Admin may set this" field is then a one-line change that inherits the
guard, the read-only rendering and the tests together. The alternative — a bespoke check
per field — is how this codebase ended up with four copies of the pipeline-status map.

## Decisions

- **Inserts are guarded too**, unlike the status rules. Supplying the coach at creation is
  the same act as changing it. Nothing legitimate does: neither the won-session handover
  (`crm.txb.pipelines.common`) nor the guest registration endpoint sets a coach, and the
  field is not on the Quick Entry layout, so the create modal never offers it. A non-Admin
  insert that omits the field is untouched — verified by test.
- **Not scoped to a pipeline.** The status rule applies only to Delivering Coaching; this
  one applies to every CRM Deal. A Workshop deal's coach is as much an assignment decision
  as a Delivering Coaching one.
- **`custom_delivery_coach_name` is not listed.** It is derived by
  `sync_delivery_coach_name` (`before_validate`) from the guarded field, so guarding the
  source covers it. Guarding both would reject the sync's own write.
- **Clearing counts as changing.** Removing an assignment is as much a decision as making
  one.
- **`custom_assigned_coach` is left alone.** It is a separate Data field on this site, not
  a Link to User, and nothing in the app writes it. If it turns out to be a live second
  door, add it to the tuple.

## Known limits

- Enforcement is role-based, not record-based: any Admin may reassign any deal's coach.
  That matches the ticket and the existing status rule.
- A `frappe.db.set_value` call bypasses `validate()` entirely, as it does for every other
  document rule in this app. In-app code paths do not do this for CRM Deal.
- The field is a site **Custom Field**, not in `crm_deal.json`. Every consumer checks
  `meta.has_field`, and the tests skip where it is not installed.

## Verifying

```bash
bench --site <site> run-tests --module crm.txb.test_permissions   # 33 tests
```

Manually, as a Sales User: open a Delivering Coaching deal → Delivery Coach renders
read-only; a REST `PUT` setting it returns `PermissionError`. As an Admin: both work.

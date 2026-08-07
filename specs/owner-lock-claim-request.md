# Admin-only ownership and the Claim Request flow (TXB-106)

Ownership drives commission. Today any user can move it, and three separate mechanisms
will do so on their behalf — some without being asked. This spec closes every route,
gives non-Admins a way to ask, and removes the two DOM-injecting form scripts that were
the previous attempt.

It also carries two fixes to the Convert to Deal modal that are unrelated to ownership but
block using it at all. They ship as their own commits ahead of the ownership work.

---

## 1. What is actually broken today

**Nothing enforces ownership on the server.** `Lead Owner Read-Only` is a Form Script that
injects CSS to grey out the field. A non-Admin can still `PATCH /api/resource/CRM Lead/X`
with a new `lead_owner` and it succeeds. Deals and Contacts have not even the CSS.

**`protect_owner` has a hole.** `crm/txb/doc_events/lead.py:34` returns early when the
record has no previous owner, so any user may claim an unowned lead. The ticket forbids
exactly this.

**Assignment silently rewrites ownership.** `AssignTo.vue:88`: adding an assignee to a
record with no owner sets the owner to that assignee, and announces it in a toast.
`AssignTo.vue:66` reassigns the owner to "the next available assignee" when the owner is
removed from the assignee list. Both run client-side with no permission check, and either
one launders an owner change past any server guard we add.

**Contact ownership is 93% absent.** `custom_contact_owner` (Link → User) exists but is
set on 43 of 636 contacts. Leads are 1504/1504; deals 82/86.

**Contact → Create Deal is broken for non-Admins.** The `Contact_Create Opportunity` form
script inserts a deal and then fires a second `PUT` to correct the status. TXB-110's
`guard_transition` refuses that edge: `is_allowed("Workshop", "Submitted", "Workshop
submitted")` is `False`. Workshop and Selling Training fail for every non-Admin. Individual
Session and Delivering Coaching survive only because the corrected status equals the
inserted one, so `has_value_changed("status")` is false and the guard returns early.

---

## 2. Scope decisions

| Question | Decision |
| --- | --- |
| Lock owner, or owner *and* assignment? | **Owner field only.** Assignment stays open to everyone, but stops writing the owner field. Commission integrity is the stated purpose; assignment is collaboration. Locking assignment would also make Admin the sole route to granting record visibility, since `has_deal_permission` grants access via assignment. |
| Who receives a Claim Request task? | **A configurable approver setting**, defaulted to Kristina. Role-bound, not person-bound, as the ticket requires. |
| Backfill the unowned records? | **Yes, from the record creator — but only where the creator is a real user.** See §7. |
| Replace the two form scripts? | **Yes, both**, in this ticket. |
| Claim Request storage | **CRM Task plus two custom fields.** No new doctype. See §6. |

### Considered and rejected: Frappe permlevels

Setting `permlevel = 1` on the three owner fields and granting permlevel-1 write to
`System Manager` would give server enforcement and the read-only UI for free —
`handle_perm_level_restrictions` already reads permlevels, and
`Document.validate_higher_perm_levels` already blocks the writes.

Rejected because the failure message is Frappe's generic permlevel wording, and the DoD
asks for a clear error that points at the Claim Request. It would also mean a schema change
on `Contact`, a core Frappe doctype shared with other apps. Explicit hooks match how
TXB-105 and TXB-110 were built in this fork, and are testable in plain Python.

---

## 3. One canonical owner map

`crm/txb/constants.py`:

```python
# The field that carries commission-bearing ownership, per doctype. Single source of
# truth: `crm.permissions.org_hierarchy` scopes visibility by the same fields.
OWNER_FIELDS = {
	"CRM Lead": "lead_owner",
	"CRM Deal": "deal_owner",
	"Contact": "custom_contact_owner",
}
```

`crm/permissions/org_hierarchy.py` deletes its private `_OWNER_FIELD` and imports this. It
only ever indexes it with `CRM Lead` and `CRM Deal`, so the extra `Contact` key is inert
there.

---

## 4. Enforcement — `crm/txb/ownership.py`

Two hooks, registered for all three doctypes. Guarding the document lifecycle closes the
side panel, list bulk edit, kanban, `frappe.client.set_value` and raw REST in one place —
the same reasoning that made `guard_transition` work in TXB-110.

### `claim_owner_on_insert` (before_insert)

```python
def claim_owner_on_insert(doc, method=None):
	field = OWNER_FIELDS.get(doc.doctype)
	if not field or not doc.meta.has_field(field):
		return

	# Guest is never a legitimate owner. The public registration endpoint runs as Guest
	# and sets `deal_owner` deliberately, carrying it over from the source deal; leaving
	# it alone here is what keeps that flow correct without a flag to plumb through.
	if frappe.session.user == "Guest":
		return

	# An Admin may nominate someone else at creation time. Everyone else owns what they
	# create, whatever the client sent.
	if is_admin() and doc.get(field):
		return

	doc.set(field, frappe.session.user)
```

This supersedes `crm.txb.doc_events.lead.assign_owner`, which is deleted along with its
hook entry rather than left running alongside.

### `guard_owner_change` (validate)

```python
def guard_owner_change(doc, method=None):
	if doc.is_new():
		return

	field = OWNER_FIELDS.get(doc.doctype)
	if not field or not doc.meta.has_field(field):
		return

	if not doc.has_value_changed(field):
		return

	if is_admin():
		return

	frappe.throw(
		_("Only an Admin can change the owner. Use Request Ownership to ask for this record."),
		frappe.PermissionError,
		title=_("Not permitted"),
	)
```

A hard throw, not the current silent revert — the DoD asks for a clear error. It fires on
unowned records too, which is the requirement that the first person to touch an unowned
record does not become its owner.

`crm.txb.doc_events.lead.protect_owner` and its `is_privileged` helper are deleted;
`guard_owner_change` replaces both, and corrects `is_privileged`'s use of raw
`System Manager` lookups in favour of the shared `is_admin`.

### Hook registration

```python
"Contact": {
	"before_insert": ["crm.txb.ownership.claim_owner_on_insert"],
	"validate": ["crm.api.contact.validate", "crm.txb.ownership.guard_owner_change"],
},
"CRM Lead": {
	"before_insert": [
		"crm.txb.doc_events.lead.prevent_duplicate",
		"crm.txb.ownership.claim_owner_on_insert",
	],
	"before_validate": ["crm.txb.doc_events.lead.default_disqualified_reason"],
	"validate": ["crm.txb.ownership.guard_owner_change"],
},
"CRM Deal": {
	"before_insert": ["crm.txb.ownership.claim_owner_on_insert"],
	"validate": [
		"crm.txb.permissions.guard_status_change",
		"crm.txb.permissions.guard_transition",
		"crm.txb.ownership.guard_owner_change",
	],
},
```

Owner guard last on CRM Deal: a user changing both status and owner hears about the status
rule first, which is the more common mistake.

---

## 5. Ownership through conversion

All three fall out of `claim_owner_on_insert` — the converting user is the session user, so
the new record is theirs. One consequence is deliberate and worth stating plainly:

- **Lead → Contact.** A newly created Contact gets the converting user. An *existing*
  Contact is reused untouched, keeping its own owner.
- **Lead → Deal.** `LEAD_DEAL_FIELD_MAP` copies `lead_owner → deal_owner` today.
  `claim_owner_on_insert` overwrites it with the converting user. **This changes a shipped
  flow.** It is what the ticket asks for: Contact and Opportunity owners may legitimately
  differ, because a second salesman may open an opportunity on someone else's contact.
- **Contact → Deal.** Creator owns it, which the script being replaced already did.

Conversion is never blocked on source ownership. We add no such check, and
`convert_to_deal`'s existing `write` check on the Lead already passes for anyone who can
see it.

`create_coaching_deal` (the Delivering Coaching handover) currently sets no owner, which is
where three of the four unowned deals came from. Under the new hook the coach who runs the
winning action owns the coaching deal. That is a behaviour change, and the right one.

---

## 6. Claim Request

### Storage

Two custom fields on `CRM Task`, both with `search_index` set because the duplicate check
filters on them:

- `custom_claim_requested_by` — Link → User
- `custom_claim_requested_owner` — Link → User

A dedicated `CRM Claim Request` doctype was considered. It models the data more honestly,
but the ticket describes exactly one artifact — a task the Admin opens, acts on, and closes
— and a second record would need its open/closed state either mirrored from the task or
derived by join. Two records and a synchronisation problem, to buy structure we can read
off the task. Rejected as over-engineering.

### API

`crm/txb/api/ownership.py`:

```python
@frappe.whitelist()
def request_claim(doctype: str, name: str, requested_owner: str, reason: str) -> dict
```

1. Reject unknown doctypes — only the three in `OWNER_FIELDS`.
2. Reject a caller who is already an Admin; they change the field directly.
3. Require a non-empty `reason`.
4. Require read access to the record via `frappe.has_permission`.
5. **Duplicate check** — an existing `CRM Task` with the same `reference_doctype`,
   `reference_docname` and `custom_claim_requested_by`, whose `status` is not in
   `("Done", "Canceled")`. If found, return it with `{"created": False}` and a message
   naming the open request. No second task.
6. Create one `CRM Task` assigned to the approver (§ below), with a `description`
   carrying: requester, requested owner, object type, object name and a link, the current
   owner or "Unassigned", pipeline and status where the doctype has them, the request
   timestamp, and the requester's reason.
7. Return `{"created": True, "task": name}`.

The owner field is never written. No conversion is performed.

Inserted with `ignore_permissions=True`: the requester must be able to create the task
without holding write permission on the record they are asking for.

### Who receives it

A `custom_claim_approver` (Link → User) custom field on `FCRM Settings`, which is a Single.
A patch sets it to `kristina@txbconsulting.com` when that user exists and the field is
empty. Changing the approver is a settings edit, not a code change — which is the ticket's
"tied to the Admin role, not to Kristina".

If it is ever blank, `request_claim` falls back to the oldest enabled user holding
`ADMIN_ROLE` and logs a warning, so a missing setting degrades rather than breaks. If no
such user exists it throws a configuration error naming the setting.

---

## 7. Backfill

`crm/patches/v1_0/backfill_record_owners.py` — for each doctype in `OWNER_FIELDS`, set the
owner field from Frappe's `owner` column where the owner field is empty **and** the creator
is an enabled User other than `Administrator` and `Guest`.

The Administrator and Guest exclusions are the point. On this data set:

| Creator | Unowned contacts |
| --- | --- |
| `Administrator` | 548 |
| real users | 37 |
| `Guest` | 8 |

Handing 548 bulk-imported contacts to the Administrator account would be inventing
ownership, and ownership here decides commission. The patch recovers the 37 contacts and
the 1 deal that have a genuine creator. The remaining 556 stay Unassigned, to be claimed
through the flow this ticket builds or assigned by Kristina when they matter.

The patch runs with `frappe.db.set_value(..., update_modified=False)` so a backfill does
not disturb the modified timestamps used by activity sorting, and it does not fire the
guard.

---

## 8. Frontend

### Owner read-only for non-Admins

`crm/fcrm/doctype/crm_fields_layout/crm_fields_layout.py` already calls
`handle_perm_level_restrictions(field, doctype, ...)` from both `get_fields_layout` (line
80) and `get_sidepanel_sections` (line 139), and that helper's whole job is setting
`field.read_only = 1`. A sibling call to `crm.txb.ownership.restrict_owner_field(field,
doctype)` goes next to each:

```python
def restrict_owner_field(field, doctype):
	"""Render the owner as read-only for anyone who cannot change it.

	Cosmetic only -- `guard_owner_change` is the boundary. This exists so the field does
	not invite an edit that the server will refuse.
	"""
	if field.get("fieldname") != OWNER_FIELDS.get(doctype):
		return
	if is_admin():
		return
	field.read_only = 1
```

`Field.vue` already binds `:disabled="Boolean(field.read_only)"`, so Deal, Lead, Contact,
their mobile pages and the all-fields modal all inherit this from one place.

Because `get_fields_layout` also serves the `Quick Entry` layouts, this covers the creation
modals too: `LeadModal.vue:243` and `DealModal.vue:294` prefill the owner with the current
user, and a non-Admin now sees that value read-only rather than editing it into something
`claim_owner_on_insert` will silently overwrite. Admins keep the editable field, which is
what makes nomination at creation time usable.

`Lead Owner Read-Only` is disabled by patch in the same deploy.

### Request Ownership

A `RequestOwnershipModal.vue`, opened from the header dropdown on Lead, Deal and Contact,
shown only when `!isAdmin`. It states plainly that the owner will not change and that an
Admin decides. Fields: requested owner (Link → User, defaulting to the current user) and a
required reason. On success it toasts the task that was created, or the open request that
already existed.

### Contact → Deal

`CreateDealFromContactModal.vue` replaces `Contact_Create Opportunity`. One insert with the
correct pipeline status — no insert-then-`PUT`, which is what fixes the regression. Pipeline
and status come from `allowedStatusesFor` in `utils/pipelineStatuses.js`, the same
server-owned map the deal page and the convert modal use, rather than the script's fifth
private copy. Disabled by patch in the same deploy.

### Severing assignment from ownership

`AssignTo.vue` loses `ownerField` and the owner-writing half of `saveAssignees`. Assignees
are added and removed; the owner field is not touched. The two toasts that announced the
owner change go with it.

---

## 9. Convert to Deal modal — two fixes

Unrelated to ownership, delivered as separate commits ahead of it.

### Fix 1 — the modal fails to render

`ConvertToDealModal.vue:267` reads `pipeline_type.options` off raw doctype meta and calls
`.split('\n')`. `stores/meta.js:84` `getFields()` mutates that shared meta **in place**:

```js
if (f.fieldtype === 'Select' && typeof f.options === 'string') {
  f.options = f.options.split('\n').map(...)   // f IS doctypesMeta['CRM Deal'].fields[i]
}
```

`doctypesMeta` is a module-level `reactive({})`, and `ViewControls.vue:809` calls
`getFields()` on every list view. So a session that touched the Deals list has an **array**
there, and `(array || '').split` throws `TypeError: ... .split is not a function`. A cold
load straight onto a Lead still has a string and works. That is the intermittency.
`useAutofocusOnOpen`'s `querySelector` error is downstream: the render threw, so the
Dialog's body ref was never a DOM node.

Fix: a `selectFieldOptions(field)` helper in `frontend/src/utils/selectOptions.js`
accepting either shape, used at that one call site. Unit-tested against string, array,
`undefined` and empty input.

The in-place mutation in `stores/meta.js` is the underlying defect and is **not** fixed
here — it is upstream Frappe CRM code with 15+ call sites, and a permissions branch is the
wrong place to destabilise the meta store. Raised as its own ticket.

### Fix 2 — the duplicate Status field

No `CRM Deal-Required Fields` layout record exists, and `status` is the only `reqd=1` field
without a default on CRM Deal. `get_fields_layout` therefore synthesises a section
containing exactly that one field, and the modal renders Status twice: once in the Pipeline
section filtered to the chosen pipeline, once in the `FieldLayout` offering every deal
status including ones invalid for that pipeline.

Removing it by hand cannot work. `crm_fields_layout.py:66` recomputes the required list from
the doctype on every call and re-appends anything missing from the saved layout, so deleting
`status` guarantees its return.

Fix: filter `status` and `pipeline_type` out of the `dealTabs` transform, since the modal
renders both explicitly. The transform's status-patching block goes with them, and so do
the now-unused `dealStatuses` and `getDealStatus`. That block existed only to paper over the
duplication — it was shape-shifting a Link field into a Select.

The Required Fields layout is empty once status is excluded, so the `FieldLayout` section
does not render and the modal is Organization, Pipeline, Convert. Adding fields there via
the pencil icon works properly once status is not fighting it. The leftover
`CRM Deal-Required Fields` record on localhost is left in place; it is harmless and does not
exist in production.

---

## 10. Testing

**`crm/txb/test_ownership.py`** — `frappe.tests.utils.FrappeTestCase`, which is what this
bench's Frappe v15 provides. `frappe.tests.IntegrationTestCase` is v16 and does not import
here.

- `claim_owner_on_insert` sets the creator as owner for each of the three doctypes
- a non-Admin's nominated owner is overwritten; an Admin's is honoured
- the Guest path leaves an explicitly supplied owner alone
- `guard_owner_change` rejects a non-Admin change on an **owned** record
- `guard_owner_change` rejects a non-Admin change on an **unowned** record
- an Admin may change any of the three
- saving a record without touching the owner is unaffected
- Lead → Contact, Lead → Deal and Contact → Deal each land on the converting user
- an existing Contact reused during conversion keeps its owner
- conversion succeeds when the user does not own the source Lead

**`crm/txb/test_claim_request.py`**

- a request creates one task, assigned to the configured approver
- the description carries every field the ticket lists
- the owner field is unchanged afterwards
- a second request from the same requester for the same record returns the existing task
- a second request from a *different* requester for the same record creates its own task
- a closed prior request does not suppress a new one
- an Admin caller is refused
- an empty reason is refused
- the approver falls back to an `ADMIN_ROLE` holder when the setting is blank

**Frontend** — `selectFieldOptions` across all four input shapes; the convert modal excludes
`status` and `pipeline_type` from its layout; `AssignTo` no longer writes an owner field;
the Contact → Deal modal picks the correct status per pipeline. The existing 188 tests stay
green.

**Manual, before QA** — Admin, coach/salesman and Unassigned scenarios across all three
doctypes, per the ticket's Definition of Done, plus a Convert to Deal opened *after*
visiting the Deals list, which is the path that reproduces Fix 1.

---

## 11. Red flags

- **Deploy coupling, twice.** Both script-disabling patches must ship with their Vue
  replacements. Between them users see doubled UI.
- **`bench migrate` has not run on localhost.** `Auto Assign Lead Owner` and
  `Protect Lead Owner` are still enabled there, double-running the ported hooks. It must be
  run before any of this is testable.
- **The hard throw is a behaviour change.** Today a non-Admin editing a lead owner gets a
  toast and a silent revert. They will now get an error. Any integration that PATCHes whole
  documents including the owner field will start failing — and `_OWNER_FIELD`-bearing
  payloads are exactly what a naive round-trip sends.
- **Lead → Deal ownership changes for everyone**, not only non-Admins. Deals converted from
  a lead owned by someone else will now belong to the converter.
- **556 contacts remain unowned** after the backfill. That is the honest outcome of refusing
  to invent ownership for bulk-imported rows, but it means the Claim Request flow carries
  real traffic from day one.
- **`stores/meta.js` still mutates shared meta.** Fix 1 works around it at one call site.
  Any other code reading `options` off raw meta has the same latent bug.

---

## Implementation status

Delivered on `feature/TXB-106-owner-lock`. All thirteen planned tasks complete, plus one
task added mid-flight.

### The hole the plan missed

`crm/api/todo.py` wrote the owner field on **every assignment**:

```python
# Mirror assign_to: the latest assignment owns the record, overriding any prior owner.
frappe.db.set_value(doc.reference_type, doc.reference_name, fieldname, doc.allocated_to, ...)
```

and `clear_owner_on_unassign` nulled it when an assignment was cancelled. Both use
`frappe.db.set_value`, which writes past the document lifecycle and therefore past
`guard_owner_change`. **Any user could take any lead or deal by assigning themselves to
it.** §3 of this spec severed the client half in `AssignTo.vue` and assumed that was the
whole coupling; it was not, and without the server half the feature was bypassable in one
click. Removed as Task 14, with the `test_crm_lead` test that pinned the old behaviour
inverted rather than deleted.

It surfaced as a *failing test*, not a review finding: Task 5's Admin-conversion case went
red because `create_deal` re-assigns the lead's assignees to the new deal, firing the hook
and stamping `deal_owner` back to the lead's owner. A true positive pointing straight at it.

### Corrections to this spec's assumptions

Four assumptions here were wrong about **this site**, and each cost a debugging cycle. The
pattern is consistent: the doctype JSON and the live site disagree, and only the live site
is authoritative.

| Assumed | Actually |
| --- | --- |
| `CRM Lead.organization` is free text | A Property Setter overrides it to a **Link**. `DocField` still reports `Data`. |
| `CRM Lead` needs no mandatory fixture fields | Property Setters make `email` **and** `last_name` `reqd`. |
| A contact's email is on the parent | It lives in the `Contact Email` child table; `Contact.email_id` matches nothing. |
| `deal_owner` is an ordinary field | It carries `permlevel = 1`, so plain Sales Users already saw it read-only. `lead_owner` and `custom_contact_owner` are permlevel 0 and were fully exposed. |

The permlevel finding means §8's read-only work was already half-done for CRM Deal by an
undocumented Property Setter. `restrict_owner_field` ships anyway: it keys on `ADMIN_ROLE`,
which is the rule the ticket states, where permlevel keys on whoever holds a permlevel-1
DocPerm — a wider set. A `Sales Manager` who is not a `System Manager` could edit
`deal_owner` before this change and cannot now.

Also, §5 assumed the conversion rule fell out of `claim_owner_on_insert` alone. It did not
for Admins: `LEAD_DEAL_FIELD_MAP` populated `deal_owner` from the lead, and the hook reads a
populated owner as a deliberate nomination. The map was emptied, along with its client-side
mirror in `ConvertToDealModal.vue`.

### The bench was running doubled handlers

Fifteen Server Scripts that this fork had already migrated to Python were still **enabled**
on `localhost`, because `disable_migrated_server_scripts` had never run there. Frappe runs
`hooks.py` doc_events *and* enabled Server Scripts for the same event, so both fired.

`Auto Assign Lead Owner` — `doc.lead_owner = frappe.session.user`, the pre-TXB-106 rule with
no Admin carve-out — ran *after* `claim_owner_on_insert` and silently overwrote nominated
owners. It broke `test_org_hierarchy` and made `test_ownership`'s CRM Lead cases pass for
the wrong reason. Resolved by running the existing merged patch. Every test run on this
bench since the script migration had been exercising doubled handlers.

`crm/permissions/test_org_hierarchy.py` also imported the v16-only `IntegrationTestCase` and
had never run here at all; migrated to `FrappeTestCase`.

### The upstream test suite has never run on this bench

**23 test files import `IntegrationTestCase` from `frappe.tests`**, which is Frappe v16 only.
On this v15.116.0 bench every one dies at import — including `test_crm_deal.py`,
`test_crm_task.py`, `test_crm_call_log.py` and the whole `lead_syncing` suite. The only
backend tests that have ever executed here are those under `crm/txb/`, migrated in TXB-110,
plus `crm/permissions/test_org_hierarchy.py` and `crm/fcrm/doctype/crm_lead/test_crm_lead.py`
migrated by this ticket because it modified both.

That is worth its own ticket. There is currently no automated coverage of CRM Deal, CRM
Task, call logs or lead syncing on this deployment.

`test_crm_lead.py`, now that it runs, reports **13 errors unrelated to ownership**: every
one is `LinkValidationError` on `organization`, because a Property Setter makes
`CRM Lead.organization` a Link here while the upstream fixtures pass free text. They would
fail identically on `develop` if the module could load. They are not fixed here — the
obvious fix, pre-creating those organizations, would defeat the tests that exist to assert
conversion *creates* them.

### Deliberately not addressed

- **`stores/meta.js` mutates shared doctype meta.** Worked around at one call site. Any
  other code reading `options` off raw meta has the same latent bug. Own ticket.
- **`guard_owner_change` remains bypassable by `frappe.db.set_value` and `db_set`**, as
  every guard in this codebase is. No application code now writes an owner that way — the
  one that did is fixed above, and the only remaining caller is the backfill patch, which
  does it deliberately. A CI grep would make this airtight.
- **556 contacts stay unowned** — 548 created by `Administrator` in a bulk import, 8 by
  `Guest`. The backfill fills only records with a real human creator, because inventing
  ownership would invent commission.
- **Server Scripts are disabled, not deleted.** A System Manager can re-enable one from the
  UI and silently reintroduce double-running. Deleting them belongs in a change that alters
  the merged patch for all environments, not a localhost-only divergence.

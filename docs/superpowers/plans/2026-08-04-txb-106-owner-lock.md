# Admin-only ownership and Claim Request — Implementation Plan (TXB-106)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restrict owner edits on CRM Lead, Contact and CRM Deal to the Admin role, make the creator own what they create on every creation and conversion path, and give non-Admins a Claim Request that raises a CRM Task without touching the owner.

**Architecture:** One canonical owner-field map in `crm/txb/constants.py`, consumed by a new `crm/txb/ownership.py` that registers a `before_insert` and a `validate` hook on all three doctypes — the same document-lifecycle choke point TXB-105 and TXB-110 used, which closes the UI, the kanban, bulk edit and the REST API at once. The read-only rendering rides on the existing `handle_perm_level_restrictions` call sites in `crm_fields_layout.py`, so one function covers desktop, mobile and quick-entry. Claim Requests are ordinary `CRM Task` rows carrying two new custom fields; no new doctype.

**Tech Stack:** Frappe v15.116.0 (pinned deliberately — see `docs/deployment-guide.md`), Python 3.11, Vue 3 + frappe-ui + Pinia, Vitest, prettier 3.2.5.

## Global Constraints

- Python files use **TABS** for indentation. JS/Vue files use **2 spaces**. Match the surrounding file exactly.
- Backend tests subclass `frappe.tests.utils.FrappeTestCase`. **Never** `frappe.tests.IntegrationTestCase` — that is Frappe v16 and does not import on this bench.
- The bench site is `localhost`. Run backend tests with `bench --site localhost run-tests --module <dotted.module>` from `/home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be`.
- Frontend tests run with `yarn test:run` from `apps/crm/frontend`. There is **no** `yarn lint` script — formatting is checked with `npx prettier@3.2.5 --check <files>` only.
- The CRM's "Admin" role is the Frappe role **`System Manager`**, already exported as `ADMIN_ROLE` in `crm/txb/constants.py`. Use `crm.txb.permissions.is_admin`; never re-implement a role check.
- Never run `bench migrate`, database migrations, or anything that sends email or notifications. Dev-database reads and test-created records are fine.
- Statuses that exist on this site and sit outside every pipeline list: `Discovery`, `Demo/Making`, `Delivery`, `Proposal Sent`, `Workshop Delivered`. Use `Discovery` when a test needs an off-pipeline status.
- `pipeline_type` is a Select with **no blank option and no default**, so Frappe's `_set_defaults` fills the first option (`Individual Session`) on any insert that omits it. Always pass `pipeline_type` explicitly in tests.
- `crm.txb.api.actions.execute_action` enforces `has_deal_permission` (owned or assigned only) with no `ignore_permissions` escape. A test user acting on a deal must own it.
- Commit messages end with the two trailer lines used throughout this branch:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01DeDbbexZuPyHuzZny3F7Vq
  ```
- Branch is `feature/TXB-106-owner-lock`, already created off `develop`. Never push to `main` or `develop`.

---

## File Structure

**Created**

| File | Responsibility |
| --- | --- |
| `crm/txb/ownership.py` | The two document hooks and the layout read-only helper. Owner rules only. |
| `crm/txb/api/ownership.py` | The single whitelisted `request_claim` endpoint. |
| `crm/txb/test_ownership.py` | Hook behaviour: insert ownership, the change guard, conversion. |
| `crm/txb/test_claim_request.py` | `request_claim`: content, dedupe, refusals, approver fallback. |
| `crm/patches/v1_0/add_ownership_custom_fields.py` | The three custom fields this ticket introduces. |
| `crm/patches/v1_0/backfill_record_owners.py` | Owner from creator, where the creator is a real user. |
| `crm/patches/v1_0/disable_owner_form_scripts.py` | Retires `Lead Owner Read-Only` and `Contact_Create Opportunity`. |
| `frontend/src/utils/selectOptions.js` | `selectFieldOptions` — tolerate both meta shapes. |
| `frontend/src/utils/convertLayout.js` | `excludeSelfRenderedFields` — strip fields the convert modal renders itself. |
| `frontend/src/components/Modals/RequestOwnershipModal.vue` | The Claim Request form. |
| `frontend/src/components/Modals/CreateDealFromContactModal.vue` | Native Contact → Deal, replacing the injected one. |
| `frontend/tests/unit/selectOptions.test.js` | Unit tests for `selectFieldOptions`. |
| `frontend/tests/unit/convertLayout.test.js` | Unit tests for `excludeSelfRenderedFields`. |

**Modified**

| File | Change |
| --- | --- |
| `crm/txb/constants.py` | Add `OWNER_FIELDS`. |
| `crm/permissions/org_hierarchy.py` | Drop `_OWNER_FIELD`, import `OWNER_FIELDS`. |
| `crm/txb/doc_events/lead.py` | Delete `assign_owner`, `protect_owner`, `is_privileged`. |
| `crm/hooks.py` | Rewire lead hooks; add ownership hooks to Contact, CRM Lead, CRM Deal. |
| `crm/fcrm/doctype/crm_lead/crm_lead.py` | Empty `LEAD_DEAL_FIELD_MAP`. |
| `crm/fcrm/doctype/crm_fields_layout/crm_fields_layout.py` | Call `restrict_owner_field` beside both `handle_perm_level_restrictions` calls. |
| `crm/install.py` | Custom-field creation helper. |
| `crm/patches.txt` | Register the three new patches. |
| `frontend/src/components/Modals/ConvertToDealModal.vue` | Both convert fixes; drop the client-side owner map. |
| `frontend/src/components/AssignTo.vue` | Stop writing the owner field. |
| `frontend/src/pages/Contact.vue`, `Lead.vue`, `Deal.vue`, `MobileDeal.vue` | Header buttons for the two modals. |

---

## Task 1: Convert modal renders regardless of what loaded first

**Files:**
- Create: `frontend/src/utils/selectOptions.js`
- Create: `frontend/tests/unit/selectOptions.test.js`
- Modify: `frontend/src/components/Modals/ConvertToDealModal.vue:263-271`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `selectFieldOptions(field) -> Array<{label: string, value: string}>`, importable from `@/utils/selectOptions`.

**Context.** `stores/meta.js:84` `getFields()` rewrites Select `options` from a newline string into an array of `{label, value}` **on the shared `doctypesMeta` reactive object**. `ViewControls.vue:809` calls `getFields()`, and ViewControls runs on every list view. So after a session touches any CRM Deal list, `ConvertToDealModal`'s `(field?.options || '').split('\n')` receives an array and throws `TypeError: ... .split is not a function`. Do **not** fix `stores/meta.js` — it is upstream code with 15+ call sites and is out of scope for this branch.

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/unit/selectOptions.test.js`:

```js
import { describe, it, expect } from 'vitest'
import { selectFieldOptions } from '@/utils/selectOptions'

describe('selectFieldOptions', () => {
  it('parses a newline-delimited string, as raw doctype meta stores it', () => {
    expect(selectFieldOptions({ options: 'Workshop\nSelling Training' })).toEqual([
      { label: 'Workshop', value: 'Workshop' },
      { label: 'Selling Training', value: 'Selling Training' },
    ])
  })

  it('passes through the array shape getFields() rewrites the shared meta into', () => {
    const already = [
      { label: 'Workshop', value: 'Workshop' },
      { label: 'Selling Training', value: 'Selling Training' },
    ]
    expect(selectFieldOptions({ options: already })).toEqual(already)
  })

  it('drops the blank entry getFields() prepends to non-required selects', () => {
    expect(
      selectFieldOptions({ options: [{ label: '', value: '' }, { label: 'Workshop', value: 'Workshop' }] }),
    ).toEqual([{ label: 'Workshop', value: 'Workshop' }])
  })

  it('drops blank lines from the string shape', () => {
    expect(selectFieldOptions({ options: '\nWorkshop\n' })).toEqual([
      { label: 'Workshop', value: 'Workshop' },
    ])
  })

  it('returns an empty array when meta has not loaded yet', () => {
    expect(selectFieldOptions(undefined)).toEqual([])
    expect(selectFieldOptions({})).toEqual([])
    expect(selectFieldOptions({ options: null })).toEqual([])
  })
})
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be/apps/crm/frontend
yarn test:run tests/unit/selectOptions.test.js
```

Expected: FAIL — cannot resolve `@/utils/selectOptions`.

- [ ] **Step 3: Write the helper**

Create `frontend/src/utils/selectOptions.js`:

```js
/**
 * Options for a Select field, whatever shape its meta is currently in.
 *
 * `stores/meta.js` getFields() rewrites Select `options` from Frappe's newline string
 * into an array of {label, value} *on the shared doctypesMeta object*. Whether a given
 * field is a string or an array therefore depends on whether anything called getFields()
 * for that doctype earlier in the session — ViewControls does, on every list view. Reading
 * raw meta means handling both.
 *
 * @param {Object} field  a doctype meta field, possibly undefined before meta loads
 * @returns {Array<{label: string, value: string}>}
 */
export function selectFieldOptions(field) {
  const options = field?.options

  if (Array.isArray(options)) {
    return options.filter((option) => option?.value)
  }

  if (typeof options !== 'string') return []

  return options
    .split('\n')
    .filter(Boolean)
    .map((value) => ({ label: value, value }))
}
```

- [ ] **Step 4: Run the test and confirm it passes**

```bash
yarn test:run tests/unit/selectOptions.test.js
```

Expected: PASS, 5 tests.

- [ ] **Step 5: Use it in the modal**

In `frontend/src/components/Modals/ConvertToDealModal.vue`, add to the imports near the other `@/utils` import (line 128):

```js
import { selectFieldOptions } from '@/utils/selectOptions'
```

Replace the whole `pipelineTypeOptions` computed (lines 263-271) with:

```js
const pipelineTypeOptions = computed(() => {
  const field = dealMeta.value?.fields?.find(
    (f) => f.fieldname === 'pipeline_type',
  )
  // Labels are translated here rather than in the helper, which stays presentation-free.
  return selectFieldOptions(field).map(({ value }) => ({
    label: __(value),
    value,
  }))
})
```

- [ ] **Step 6: Verify the whole suite and formatting**

```bash
yarn test:run
npx prettier@3.2.5 --check src/utils/selectOptions.js src/components/Modals/ConvertToDealModal.vue tests/unit/selectOptions.test.js
```

Expected: all suites pass; prettier reports all files use the correct style.

- [ ] **Step 7: Commit**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be/apps/crm
git add frontend/src/utils/selectOptions.js frontend/tests/unit/selectOptions.test.js frontend/src/components/Modals/ConvertToDealModal.vue
git commit -m "$(cat <<'EOF'
fix(deals): render Convert to Deal after a list view has loaded meta

stores/meta.js getFields() rewrites Select options from Frappe's newline
string into an array of {label, value} on the shared doctypesMeta object,
and ViewControls calls it on every list view. The convert modal read
pipeline_type.options off raw meta and called .split(), so it threw
whenever the session had already touched a CRM Deal list -- and worked on
a cold load straight onto a lead, which is what made it look intermittent.

selectFieldOptions accepts either shape. The mutation in stores/meta.js is
the underlying defect and is left alone: it is upstream code with 15+ call
sites, and a permissions branch is the wrong place to destabilise it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DeDbbexZuPyHuzZny3F7Vq
EOF
)"
```

---

## Task 2: Convert modal stops rendering Status twice

**Files:**
- Create: `frontend/src/utils/convertLayout.js`
- Create: `frontend/tests/unit/convertLayout.test.js`
- Modify: `frontend/src/components/Modals/ConvertToDealModal.vue:289-314`

**Interfaces:**
- Consumes: nothing.
- Produces: `excludeSelfRenderedFields(tabs, fieldnames) -> Array` and `SELF_RENDERED_FIELDS = ['status', 'pipeline_type']`, importable from `@/utils/convertLayout`.

**Context.** No `CRM Deal-Required Fields` layout record exists, and `status` is the only `reqd=1` field without a default on CRM Deal. `get_fields_layout` therefore synthesises a section containing exactly that field, so the modal renders Status twice — once in its own Pipeline section filtered to the chosen pipeline, once in the `FieldLayout` offering every deal status. Removing it by hand cannot work: `crm_fields_layout.py:66` recomputes the required list from the doctype on every call and re-appends anything missing from the saved layout.

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/unit/convertLayout.test.js`:

```js
import { describe, it, expect } from 'vitest'
import {
  excludeSelfRenderedFields,
  SELF_RENDERED_FIELDS,
} from '@/utils/convertLayout'

const tabsWith = (...fieldnames) => [
  {
    name: 'first_tab',
    sections: [
      {
        name: 'required_fields_section_abcd',
        columns: [{ name: 'col', fields: fieldnames.map((fieldname) => ({ fieldname })) }],
      },
    ],
  },
]

describe('excludeSelfRenderedFields', () => {
  it('drops the synthesised status field, leaving no section to render', () => {
    // status is the only reqd field without a default on CRM Deal, so the synthesised
    // section holds nothing else -- and [] is how FieldLayout is told not to render.
    expect(excludeSelfRenderedFields(tabsWith('status'), SELF_RENDERED_FIELDS)).toEqual([])
  })

  it('drops pipeline_type too, should it ever become required', () => {
    const result = excludeSelfRenderedFields(
      tabsWith('pipeline_type', 'deal_value'),
      SELF_RENDERED_FIELDS,
    )
    expect(result[0].sections[0].columns[0].fields).toEqual([
      { fieldname: 'deal_value' },
    ])
  })

  it('keeps unrelated required fields', () => {
    const result = excludeSelfRenderedFields(
      tabsWith('deal_value', 'close_date'),
      SELF_RENDERED_FIELDS,
    )
    expect(result[0].sections[0].columns[0].fields).toHaveLength(2)
  })

  it('does not mutate the layout it was given', () => {
    // dealTabs.data is the resource's own cached value; a transform that edited it in
    // place would corrupt the cache the way stores/meta.js does.
    const tabs = tabsWith('status', 'deal_value')
    excludeSelfRenderedFields(tabs, SELF_RENDERED_FIELDS)
    expect(tabs[0].sections[0].columns[0].fields).toHaveLength(2)
  })

  it('tolerates the empty and undefined layouts the endpoint can return', () => {
    expect(excludeSelfRenderedFields(undefined, SELF_RENDERED_FIELDS)).toEqual([])
    expect(excludeSelfRenderedFields([], SELF_RENDERED_FIELDS)).toEqual([])
    expect(
      excludeSelfRenderedFields([{ name: 'first_tab', sections: [] }], SELF_RENDERED_FIELDS),
    ).toEqual([])
  })
})
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be/apps/crm/frontend
yarn test:run tests/unit/convertLayout.test.js
```

Expected: FAIL — cannot resolve `@/utils/convertLayout`.

- [ ] **Step 3: Write the helper**

Create `frontend/src/utils/convertLayout.js`:

```js
/**
 * Fields the Convert to Deal modal renders with its own controls.
 *
 * `get_fields_layout(type="Required Fields")` synthesises a section from every reqd field
 * without a default. On CRM Deal that is `status`, which the modal already renders in its
 * Pipeline section — filtered to the chosen pipeline, where the synthesised one offers
 * every status including ones invalid for that pipeline. `pipeline_type` is listed too so
 * the duplicate cannot reappear if it is ever made required.
 */
export const SELF_RENDERED_FIELDS = ['status', 'pipeline_type']

/**
 * Strip the given fieldnames from a fields layout, dropping anything left empty.
 *
 * Returns [] when nothing survives, because the modal keys its FieldLayout on
 * `dealTabs.data?.length` — an empty array is how the section is told not to render.
 *
 * @param {Array} tabs        as returned by get_fields_layout
 * @param {string[]} fieldnames
 * @returns {Array} tabs containing at least one field, or []
 */
export function excludeSelfRenderedFields(tabs, fieldnames) {
  const kept = (tabs || []).map((tab) => ({
    ...tab,
    sections: (tab.sections || [])
      .map((section) => ({
        ...section,
        columns: (section.columns || [])
          .map((column) => ({
            ...column,
            fields: (column.fields || []).filter(
              (field) => !fieldnames.includes(field?.fieldname),
            ),
          }))
          .filter((column) => column.fields.length),
      }))
      .filter((section) => section.columns.length),
  }))

  return kept.filter((tab) => tab.sections.length)
}
```

- [ ] **Step 4: Run the test and confirm it passes**

```bash
yarn test:run tests/unit/convertLayout.test.js
```

Expected: PASS, 5 tests.

- [ ] **Step 5: Use it in the modal and delete what it replaces**

In `frontend/src/components/Modals/ConvertToDealModal.vue`:

Add to the imports beside the other `@/utils` imports:

```js
import {
  excludeSelfRenderedFields,
  SELF_RENDERED_FIELDS,
} from '@/utils/convertLayout'
```

Delete the `dealStatuses` computed (line 289) entirely:

```js
const dealStatuses = computed(() => statusOptions('deal'))
```

Replace the `dealTabs` resource's `transform` (lines 296-313) with:

```js
  transform: (_tabs) => excludeSelfRenderedFields(_tabs, SELF_RENDERED_FIELDS),
```

Change the `statusesStore()` destructure on line 145 from:

```js
const { statusOptions, getDealStatus, pipelineStatuses } = statusesStore()
```

to:

```js
const { pipelineStatuses } = statusesStore()
```

- [ ] **Step 6: Confirm nothing else in the file used what you deleted**

```bash
grep -n "statusOptions\|getDealStatus\|dealStatuses" src/components/Modals/ConvertToDealModal.vue
```

Expected: no output. If anything remains, it is a real usage — stop and report it rather than deleting the usage.

- [ ] **Step 7: Verify the whole suite and formatting**

```bash
yarn test:run
npx prettier@3.2.5 --check src/utils/convertLayout.js src/components/Modals/ConvertToDealModal.vue tests/unit/convertLayout.test.js
```

Expected: all suites pass; prettier clean.

- [ ] **Step 8: Commit**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be/apps/crm
git add frontend/src/utils/convertLayout.js frontend/tests/unit/convertLayout.test.js frontend/src/components/Modals/ConvertToDealModal.vue
git commit -m "$(cat <<'EOF'
fix(deals): stop Convert to Deal rendering Status twice

status is the only reqd field without a default on CRM Deal, so
get_fields_layout synthesises a Required Fields section containing just
it -- next to the Status select the modal already renders, filtered to the
chosen pipeline. Removing it from the layout by hand cannot work either:
the required list is recomputed from the doctype on every call and
re-appended whenever it is missing.

The modal now excludes the fields it renders itself. That also retires the
transform block that shape-shifted status from a Link into a Select, which
existed only to paper over the duplication.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DeDbbexZuPyHuzZny3F7Vq
EOF
)"
```

---

## Task 3: The creator owns what they create

**Files:**
- Modify: `crm/txb/constants.py`
- Modify: `crm/permissions/org_hierarchy.py:7-10`
- Create: `crm/txb/ownership.py`
- Modify: `crm/txb/doc_events/lead.py` (delete `assign_owner`)
- Modify: `crm/hooks.py` (`doc_events`)
- Create: `crm/txb/test_ownership.py`

**Interfaces:**
- Consumes: `crm.txb.permissions.is_admin(user=None) -> bool`, already present.
- Produces:
  - `crm.txb.constants.OWNER_FIELDS: dict[str, str]`
  - `crm.txb.ownership.claim_owner_on_insert(doc, method=None) -> None`
  - `crm.txb.ownership.owner_field(doctype: str) -> str | None`

- [ ] **Step 1: Write the failing test**

Create `crm/txb/test_ownership.py`:

```python
# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-106: who owns a record, and who may change that.

Ownership decides commission, so it is enforced on the document lifecycle rather than in
any one screen -- `before_insert` decides the initial owner, `validate` refuses later
changes. These tests exercise the hooks directly, since the point is that every write path
reaches them.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.txb.constants import ADMIN_ROLE, OWNER_FIELDS

SALESMAN = "txb-owner-sales@example.com"
OTHER_SALESMAN = "txb-owner-sales2@example.com"
ADMIN = "txb-owner-admin@example.com"


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


class OwnershipTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(SALESMAN, ["Sales User"])
		ensure_user(OTHER_SALESMAN, ["Sales User"])
		ensure_user(ADMIN, ["Sales User", ADMIN_ROLE])
		frappe.db.commit()  # nosemgrep -- roles must outlive per-test rollback

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def make_lead(self, **kwargs):
		return frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": "Owner", "last_name": "Test", **kwargs}
		).insert(ignore_permissions=True)

	def make_deal(self, **kwargs):
		# pipeline_type is always explicit: the Select has no blank option and no default,
		# so Frappe's _set_defaults would otherwise silently fill "Individual Session".
		return frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"pipeline_type": "Individual Session",
				"status": "Submitted",
				**kwargs,
			}
		).insert(ignore_permissions=True)

	def make_contact(self, **kwargs):
		return frappe.get_doc(
			{"doctype": "Contact", "first_name": "Owner", "last_name": "Test", **kwargs}
		).insert(ignore_permissions=True)


class TestOwnerOnInsert(OwnershipTestCase):
	def test_lead_creator_becomes_owner(self):
		frappe.set_user(SALESMAN)
		self.assertEqual(self.make_lead().lead_owner, SALESMAN)

	def test_deal_creator_becomes_owner(self):
		frappe.set_user(SALESMAN)
		self.assertEqual(self.make_deal().deal_owner, SALESMAN)

	def test_contact_creator_becomes_owner(self):
		frappe.set_user(SALESMAN)
		self.assertEqual(self.make_contact().custom_contact_owner, SALESMAN)

	def test_a_non_admin_cannot_nominate_someone_else_at_creation(self):
		"""The ticket is explicit: the creator owns it, whatever the client sent."""
		frappe.set_user(SALESMAN)
		deal = self.make_deal(deal_owner=OTHER_SALESMAN)
		self.assertEqual(deal.deal_owner, SALESMAN)

	def test_an_admin_may_nominate_someone_else_at_creation(self):
		frappe.set_user(ADMIN)
		deal = self.make_deal(deal_owner=SALESMAN)
		self.assertEqual(deal.deal_owner, SALESMAN)

	def test_an_admin_who_nominates_nobody_owns_it(self):
		frappe.set_user(ADMIN)
		self.assertEqual(self.make_deal().deal_owner, ADMIN)

	def test_guest_never_becomes_an_owner(self):
		"""The public registration endpoint runs as Guest and sets deal_owner itself,
		carrying it over from the source deal. Overwriting it with "Guest" would be wrong."""
		frappe.set_user("Guest")
		try:
			deal = self.make_deal(deal_owner=SALESMAN)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(deal.deal_owner, SALESMAN)
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be
bench --site localhost run-tests --module crm.txb.test_ownership
```

Expected: FAIL at import — `cannot import name 'OWNER_FIELDS' from 'crm.txb.constants'`.

- [ ] **Step 3: Add the canonical owner map**

Append to `crm/txb/constants.py` (TABS, and note this file uses no leading tabs at module level):

```python
# The field carrying commission-bearing ownership, per doctype.
#
# Single source of truth: `crm.permissions.org_hierarchy` scopes record visibility by the
# same fields, and `crm.txb.ownership` guards them. Contact's is a site Custom Field, so
# every consumer checks `meta.has_field` before using it.
OWNER_FIELDS = {
	"CRM Lead": "lead_owner",
	"CRM Deal": "deal_owner",
	"Contact": "custom_contact_owner",
}
```

- [ ] **Step 4: Point org_hierarchy at it**

In `crm/permissions/org_hierarchy.py`, delete lines 7-10:

```python
_OWNER_FIELD = {
	"CRM Lead": "lead_owner",
	"CRM Deal": "deal_owner",
}
```

Add to the imports at the top:

```python
from crm.txb.constants import OWNER_FIELDS
```

Change the single usage at line 34 from `owner_field = _OWNER_FIELD[doctype]` to:

```python
	# Only ever called with CRM Lead and CRM Deal; the map's Contact entry is inert here.
	owner_field = OWNER_FIELDS[doctype]
```

- [ ] **Step 5: Write the insert hook**

Create `crm/txb/ownership.py`:

```python
"""Who owns a Lead, Contact or Opportunity, and who may change that.

Ownership decides commission, so the rule lives on the document lifecycle rather than in
any one screen. Every write path -- the side panel, list bulk edit, Kanban,
`frappe.client.set_value` and raw REST -- reaches `before_insert` and `validate`, so
guarding there closes all of them at once. The same reasoning put the status rules in
`crm.txb.permissions`.

Replaces the `Auto Assign Lead Owner` and `Protect Lead Owner` Server Scripts and the
`Lead Owner Read-Only` Form Script, which enforced nothing -- it injected CSS.
"""

import frappe
from frappe import _

from crm.txb.constants import OWNER_FIELDS
from crm.txb.permissions import is_admin

GUEST = "Guest"


def owner_field(doctype: str) -> str | None:
	"""The owner fieldname for `doctype`, or None if it has no owner concept."""
	return OWNER_FIELDS.get(doctype)


def claim_owner_on_insert(doc, method=None):
	"""Own every new record as the creating user.

	Covers direct creation and all three conversions -- Lead to Contact, Lead to Deal and
	Contact to Deal -- because in each the converting user is the session user.
	"""
	field = owner_field(doc.doctype)
	if not field or not doc.meta.has_field(field):
		return

	# Guest is never a legitimate owner. The public registration endpoint runs as Guest
	# and sets `deal_owner` deliberately, carrying it over from the source deal; leaving
	# that alone here is what keeps the flow correct without a flag to plumb through.
	if frappe.session.user == GUEST:
		return

	# An Admin may hand a new record to someone else. Everyone else owns what they create,
	# whatever the client sent -- a non-Admin cannot nominate an owner at creation either.
	if is_admin() and doc.get(field):
		return

	doc.set(field, frappe.session.user)
```

- [ ] **Step 6: Delete `assign_owner` and rewire the hooks**

In `crm/txb/doc_events/lead.py`, delete the whole `assign_owner` function (lines 18-25) and its docstring. Leave `protect_owner`, `is_privileged`, `default_disqualified_reason` and `prevent_duplicate` alone — Task 4 removes the first two.

In `crm/hooks.py`, inside `doc_events`, change the `Contact` and `CRM Lead` entries and add a `before_insert` to `CRM Deal`:

```python
	"Contact": {
		"before_insert": ["crm.txb.ownership.claim_owner_on_insert"],
		"before_validate": ["crm.txb.doc_events.contact.sync_organization"],
		"validate": ["crm.api.contact.validate"],
	},
	"CRM Lead": {
		# prevent_duplicate first: it throws, so nothing else should run before it.
		"before_insert": [
			"crm.txb.doc_events.lead.prevent_duplicate",
			"crm.txb.ownership.claim_owner_on_insert",
		],
		"before_validate": [
			"crm.txb.doc_events.lead.protect_owner",
			"crm.txb.doc_events.lead.default_disqualified_reason",
		],
	},
```

and in the existing `"CRM Deal"` entry, add a `before_insert` key above `before_validate`:

```python
		"before_insert": ["crm.txb.ownership.claim_owner_on_insert"],
```

- [ ] **Step 7: Run the tests and confirm they pass**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be
bench --site localhost run-tests --module crm.txb.test_ownership
```

Expected: PASS, 7 tests.

- [ ] **Step 8: Confirm nothing else regressed**

```bash
bench --site localhost run-tests --module crm.txb.test_doc_events
bench --site localhost run-tests --module crm.permissions.test_org_hierarchy
```

Expected: both pass. If `test_doc_events` referenced `assign_owner`, delete that test — the behaviour it covered now belongs to `test_ownership.py` and duplicating it would be two tests for one rule. Report the deletion.

- [ ] **Step 9: Commit**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be/apps/crm
git add crm/txb/constants.py crm/permissions/org_hierarchy.py crm/txb/ownership.py crm/txb/doc_events/lead.py crm/hooks.py crm/txb/test_ownership.py
git commit -m "$(cat <<'EOF'
feat(txb): the creator owns every new lead, contact and opportunity

One canonical owner map in constants, consumed by both the new ownership
hooks and org_hierarchy's visibility scoping, which carried its own copy.

before_insert covers direct creation and all three conversions, since in
each the converting user is the session user. Two carve-outs: Guest is
never an owner, which is what lets the public registration endpoint keep
setting deal_owner from the source deal; and an Admin may hand a new
record to someone else.

Supersedes assign_owner, which forced the session user unconditionally --
including for leads created by an integration.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DeDbbexZuPyHuzZny3F7Vq
EOF
)"
```

---

## Task 4: Only an Admin may move an owner

**Files:**
- Modify: `crm/txb/ownership.py`
- Modify: `crm/txb/doc_events/lead.py` (delete `protect_owner`, `is_privileged`)
- Modify: `crm/hooks.py`
- Modify: `crm/txb/test_ownership.py`

**Interfaces:**
- Consumes: `crm.txb.ownership.owner_field`, `crm.txb.permissions.is_admin`.
- Produces: `crm.txb.ownership.guard_owner_change(doc, method=None) -> None`, raising `frappe.PermissionError`.

- [ ] **Step 1: Write the failing test**

Append to `crm/txb/test_ownership.py`:

```python
class TestOwnerChangeGuard(OwnershipTestCase):
	def test_a_salesman_cannot_take_an_owned_deal(self):
		deal = self.make_deal(deal_owner=OTHER_SALESMAN)

		frappe.set_user(SALESMAN)
		deal.reload()
		deal.deal_owner = SALESMAN

		with self.assertRaises(frappe.PermissionError):
			deal.save(ignore_permissions=True)

	def test_a_salesman_cannot_take_an_unowned_deal(self):
		"""The hole in the script this replaces: it returned early when there was no
		previous owner, so anyone could claim an unowned record."""
		deal = self.make_deal()
		frappe.db.set_value("CRM Deal", deal.name, "deal_owner", "")

		frappe.set_user(SALESMAN)
		deal.reload()
		deal.deal_owner = SALESMAN

		with self.assertRaises(frappe.PermissionError):
			deal.save(ignore_permissions=True)

	def test_a_salesman_cannot_take_an_owned_lead(self):
		lead = self.make_lead(lead_owner=OTHER_SALESMAN)

		frappe.set_user(SALESMAN)
		lead.reload()
		lead.lead_owner = SALESMAN

		with self.assertRaises(frappe.PermissionError):
			lead.save(ignore_permissions=True)

	def test_a_salesman_cannot_take_an_owned_contact(self):
		contact = self.make_contact(custom_contact_owner=OTHER_SALESMAN)

		frappe.set_user(SALESMAN)
		contact.reload()
		contact.custom_contact_owner = SALESMAN

		with self.assertRaises(frappe.PermissionError):
			contact.save(ignore_permissions=True)

	def test_an_admin_may_change_any_owner(self):
		deal = self.make_deal(deal_owner=OTHER_SALESMAN)

		frappe.set_user(ADMIN)
		deal.reload()
		deal.deal_owner = SALESMAN
		deal.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("CRM Deal", deal.name, "deal_owner"), SALESMAN)

	def test_a_salesman_may_still_edit_other_fields_on_a_deal_they_own(self):
		"""Day-to-day work must not break; the ticket restricts one field, not the record."""
		deal = self.make_deal(deal_owner=SALESMAN)

		frappe.set_user(SALESMAN)
		deal.reload()
		deal.next_step = "Call them back"
		deal.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("CRM Deal", deal.name, "next_step"), "Call them back")

	def test_saving_without_touching_the_owner_is_allowed(self):
		"""A client that round-trips the whole document must not trip the guard."""
		deal = self.make_deal(deal_owner=OTHER_SALESMAN)

		frappe.set_user(SALESMAN)
		deal.reload()
		deal.deal_owner = OTHER_SALESMAN  # unchanged
		deal.next_step = "Unchanged owner"
		deal.save(ignore_permissions=True)

		self.assertEqual(
			frappe.db.get_value("CRM Deal", deal.name, "deal_owner"), OTHER_SALESMAN
		)

	def test_the_error_names_the_claim_request(self):
		"""The DoD asks for a clear error, not a silent revert."""
		deal = self.make_deal(deal_owner=OTHER_SALESMAN)

		frappe.set_user(SALESMAN)
		deal.reload()
		deal.deal_owner = SALESMAN

		with self.assertRaises(frappe.PermissionError) as caught:
			deal.save(ignore_permissions=True)

		self.assertIn("Request Ownership", str(caught.exception))
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be
bench --site localhost run-tests --module crm.txb.test_ownership
```

Expected: the eight new tests FAIL. The `assertRaises` ones fail because nothing throws; the last one fails on the missing message.

- [ ] **Step 3: Write the guard**

Append to `crm/txb/ownership.py`:

```python
def guard_owner_change(doc, method=None):
	"""Refuse an owner change made by anyone but an Admin.

	Inserts are exempt -- `claim_owner_on_insert` has already decided the initial owner,
	and the two rules would otherwise contradict each other.

	This fires for unowned records too. That is the requirement, and the hole in the
	script it replaces: `protect_owner` returned early when there was no previous owner,
	so the first person to touch an unassigned record could claim it.
	"""
	if doc.is_new():
		return

	field = owner_field(doc.doctype)
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

- [ ] **Step 4: Delete what it replaces**

In `crm/txb/doc_events/lead.py`, delete `protect_owner` and `is_privileged` in full, along with the now-unused `ADMINISTRATOR` and `SYSTEM_MANAGER` constants and the `from frappe import _` import **only if** nothing else in the file uses it (`prevent_duplicate` does — keep it). Update the module docstring's script list to drop `Protect Lead Owner` and `Auto Assign Lead Owner`.

- [ ] **Step 5: Wire the guard**

In `crm/hooks.py`, `doc_events`:

```python
	"Contact": {
		"before_insert": ["crm.txb.ownership.claim_owner_on_insert"],
		"before_validate": ["crm.txb.doc_events.contact.sync_organization"],
		"validate": ["crm.api.contact.validate", "crm.txb.ownership.guard_owner_change"],
	},
	"CRM Lead": {
		# prevent_duplicate first: it throws, so nothing else should run before it.
		"before_insert": [
			"crm.txb.doc_events.lead.prevent_duplicate",
			"crm.txb.ownership.claim_owner_on_insert",
		],
		"before_validate": ["crm.txb.doc_events.lead.default_disqualified_reason"],
		"validate": ["crm.txb.ownership.guard_owner_change"],
	},
```

and in `"CRM Deal"`, append to the existing `validate` list so the owner guard runs **last**:

```python
		"validate": [
			"crm.txb.permissions.guard_status_change",
			"crm.txb.permissions.guard_transition",
			# Owner last: a user changing status and owner together hears about the
			# status rule first, which is the more common mistake.
			"crm.txb.ownership.guard_owner_change",
		],
```

- [ ] **Step 6: Run the tests and confirm they pass**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be
bench --site localhost run-tests --module crm.txb.test_ownership
```

Expected: PASS, 15 tests.

- [ ] **Step 7: Confirm the other suites still pass**

```bash
bench --site localhost run-tests --module crm.txb.test_doc_events
bench --site localhost run-tests --module crm.txb.test_permissions
bench --site localhost run-tests --module crm.txb.test_transitions
```

Expected: all pass. If a `test_doc_events` test covered `protect_owner`, delete it and report the deletion — that rule now lives in `test_ownership.py`.

- [ ] **Step 8: Commit**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be/apps/crm
git add crm/txb/ownership.py crm/txb/doc_events/lead.py crm/hooks.py crm/txb/test_ownership.py
git commit -m "$(cat <<'EOF'
feat(txb): only an Admin may change an owner

Guards CRM Lead, Contact and CRM Deal on validate, so the side panel,
Kanban, bulk edit and the REST API are all closed by one rule. A hard
throw naming Request Ownership, not the silent revert the old script did:
the ticket asks for a clear error.

Fires on unowned records too. That was the hole in protect_owner, which
returned early when there was no previous owner -- so the first person to
touch an unassigned record could claim it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DeDbbexZuPyHuzZny3F7Vq
EOF
)"
```

---

## Task 5: Conversion hands the new record to the converter

**Files:**
- Modify: `crm/fcrm/doctype/crm_lead/crm_lead.py:18`
- Modify: `frontend/src/components/Modals/ConvertToDealModal.vue:316-317`
- Modify: `crm/txb/test_ownership.py`

**Interfaces:**
- Consumes: `claim_owner_on_insert` from Task 3.
- Produces: nothing new.

**Context.** `LEAD_DEAL_FIELD_MAP = {"lead_owner": "deal_owner"}` copies the lead's owner onto the new deal. For a **non-Admin** converter that is harmless — `claim_owner_on_insert` overwrites it. For an **Admin** it is not: the hook treats a populated owner as a deliberate nomination and returns early, so an Admin converting someone else's lead would produce a deal owned by the lead's owner rather than by the Admin. The ticket says the creating user owns the new opportunity, without exception. Deleting the map entry is the fix; the modal carries a client-side mirror of it that must go too.

- [ ] **Step 1: Write the failing test**

Append to `crm/txb/test_ownership.py`:

```python
class TestConversionOwnership(OwnershipTestCase):
	def convert(self, lead):
		from crm.fcrm.doctype.crm_lead.crm_lead import convert_to_deal

		return convert_to_deal(lead=lead.name, deal={"pipeline_type": "Individual Session"})

	def test_the_converter_owns_the_new_deal(self):
		lead = self.make_lead(lead_owner=OTHER_SALESMAN, organization="Convert Co")

		frappe.set_user(SALESMAN)
		deal_name = self.convert(lead)

		self.assertEqual(frappe.db.get_value("CRM Deal", deal_name, "deal_owner"), SALESMAN)

	def test_an_admin_converting_also_owns_the_new_deal(self):
		"""LEAD_DEAL_FIELD_MAP used to carry lead_owner across, which the insert hook reads
		as an Admin's deliberate nomination -- so the Admin path silently kept the lead's
		owner. The map entry is gone."""
		lead = self.make_lead(lead_owner=OTHER_SALESMAN, organization="Convert Admin Co")

		frappe.set_user(ADMIN)
		deal_name = self.convert(lead)

		self.assertEqual(frappe.db.get_value("CRM Deal", deal_name, "deal_owner"), ADMIN)

	def test_the_converter_owns_the_new_contact(self):
		lead = self.make_lead(
			lead_owner=OTHER_SALESMAN,
			organization="Contact Co",
			email="convert-owner@example.com",
		)

		frappe.set_user(SALESMAN)
		self.convert(lead)

		contact = frappe.db.get_value(
			"Contact", {"email_id": "convert-owner@example.com"}, "custom_contact_owner"
		)
		self.assertEqual(contact, SALESMAN)

	def test_conversion_is_not_blocked_by_not_owning_the_lead(self):
		"""Explicitly required: conversion must not depend on source ownership."""
		lead = self.make_lead(lead_owner=OTHER_SALESMAN, organization="Not Mine Co")

		frappe.set_user(SALESMAN)
		self.assertTrue(self.convert(lead))

	def test_an_existing_contact_keeps_its_owner_through_conversion(self):
		"""A reused Contact is not a new record, so its owner is not up for grabs."""
		self.make_contact(
			first_name="Reused",
			last_name="Person",
			custom_contact_owner=OTHER_SALESMAN,
			email_ids=[{"email_id": "reused-owner@example.com", "is_primary": 1}],
		)
		lead = self.make_lead(
			first_name="Reused",
			last_name="Person",
			lead_owner=OTHER_SALESMAN,
			organization="Reuse Co",
		)

		frappe.set_user(SALESMAN)
		self.convert(lead)

		owner = frappe.db.get_value(
			"Contact", {"email_id": "reused-owner@example.com"}, "custom_contact_owner"
		)
		self.assertEqual(owner, OTHER_SALESMAN)
```

- [ ] **Step 2: Run it and confirm the Admin case fails**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be
bench --site localhost run-tests --module crm.txb.test_ownership
```

Expected: `test_an_admin_converting_also_owns_the_new_deal` FAILS with `OTHER_SALESMAN != ADMIN`. The others should already pass — if any other fails, report it before changing code.

- [ ] **Step 3: Empty the field map**

In `crm/fcrm/doctype/crm_lead/crm_lead.py`, replace line 18:

```python
LEAD_DEAL_FIELD_MAP = {"lead_owner": "deal_owner"}
```

with:

```python
# Deliberately empty. It used to carry lead_owner onto deal_owner, but TXB-106 gives the
# new opportunity to whoever converted the lead -- a second salesman may legitimately open
# an opportunity on someone else's lead, and the commission follows the converter. The
# mapping is kept as a hook for future lead/deal field pairs rather than deleted.
LEAD_DEAL_FIELD_MAP = {}
```

- [ ] **Step 4: Remove the client-side mirror**

In `frontend/src/components/Modals/ConvertToDealModal.vue`, replace lines 316-317:

```js
const leadDealFieldMap = { deal_owner: 'lead_owner' }
const skipPrefillFields = ['organization', 'status']
```

with:

```js
// Empty, mirroring LEAD_DEAL_FIELD_MAP on the server. deal_owner used to be prefilled
// from the lead; TXB-106 gives the deal to the converting user instead, and a prefilled
// value would read as an Admin's deliberate nomination and be honoured.
const leadDealFieldMap = {}
const skipPrefillFields = ['organization', 'status', 'deal_owner']
```

- [ ] **Step 5: Run the tests and confirm they pass**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be
bench --site localhost run-tests --module crm.txb.test_ownership
bench --site localhost run-tests --module crm.fcrm.doctype.crm_lead.test_crm_lead
```

Expected: `test_ownership` passes 20 tests. `test_crm_lead` may have a test asserting the old owner mapping — if so it is now wrong, and the fix is to update its expectation to the converting user, not to restore the map. Report any such change.

- [ ] **Step 6: Frontend suite and formatting**

```bash
cd apps/crm/frontend && yarn test:run
npx prettier@3.2.5 --check src/components/Modals/ConvertToDealModal.vue
```

Expected: pass and clean.

- [ ] **Step 7: Commit**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be/apps/crm
git add crm/fcrm/doctype/crm_lead/crm_lead.py frontend/src/components/Modals/ConvertToDealModal.vue crm/txb/test_ownership.py
git commit -m "$(cat <<'EOF'
feat(txb): converting a lead hands the opportunity to the converter

LEAD_DEAL_FIELD_MAP carried lead_owner onto deal_owner. For a non-Admin
the insert hook overwrote it, so the rule looked correct; for an Admin the
populated field read as a deliberate nomination and was honoured, leaving
the deal owned by the lead's owner. Emptying the map -- and the modal's
client-side mirror of it -- makes the converter the owner for everyone.

A second salesman opening an opportunity on someone else's contact is the
case the ticket calls out, and the commission follows the converter.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DeDbbexZuPyHuzZny3F7Vq
EOF
)"
```

---

## Task 6: Custom fields for the Claim Request and the approver setting

**Files:**
- Modify: `crm/install.py`
- Create: `crm/patches/v1_0/add_ownership_custom_fields.py`
- Modify: `crm/patches.txt`

**Interfaces:**
- Consumes: nothing.
- Produces: `crm.install.add_ownership_custom_fields() -> None`, and these fields on the site:
  - `CRM Task.custom_claim_requested_by` — Link → User, `search_index`
  - `CRM Task.custom_claim_requested_owner` — Link → User, `search_index`
  - `FCRM Settings.custom_claim_approver` — Link → User

- [ ] **Step 1: Write the installer**

Append to `crm/install.py`, following the shape of `add_email_account_custom_field` at line 308:

```python
def add_ownership_custom_fields():
	"""Fields backing the TXB-106 Claim Request flow.

	The two CRM Task fields are indexed because the duplicate check filters on them
	together with the reference -- one open request per requester per record.
	"""
	if not frappe.get_meta("CRM Task").has_field("custom_claim_requested_by"):
		click.secho("* Installing Claim Request Custom Fields in CRM Task")

		create_custom_fields(
			{
				"CRM Task": [
					{
						"fieldname": "custom_claim_requested_by",
						"fieldtype": "Link",
						"options": "User",
						"label": "Claim Requested By",
						"read_only": 1,
						"search_index": 1,
						"insert_after": "assigned_to",
					},
					{
						"fieldname": "custom_claim_requested_owner",
						"fieldtype": "Link",
						"options": "User",
						"label": "Claim Requested Owner",
						"read_only": 1,
						"search_index": 1,
						"insert_after": "custom_claim_requested_by",
					},
				]
			}
		)

		frappe.clear_cache(doctype="CRM Task")

	if not frappe.get_meta("FCRM Settings").has_field("custom_claim_approver"):
		click.secho("* Installing Claim Approver Custom Field in FCRM Settings")

		create_custom_fields(
			{
				"FCRM Settings": [
					{
						"fieldname": "custom_claim_approver",
						"fieldtype": "Link",
						"options": "User",
						"label": "Claim Request Approver",
						"description": "Who receives Claim Request tasks. Leave blank to fall back to the longest-standing Admin.",
						"insert_after": "enable_sales_hierarchy",
					}
				]
			}
		)

		frappe.clear_cache(doctype="FCRM Settings")
```

- [ ] **Step 2: Write the patch**

Create `crm/patches/v1_0/add_ownership_custom_fields.py`:

```python
"""Install the Claim Request fields and seed the approver.

Kept separate from the flow itself so the fields exist before any code reads them.
"""

import frappe

from crm.install import add_ownership_custom_fields

DEFAULT_APPROVER = "kristina@txbconsulting.com"


def execute():
	add_ownership_custom_fields()
	seed_approver()


def seed_approver():
	"""Point the setting at the person doing this today.

	The ticket requires the permission be tied to the Admin role rather than to one
	person, which is what the setting achieves -- reassigning is a settings edit. Seeding
	it just means the flow works on day one.
	"""
	if frappe.db.get_single_value("FCRM Settings", "custom_claim_approver"):
		return

	if not frappe.db.exists("User", DEFAULT_APPROVER):
		return

	frappe.db.set_single_value("FCRM Settings", "custom_claim_approver", DEFAULT_APPROVER)
```

- [ ] **Step 3: Register it**

Append to `crm/patches.txt`, after `crm.patches.v1_0.disable_convert_dialog_script`:

```
crm.patches.v1_0.add_ownership_custom_fields
```

- [ ] **Step 4: Run the patch and verify the fields exist**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be
bench --site localhost execute crm.patches.v1_0.add_ownership_custom_fields.execute
bench --site localhost execute frappe.client.get_list --kwargs '{"doctype":"Custom Field","filters":{"dt":["in",["CRM Task","FCRM Settings"]],"fieldname":["like","custom_claim%"]},"fields":["dt","fieldname","fieldtype","options","search_index"],"limit_page_length":0}'
```

Expected: three rows — two on `CRM Task` with `search_index: 1`, one on `FCRM Settings`.

- [ ] **Step 5: Verify it is idempotent**

```bash
bench --site localhost execute crm.patches.v1_0.add_ownership_custom_fields.execute
```

Expected: no error, no duplicate fields. Re-run the query from Step 4 and confirm still exactly three rows.

- [ ] **Step 6: Commit**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be/apps/crm
git add crm/install.py crm/patches/v1_0/add_ownership_custom_fields.py crm/patches.txt
git commit -m "$(cat <<'EOF'
feat(txb): add the Claim Request fields and the approver setting

Two indexed fields on CRM Task -- the duplicate check filters on requester
and reference together -- and a Claim Request Approver on FCRM Settings,
seeded to Kristina. The setting is what keeps the permission tied to the
Admin role rather than to one person: reassigning is a settings edit.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DeDbbexZuPyHuzZny3F7Vq
EOF
)"
```

---

## Task 7: The Claim Request endpoint

**Files:**
- Create: `crm/txb/api/ownership.py`
- Create: `crm/txb/test_claim_request.py`

**Interfaces:**
- Consumes: `crm.txb.constants.OWNER_FIELDS`, `crm.txb.permissions.is_admin`, `crm.txb.ownership.owner_field`, and the custom fields from Task 6.
- Produces: `crm.txb.api.ownership.request_claim(doctype, name, requested_owner, reason) -> dict` returning `{"created": bool, "task": str, "message": str}`.

- [ ] **Step 1: Write the failing test**

Create `crm/txb/test_claim_request.py`:

```python
# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-106: asking an Admin for a record, without taking it.

A Claim Request raises exactly one CRM Task and changes nothing else. These tests pin the
two properties that make it safe -- the owner is untouched, and a requester cannot spam
the approver with duplicates for the same record.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.txb.api.ownership import request_claim
from crm.txb.constants import ADMIN_ROLE

SALESMAN = "txb-claim-sales@example.com"
OTHER_SALESMAN = "txb-claim-sales2@example.com"
ADMIN = "txb-claim-admin@example.com"
APPROVER = "txb-claim-approver@example.com"


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


class TestClaimRequest(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(SALESMAN, ["Sales User"])
		ensure_user(OTHER_SALESMAN, ["Sales User"])
		ensure_user(ADMIN, ["Sales User", ADMIN_ROLE])
		ensure_user(APPROVER, ["Sales User", ADMIN_ROLE])
		frappe.db.commit()  # nosemgrep -- roles must outlive per-test rollback

	def setUp(self):
		frappe.db.set_single_value("FCRM Settings", "custom_claim_approver", APPROVER)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def make_deal(self, **kwargs):
		return frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"pipeline_type": "Individual Session",
				"status": "Submitted",
				"deal_owner": OTHER_SALESMAN,
				**kwargs,
			}
		).insert(ignore_permissions=True)

	def claim(self, deal, requested_owner=SALESMAN, reason="I ran the discovery call"):
		return request_claim(
			doctype="CRM Deal",
			name=deal.name,
			requested_owner=requested_owner,
			reason=reason,
		)

	def test_a_request_creates_one_task_for_the_approver(self):
		deal = self.make_deal()

		frappe.set_user(SALESMAN)
		result = self.claim(deal)

		self.assertTrue(result["created"])
		task = frappe.get_doc("CRM Task", result["task"])
		self.assertEqual(task.assigned_to, APPROVER)
		self.assertEqual(task.reference_doctype, "CRM Deal")
		self.assertEqual(task.reference_docname, deal.name)
		self.assertEqual(task.custom_claim_requested_by, SALESMAN)
		self.assertEqual(task.custom_claim_requested_owner, SALESMAN)

	def test_the_task_carries_the_context_an_admin_needs(self):
		deal = self.make_deal()

		frappe.set_user(SALESMAN)
		task = frappe.get_doc("CRM Task", self.claim(deal)["task"])

		for expected in (SALESMAN, deal.name, "Individual Session", "Submitted", "I ran the discovery call"):
			self.assertIn(expected, task.description, expected)

	def test_an_unassigned_record_says_so(self):
		deal = self.make_deal(deal_owner="")

		frappe.set_user(SALESMAN)
		task = frappe.get_doc("CRM Task", self.claim(deal)["task"])

		self.assertIn("Unassigned", task.description)

	def test_the_owner_is_not_changed(self):
		deal = self.make_deal()

		frappe.set_user(SALESMAN)
		self.claim(deal)

		self.assertEqual(
			frappe.db.get_value("CRM Deal", deal.name, "deal_owner"), OTHER_SALESMAN
		)

	def test_a_second_request_from_the_same_person_returns_the_open_one(self):
		deal = self.make_deal()

		frappe.set_user(SALESMAN)
		first = self.claim(deal)
		second = self.claim(deal)

		self.assertFalse(second["created"])
		self.assertEqual(second["task"], first["task"])

	def test_a_different_requester_gets_their_own_task(self):
		deal = self.make_deal()

		frappe.set_user(SALESMAN)
		first = self.claim(deal)

		frappe.set_user(OTHER_SALESMAN)
		second = self.claim(deal, requested_owner=OTHER_SALESMAN)

		self.assertTrue(second["created"])
		self.assertNotEqual(second["task"], first["task"])

	def test_a_closed_request_does_not_block_a_new_one(self):
		deal = self.make_deal()

		frappe.set_user(SALESMAN)
		first = self.claim(deal)

		frappe.set_user("Administrator")
		frappe.db.set_value("CRM Task", first["task"], "status", "Done")

		frappe.set_user(SALESMAN)
		second = self.claim(deal)

		self.assertTrue(second["created"])
		self.assertNotEqual(second["task"], first["task"])

	def test_an_admin_is_refused(self):
		deal = self.make_deal()

		frappe.set_user(ADMIN)
		with self.assertRaises(frappe.ValidationError):
			self.claim(deal)

	def test_an_empty_reason_is_refused(self):
		deal = self.make_deal()

		frappe.set_user(SALESMAN)
		with self.assertRaises(frappe.ValidationError):
			self.claim(deal, reason="   ")

	def test_an_unsupported_doctype_is_refused(self):
		frappe.set_user(SALESMAN)
		with self.assertRaises(frappe.ValidationError):
			request_claim(
				doctype="CRM Organization",
				name="whatever",
				requested_owner=SALESMAN,
				reason="because",
			)

	def test_a_blank_setting_falls_back_to_an_admin(self):
		frappe.db.set_single_value("FCRM Settings", "custom_claim_approver", "")
		deal = self.make_deal()

		frappe.set_user(SALESMAN)
		task = frappe.get_doc("CRM Task", self.claim(deal)["task"])

		self.assertIn(ADMIN_ROLE, frappe.get_roles(task.assigned_to))
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be
bench --site localhost run-tests --module crm.txb.test_claim_request
```

Expected: FAIL at import — `No module named 'crm.txb.api.ownership'`.

- [ ] **Step 3: Write the endpoint**

Create `crm/txb/api/ownership.py`:

```python
"""Asking an Admin for a record.

A non-Admin cannot change an owner (see `crm.txb.ownership`), so this is how they ask.
It raises exactly one CRM Task carrying everything the Admin needs to decide, and touches
nothing else -- no owner change, no conversion. The Admin opens the record, changes the
owner themselves and closes the task.
"""

import frappe
from frappe import _
from frappe.utils import get_url_to_form, now_datetime

from crm.txb.constants import ADMIN_ROLE, OWNER_FIELDS
from crm.txb.ownership import owner_field
from crm.txb.permissions import is_admin

CLOSED_TASK_STATUSES = ("Done", "Canceled")

# Fields worth naming in the task body when the doctype has them.
CONTEXT_FIELDS = ("pipeline_type", "status")


@frappe.whitelist()
def request_claim(doctype: str, name: str, requested_owner: str, reason: str) -> dict:
	"""Raise a Claim Request task, or return the requester's open one.

	Returns {"created": bool, "task": str, "message": str}.
	"""
	if doctype not in OWNER_FIELDS:
		frappe.throw(
			_("{0} records do not have an owner to claim.").format(_(doctype)),
			frappe.ValidationError,
		)

	if is_admin():
		frappe.throw(
			_("You can change the owner directly, so there is nothing to request."),
			frappe.ValidationError,
		)

	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("Say why you are claiming this record."), frappe.ValidationError)

	if not frappe.has_permission(doctype, "read", name):
		frappe.throw(
			_("You do not have access to this record."), frappe.PermissionError
		)

	requester = frappe.session.user

	existing = open_request_for(doctype, name, requester)
	if existing:
		return {
			"created": False,
			"task": existing,
			"message": _("You already have an open request for this record."),
		}

	doc = frappe.get_doc(doctype, name)
	task = frappe.get_doc(
		{
			"doctype": "CRM Task",
			"title": _("Claim Request: {0}").format(record_label(doc)),
			"description": describe(doc, requester, requested_owner, reason),
			"status": "Todo",
			"priority": "Medium",
			"assigned_to": approver(),
			"reference_doctype": doctype,
			"reference_docname": name,
			"custom_claim_requested_by": requester,
			"custom_claim_requested_owner": requested_owner or requester,
		}
	)
	# The requester is asking precisely because they cannot write to this record, so the
	# task is created on their behalf rather than under their permissions.
	task.insert(ignore_permissions=True)

	return {
		"created": True,
		"task": task.name,
		"message": _("Your request has been sent to an Admin."),
	}


def open_request_for(doctype: str, name: str, requester: str) -> str | None:
	"""The requester's still-open task for this record, if any.

	Scoped to the requester deliberately: two salesmen may both want the same deal, and the
	Admin should see both cases. The ticket only forbids one person asking twice.
	"""
	return frappe.db.get_value(
		"CRM Task",
		{
			"reference_doctype": doctype,
			"reference_docname": name,
			"custom_claim_requested_by": requester,
			"status": ("not in", CLOSED_TASK_STATUSES),
		},
		"name",
	)


def approver() -> str:
	"""Who receives Claim Request tasks.

	The setting keeps this tied to the Admin role rather than to one person. A blank
	setting degrades to the longest-standing Admin rather than breaking the request.
	"""
	configured = frappe.db.get_single_value("FCRM Settings", "custom_claim_approver")
	if configured and frappe.db.get_value("User", configured, "enabled"):
		return configured

	fallback = frappe.get_all(
		"Has Role",
		filters={"role": ADMIN_ROLE, "parenttype": "User"},
		pluck="parent",
		order_by="creation asc",
	)
	for user in fallback:
		if user != "Administrator" and frappe.db.get_value("User", user, "enabled"):
			frappe.logger().warning(
				f"[request_claim] No Claim Request Approver configured; falling back to {user}"
			)
			return user

	frappe.throw(
		_("No Claim Request Approver is configured and no Admin user was found."),
		frappe.ValidationError,
	)


def record_label(doc) -> str:
	"""A human name for the record, whatever doctype it is."""
	for fieldname in ("lead_name", "organization", "full_name", "name"):
		if doc.meta.has_field(fieldname) and doc.get(fieldname):
			return doc.get(fieldname)
	return doc.name


def describe(doc, requester: str, requested_owner: str, reason: str) -> str:
	"""Everything the Admin needs to decide, without opening anything else first."""
	field = owner_field(doc.doctype)
	current = (doc.get(field) if field and doc.meta.has_field(field) else "") or _("Unassigned")

	lines = [
		_("<b>{0}</b> is asking to own this record.").format(requester),
		"",
		_("Requester: {0}").format(requester),
		_("Requested owner: {0}").format(requested_owner or requester),
		_("Record: {0} - {1}").format(_(doc.doctype), record_label(doc)),
		_("Link: {0}").format(get_url_to_form(doc.doctype, doc.name)),
		_("Current owner: {0}").format(current),
	]

	for fieldname in CONTEXT_FIELDS:
		if doc.meta.has_field(fieldname) and doc.get(fieldname):
			label = doc.meta.get_label(fieldname)
			lines.append(f"{_(label)}: {doc.get(fieldname)}")

	lines += [
		_("Requested at: {0}").format(now_datetime()),
		"",
		_("Reason: {0}").format(reason),
		"",
		_("The owner has not been changed. Open the record and set it yourself, then close this task."),
	]

	return "<br>".join(lines)
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be
bench --site localhost run-tests --module crm.txb.test_claim_request
```

Expected: PASS, 11 tests.

- [ ] **Step 5: Confirm no other suite regressed**

```bash
bench --site localhost run-tests --module crm.txb.test_ownership
bench --site localhost run-tests --module crm.txb.test_permissions
```

Expected: both pass.

- [ ] **Step 6: Commit**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be/apps/crm
git add crm/txb/api/ownership.py crm/txb/test_claim_request.py
git commit -m "$(cat <<'EOF'
feat(txb): let a non-Admin request ownership of a record

Raises one CRM Task carrying requester, requested owner, the record and a
link to it, the current owner or Unassigned, pipeline and status, the
timestamp and the reason -- and changes nothing else. The Admin opens the
record, sets the owner and closes the task.

Duplicates are scoped to the requester rather than the record: two people
may both want the same deal and the Admin should see both. The ticket only
forbids one person asking twice while their first request is still open.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DeDbbexZuPyHuzZny3F7Vq
EOF
)"
```

---

## Task 8: The owner field renders read-only for non-Admins

**Files:**
- Modify: `crm/txb/ownership.py`
- Modify: `crm/fcrm/doctype/crm_fields_layout/crm_fields_layout.py:80,139`
- Create: `crm/patches/v1_0/disable_owner_form_scripts.py`
- Modify: `crm/patches.txt`
- Modify: `crm/txb/test_ownership.py`

**Interfaces:**
- Consumes: `owner_field`, `is_admin`.
- Produces: `crm.txb.ownership.restrict_owner_field(field, doctype) -> None`.

**Context.** `handle_perm_level_restrictions(field, doctype, parent_doctype=None)` already exists in `crm_fields_layout.py:149` and its whole job is setting `field.read_only = 1`. It is called from `get_fields_layout` (line 80) and `get_sidepanel_sections` (line 139). `Field.vue` binds `:disabled="Boolean(field.read_only)"`. Adding a sibling call at both sites covers the deal, lead and contact pages, their mobile versions, the all-fields modal **and** the Quick Entry creation modals, which go through `get_fields_layout` too.

- [ ] **Step 1: Write the failing test**

Append to `crm/txb/test_ownership.py`:

```python
class TestOwnerFieldRendering(OwnershipTestCase):
	def field(self, fieldname="deal_owner"):
		return frappe._dict({"fieldname": fieldname, "read_only": 0, "permlevel": 0})

	def test_a_salesman_sees_the_owner_read_only(self):
		from crm.txb.ownership import restrict_owner_field

		frappe.set_user(SALESMAN)
		field = self.field()
		restrict_owner_field(field, "CRM Deal")

		self.assertEqual(field.read_only, 1)

	def test_an_admin_may_still_edit_it(self):
		from crm.txb.ownership import restrict_owner_field

		frappe.set_user(ADMIN)
		field = self.field()
		restrict_owner_field(field, "CRM Deal")

		self.assertEqual(field.read_only, 0)

	def test_other_fields_are_untouched(self):
		from crm.txb.ownership import restrict_owner_field

		frappe.set_user(SALESMAN)
		field = self.field("next_step")
		restrict_owner_field(field, "CRM Deal")

		self.assertEqual(field.read_only, 0)

	def test_the_contact_owner_is_covered_too(self):
		from crm.txb.ownership import restrict_owner_field

		frappe.set_user(SALESMAN)
		field = self.field("custom_contact_owner")
		restrict_owner_field(field, "Contact")

		self.assertEqual(field.read_only, 1)

	def test_a_doctype_with_no_owner_concept_is_untouched(self):
		from crm.txb.ownership import restrict_owner_field

		frappe.set_user(SALESMAN)
		field = self.field("organization_name")
		restrict_owner_field(field, "CRM Organization")

		self.assertEqual(field.read_only, 0)
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be
bench --site localhost run-tests --module crm.txb.test_ownership
```

Expected: the five new tests FAIL with `ImportError: cannot import name 'restrict_owner_field'`.

- [ ] **Step 3: Write the helper**

Append to `crm/txb/ownership.py`:

```python
def restrict_owner_field(field, doctype: str, parent_doctype: str | None = None):
	"""Render the owner read-only for anyone who cannot change it.

	Cosmetic only -- `guard_owner_change` is the boundary. This exists so the field does
	not invite an edit the server will refuse, and it replaces a Form Script that injected
	CSS to fake the same effect while enforcing nothing.

	Called beside `handle_perm_level_restrictions`, which is the existing hook for exactly
	this, so one call covers the desktop pages, the mobile pages, the all-fields modal and
	the Quick Entry creation modals.
	"""
	if field.get("fieldname") != owner_field(doctype):
		return

	if is_admin():
		return

	field.read_only = 1
```

- [ ] **Step 4: Wire it into both layout endpoints**

In `crm/fcrm/doctype/crm_fields_layout/crm_fields_layout.py`, add to the imports at the top:

```python
from crm.txb.ownership import restrict_owner_field
```

At line 80, after `handle_perm_level_restrictions(field, doctype, parent_doctype)`:

```python
						restrict_owner_field(field, doctype, parent_doctype)
```

At line 139, after `handle_perm_level_restrictions(field_obj, doctype)`:

```python
					restrict_owner_field(field_obj, doctype)
```

- [ ] **Step 5: Run the tests and confirm they pass**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be
bench --site localhost run-tests --module crm.txb.test_ownership
```

Expected: PASS, 25 tests.

- [ ] **Step 6: Verify the endpoints still answer, as a circular-import check**

`crm_fields_layout` now imports `crm.txb.ownership`, which imports `crm.txb.permissions`, which imports the pipeline action registry. Confirm nothing cycles:

```bash
bench --site localhost execute crm.fcrm.doctype.crm_fields_layout.crm_fields_layout.get_sidepanel_sections --kwargs '{"doctype":"CRM Deal"}' | head -5
```

Expected: JSON layout, no `ImportError`. If it cycles, move the `is_admin` import inside `restrict_owner_field` rather than restructuring either module, and note it in the report.

- [ ] **Step 7: Retire the form script**

Create `crm/patches/v1_0/disable_owner_form_scripts.py`:

```python
"""Retire the Form Scripts the native ownership UI replaces.

`Lead Owner Read-Only` injected CSS to grey out lead_owner for non-Admins and enforced
nothing -- a PATCH to the field succeeded regardless. `restrict_owner_field` does the
rendering and `guard_owner_change` does the enforcing.

`Contact_Create Opportunity` built a Create Deal modal from raw HTML strings, then fired a
second PUT to correct the status the insert had defaulted. That second write is now
refused by TXB-110's transition guard for non-Admins on Workshop and Selling Training,
so the script is not merely redundant -- it is broken. CreateDealFromContactModal.vue
replaces it with a single insert.

Must ship in the same deploy as the Vue changes, or both the native controls and the
injected ones appear.
"""

import frappe

RETIRED_SCRIPTS = (
	"Lead Owner Read-Only",
	"Contact_Create Opportunity",
)


def execute():
	touched = False

	for name in RETIRED_SCRIPTS:
		if not frappe.db.exists("CRM Form Script", name):
			continue

		if not frappe.db.get_value("CRM Form Script", name, "enabled"):
			continue

		frappe.db.set_value("CRM Form Script", name, "enabled", 0)
		touched = True
		frappe.logger().info(f"[disable_owner_form_scripts] Disabled {name}")

	if touched:
		frappe.clear_cache()
```

Append to `crm/patches.txt`:

```
crm.patches.v1_0.disable_owner_form_scripts
```

- [ ] **Step 8: Run the patch and confirm both are off**

```bash
bench --site localhost execute crm.patches.v1_0.disable_owner_form_scripts.execute
bench --site localhost execute frappe.client.get_list --kwargs '{"doctype":"CRM Form Script","filters":{"name":["in",["Lead Owner Read-Only","Contact_Create Opportunity"]]},"fields":["name","enabled"],"limit_page_length":0}'
```

Expected: both rows report `"enabled": 0`. Run the patch a second time and confirm it is a no-op.

- [ ] **Step 9: Commit**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be/apps/crm
git add crm/txb/ownership.py crm/fcrm/doctype/crm_fields_layout/crm_fields_layout.py crm/patches/v1_0/disable_owner_form_scripts.py crm/patches.txt crm/txb/test_ownership.py
git commit -m "$(cat <<'EOF'
feat(txb): render the owner field read-only for non-Admins

Rides on handle_perm_level_restrictions' call sites, whose whole job is
already setting field.read_only -- so one function covers the desktop
pages, the mobile pages, the all-fields modal and the Quick Entry creation
modals, and Field.vue's existing :disabled binding does the rest.

Cosmetic only; guard_owner_change is the boundary. That is the difference
from the Lead Owner Read-Only form script retired here, which injected CSS
and enforced nothing.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DeDbbexZuPyHuzZny3F7Vq
EOF
)"
```

---

## Task 9: Assignment stops rewriting ownership

**Files:**
- Modify: `frontend/src/components/AssignTo.vue`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing. `AssignTo` keeps its `doctype`, `docname` props and its `assignees` model.

**Context.** `saveAssignees` currently writes the owner field in two branches: removing the owner from the assignee list reassigns the owner to "the next available assignee" or clears it, and adding an assignee to a record with **no** owner sets that assignee as owner. The second directly contradicts the ticket — the first person to touch an unowned record must not become its owner — and both are a client-side route around `guard_owner_change`.

- [ ] **Step 1: Confirm the current behaviour is what you think**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be/apps/crm/frontend
grep -n "ownerField\|document.doc\[" src/components/AssignTo.vue
```

Expected: `ownerField` defined around line 42 and read at lines 62, 67-68, 71, 88-89. If the shape differs, report it before editing.

- [ ] **Step 2: Replace the component's script block**

In `frontend/src/components/AssignTo.vue`, replace everything from `const { document } = useDocument(props.doctype, props.docname)` through the end of `saveAssignees` with:

```js
const assignees = defineModel({ type: Array, default: () => [] })

/**
 * Assignment used to write the owner field: adding an assignee to an unowned record made
 * them its owner, and removing the owner from the assignees handed it to "the next
 * available assignee". TXB-106 reserves owner changes for Admins, and both of those were
 * a way around that guard -- the first also being exactly the automatic assignment the
 * ticket forbids. Assignment now only assigns.
 */
async function saveAssignees(
  addedAssignees,
  removedAssignees,
  addAssignees,
  removeAssignees,
) {
  if (removedAssignees.length) await removeAssignees.submit(removedAssignees)
  if (addedAssignees.length) await addAssignees.submit(addedAssignees)
}
```

Then remove the now-unused imports and the `ownerField` computed. The template's `:onUpdate="ownerField && saveAssignees"` becomes:

```html
        :onUpdate="saveAssignees"
```

- [ ] **Step 3: Confirm nothing unused remains**

```bash
grep -n "ownerField\|useDocument\|toast\|computed" src/components/AssignTo.vue
```

Expected: no output. Any import still listed at the top but no longer referenced must be deleted — `useDocument`, `toast` and `computed` all become unused. `Popover` and `MultipleAvatar` stay.

- [ ] **Step 4: Verify the app still builds and the suite passes**

```bash
yarn test:run
yarn build
npx prettier@3.2.5 --check src/components/AssignTo.vue
```

Expected: tests pass, build succeeds, prettier clean. A build failure here means a leftover reference — fix it rather than restoring the import.

- [ ] **Step 5: Commit**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be/apps/crm
git add frontend/src/components/AssignTo.vue
git commit -m "$(cat <<'EOF'
fix(crm): stop assignment rewriting the owner field

Adding an assignee to a record with no owner made them its owner, and
removing the owner from the assignees handed the record to the next
assignee -- both client-side, with no permission check. The first is the
automatic assignment TXB-106 forbids outright; both were a way around the
owner guard. Assignment now only assigns.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DeDbbexZuPyHuzZny3F7Vq
EOF
)"
```

---

## Task 10: The Request Ownership modal

**Files:**
- Create: `frontend/src/components/Modals/RequestOwnershipModal.vue`
- Modify: `frontend/src/pages/Deal.vue`, `frontend/src/pages/MobileDeal.vue`, `frontend/src/pages/Lead.vue`, `frontend/src/pages/Contact.vue`

**Interfaces:**
- Consumes: `crm.txb.api.ownership.request_claim` from Task 7; `transitionsStore().isAdmin()` from `@/stores/transitions`, which already exposes whether the current user holds the Admin role.
- Produces: a component taking `doctype: String`, `docname: String`, `currentOwner: String` and a `v-model` boolean.

- [ ] **Step 1: Write the component**

Create `frontend/src/components/Modals/RequestOwnershipModal.vue`:

```vue
<template>
  <Dialog v-model="show" :options="{ title: __('Request Ownership') }">
    <template #body-content>
      <div class="flex flex-col gap-4">
        <p class="text-p-base text-ink-gray-6">
          {{
            __(
              'The owner will not change now. An Admin reviews your request and decides.',
            )
          }}
        </p>

        <div class="flex flex-col gap-1.5">
          <div class="text-sm text-ink-gray-5">{{ __('Current owner') }}</div>
          <div class="text-base text-ink-gray-9">
            {{ currentOwnerLabel }}
          </div>
        </div>

        <div class="flex flex-col gap-1.5">
          <div class="text-sm text-ink-gray-5">{{ __('Requested owner') }}</div>
          <Link
            class="form-control"
            size="md"
            :value="requestedOwner"
            doctype="User"
            @change="(value) => (requestedOwner = value)"
          />
        </div>

        <div class="flex flex-col gap-1.5">
          <div class="text-sm text-ink-gray-5">
            {{ __('Why are you claiming this?') }}
            <span class="text-ink-red-2">*</span>
          </div>
          <FormControl
            v-model="reason"
            type="textarea"
            :rows="3"
            :placeholder="__('e.g. I ran the discovery call and own the relationship')"
          />
        </div>

        <ErrorMessage :message="error" />
      </div>
    </template>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button :label="__('Cancel')" @click="show = false" />
        <Button
          variant="solid"
          :label="__('Send request')"
          :loading="submitting"
          @click="submit"
        />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import Link from '@/components/Controls/Link.vue'
import { usersStore } from '@/stores/users'
import { sessionStore } from '@/stores/session'
import { Dialog, FormControl, ErrorMessage, call, toast } from 'frappe-ui'
import { ref, computed, watch } from 'vue'

const props = defineProps({
  doctype: { type: String, required: true },
  docname: { type: String, required: true },
  currentOwner: { type: String, default: '' },
})

const show = defineModel({ type: Boolean })

const { getUser } = usersStore()
const { user } = sessionStore()

const requestedOwner = ref(user)
const reason = ref('')
const error = ref('')
const submitting = ref(false)

const currentOwnerLabel = computed(() =>
  props.currentOwner
    ? getUser(props.currentOwner).full_name || props.currentOwner
    : __('Unassigned'),
)

// Reopening the modal after a send must not show the previous request.
watch(show, (open) => {
  if (!open) return
  requestedOwner.value = user
  reason.value = ''
  error.value = ''
})

async function submit() {
  error.value = ''

  if (!reason.value.trim()) {
    error.value = __('Say why you are claiming this record.')
    return
  }

  submitting.value = true
  try {
    const result = await call('crm.txb.api.ownership.request_claim', {
      doctype: props.doctype,
      name: props.docname,
      requested_owner: requestedOwner.value,
      reason: reason.value,
    })
    toast.success(result.message)
    show.value = false
  } catch (err) {
    error.value = err.messages?.[0] || __('Could not send the request.')
  } finally {
    submitting.value = false
  }
}
</script>
```

- [ ] **Step 2: Verify it compiles before wiring it anywhere**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be/apps/crm/frontend
yarn build
```

Expected: build succeeds. An unresolved import here is easier to find now than after four pages reference it.

- [ ] **Step 3: Wire it into the deal page**

In `frontend/src/pages/Deal.vue`, add to the imports:

```js
import RequestOwnershipModal from '@/components/Modals/RequestOwnershipModal.vue'
import { transitionsStore } from '@/stores/transitions'
```

Add near the other refs:

```js
const { isAdmin: userIsAdmin } = transitionsStore()
const showRequestOwnership = ref(false)
```

In the `#right-header` template, immediately before the `<AssignTo ...>` line:

```html
      <Button
        v-if="!userIsAdmin()"
        :label="__('Request Ownership')"
        @click="showRequestOwnership = true"
      />
```

And after the closing `</LayoutHeader>`, alongside the page's other modals:

```html
  <RequestOwnershipModal
    v-if="showRequestOwnership"
    v-model="showRequestOwnership"
    doctype="CRM Deal"
    :docname="dealId"
    :current-owner="doc?.deal_owner"
  />
```

Note: `Deal.vue` already has a local `isAdmin` computed at line 452 derived from `dealActions.data`. Do **not** shadow it — the new binding is deliberately named `userIsAdmin`, and the existing one keeps its meaning.

- [ ] **Step 4: Wire it into the other three pages**

Repeat Step 3 for:

- `frontend/src/pages/MobileDeal.vue` — same doctype, same `dealId`, same `doc?.deal_owner`.
- `frontend/src/pages/Lead.vue` — `doctype="CRM Lead"`, `:docname="leadId"`, `:current-owner="doc?.lead_owner"`.
- `frontend/src/pages/Contact.vue` — `doctype="Contact"`, `:docname="contact.doc.name"`, `:current-owner="contact.doc?.custom_contact_owner"`. Its `#right-header` currently holds only `<CustomActions>`; put the button after it.

For `Lead.vue` and `Contact.vue`, `transitionsStore` is not yet imported — add both imports there too.

- [ ] **Step 5: Verify the build and formatting**

```bash
yarn test:run
yarn build
npx prettier@3.2.5 --check src/components/Modals/RequestOwnershipModal.vue src/pages/Deal.vue src/pages/MobileDeal.vue src/pages/Lead.vue src/pages/Contact.vue
```

Expected: tests pass, build succeeds, prettier clean.

- [ ] **Step 6: Commit**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be/apps/crm
git add frontend/src/components/Modals/RequestOwnershipModal.vue frontend/src/pages/Deal.vue frontend/src/pages/MobileDeal.vue frontend/src/pages/Lead.vue frontend/src/pages/Contact.vue
git commit -m "$(cat <<'EOF'
feat(crm): add Request Ownership to leads, contacts and opportunities

Shown only to non-Admins, who cannot change the owner themselves. The
modal says plainly that nothing changes yet and an Admin decides, and
requires a reason -- that reason is most of what makes the resulting task
actionable.

Reuses transitionsStore().isAdmin() rather than adding a second way to ask
the same question.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DeDbbexZuPyHuzZny3F7Vq
EOF
)"
```

---

## Task 11: Native Contact → Deal

**Files:**
- Create: `frontend/src/components/Modals/CreateDealFromContactModal.vue`
- Modify: `frontend/src/pages/Contact.vue`

**Interfaces:**
- Consumes: `allowedStatusesFor(pipelineType, currentStatus, pipelineStatuses)` from `@/utils/pipelineStatuses`; `selectFieldOptions(field)` from Task 1; `statusesStore().pipelineStatuses`; `getMeta('CRM Deal')`.
- Produces: a component taking `contact: Object` and a `v-model` boolean.

**Context.** The script being replaced inserts a deal and then fires a second `PUT` to correct the status, because the insert defaults it. That second write is a status change on an existing document, and TXB-110's `guard_transition` refuses it for non-Admins — `is_allowed("Workshop", "Submitted", "Workshop submitted")` is `False`. Doing it as one insert avoids the guard entirely, since inserts are exempt.

- [ ] **Step 1: Write the component**

Create `frontend/src/components/Modals/CreateDealFromContactModal.vue`:

```vue
<template>
  <Dialog v-model="show" :options="{ title: __('Create Opportunity') }">
    <template #body-content>
      <div class="flex flex-col gap-4">
        <div class="flex flex-col gap-1.5">
          <div class="text-sm text-ink-gray-5">{{ __('Contact') }}</div>
          <div class="text-base text-ink-gray-9">
            {{ contact.full_name || contact.name }}
          </div>
        </div>

        <div class="flex flex-col gap-1.5">
          <div class="text-sm text-ink-gray-5">{{ __('Organization') }}</div>
          <Link
            class="form-control"
            size="md"
            :value="organization"
            doctype="CRM Organization"
            @change="(value) => (organization = value)"
          />
        </div>

        <div class="flex flex-col gap-1.5">
          <div class="text-sm text-ink-gray-5">
            {{ __('Pipeline Type') }}
            <span class="text-ink-red-2">*</span>
          </div>
          <Select
            v-model="pipelineType"
            class="form-control"
            :options="pipelineTypeOptions"
            :placeholder="__('Select Pipeline Type...')"
          />
        </div>

        <div v-if="pipelineType" class="flex flex-col gap-1.5">
          <div class="text-sm text-ink-gray-5">
            {{ __('Status') }}
            <span class="text-ink-red-2">*</span>
          </div>
          <Select
            v-model="status"
            class="form-control"
            :options="statusOptions"
            :placeholder="__('Select Status...')"
          />
        </div>

        <ErrorMessage :message="error" />
      </div>
    </template>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button :label="__('Cancel')" @click="show = false" />
        <Button
          variant="solid"
          :label="__('Create')"
          :loading="creating"
          @click="create"
        />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import Link from '@/components/Controls/Link.vue'
import { statusesStore } from '@/stores/statuses'
import { getMeta } from '@/stores/meta'
import { allowedStatusesFor } from '@/utils/pipelineStatuses'
import { selectFieldOptions } from '@/utils/selectOptions'
import { Dialog, Select, ErrorMessage, call, toast } from 'frappe-ui'
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  contact: { type: Object, required: true },
})

const show = defineModel({ type: Boolean })

const router = useRouter()
const { pipelineStatuses } = statusesStore()
const { doctypeMeta: dealMeta } = getMeta('CRM Deal')

const organization = ref(props.contact.custom_organization_link || '')
const pipelineType = ref('')
const status = ref('')
const error = ref('')
const creating = ref(false)

const pipelineTypeOptions = computed(() => {
  const field = dealMeta.value?.fields?.find(
    (f) => f.fieldname === 'pipeline_type',
  )
  return selectFieldOptions(field).map(({ value }) => ({
    label: __(value),
    value,
  }))
})

// Same server-owned map the deal page and the convert modal read, rather than the
// private copy the form script carried.
const statusOptions = computed(() =>
  allowedStatusesFor(pipelineType.value, null, pipelineStatuses.data).map(
    (value) => ({ label: __(value), value }),
  ),
)

// The pipeline's first status is the one this pipeline starts in, and pre-selecting it is
// what the script approximated with a hardcoded map.
watch(pipelineType, () => {
  status.value = statusOptions.value[0]?.value || ''
})

async function create() {
  error.value = ''

  if (!pipelineType.value) {
    error.value = __('Please select a pipeline type')
    return
  }

  if (!status.value) {
    error.value = __('Please select a status')
    return
  }

  creating.value = true
  try {
    // One insert, carrying the final status. The script this replaces inserted first and
    // then PUT the corrected status, which TXB-110's transition guard now refuses for
    // non-Admins -- inserts are exempt, later status writes are not.
    const dealName = await call('crm.fcrm.doctype.crm_deal.crm_deal.create_deal', {
      doc: {
        contact: props.contact.name,
        organization: organization.value || undefined,
        pipeline_type: pipelineType.value,
        status: status.value,
      },
    })
    toast.success(__('Opportunity created'))
    show.value = false
    router.push({ name: 'Deal', params: { dealId: dealName } })
  } catch (err) {
    error.value = err.messages?.[0] || __('Could not create the opportunity.')
  } finally {
    creating.value = false
  }
}
</script>
```

- [ ] **Step 2: Confirm `create_deal` accepts this payload**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be
grep -n "def create_deal" -A 25 apps/crm/crm/fcrm/doctype/crm_deal/crm_deal.py
```

Expected: it reads `doc.get("contact")`, builds `contacts`, then `deal.update(doc)` and inserts — so `pipeline_type` and `status` travel through. Confirm `deal_owner` is **not** in the payload: `claim_owner_on_insert` sets it, and sending one would be honoured for an Admin.

- [ ] **Step 3: Wire it into the contact page**

In `frontend/src/pages/Contact.vue`, add the import:

```js
import CreateDealFromContactModal from '@/components/Modals/CreateDealFromContactModal.vue'
```

Add a ref beside the others:

```js
const showCreateDeal = ref(false)
```

In `#right-header`, after `<CustomActions>` and before the Request Ownership button added in Task 10:

```html
      <Button
        :label="__('Create Opportunity')"
        @click="showCreateDeal = true"
      />
```

And with the page's other modals:

```html
  <CreateDealFromContactModal
    v-if="showCreateDeal"
    v-model="showCreateDeal"
    :contact="contact.doc"
  />
```

- [ ] **Step 4: Verify build, tests and formatting**

```bash
cd apps/crm/frontend
yarn test:run
yarn build
npx prettier@3.2.5 --check src/components/Modals/CreateDealFromContactModal.vue src/pages/Contact.vue
```

Expected: tests pass, build succeeds, prettier clean.

- [ ] **Step 5: Commit**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be/apps/crm
git add frontend/src/components/Modals/CreateDealFromContactModal.vue frontend/src/pages/Contact.vue
git commit -m "$(cat <<'EOF'
feat(crm): create an opportunity from a contact natively

Replaces a form script that built its modal from raw HTML strings, carried
a private copy of the pipeline-to-status map, and inserted the deal before
firing a second PUT to correct the status. That second write is a status
change on an existing document, which TXB-110's transition guard refuses
for non-Admins -- so Contact to Opportunity has been broken on Workshop and
Selling Training since that shipped.

One insert carrying the final status avoids the guard entirely, since
inserts are exempt, and the creator owns the result.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DeDbbexZuPyHuzZny3F7Vq
EOF
)"
```

---

## Task 12: Backfill owners from the record creator

**Files:**
- Create: `crm/patches/v1_0/backfill_record_owners.py`
- Modify: `crm/patches.txt`

**Interfaces:**
- Consumes: `crm.txb.constants.OWNER_FIELDS`.
- Produces: nothing importable.

**Context.** On this site, 593 contacts and 4 deals have no owner. **548 of those contacts were created by `Administrator`** (a bulk import) and 8 by `Guest`; only 37 have a real human creator. Handing 548 imported rows to the Administrator account would be inventing ownership, and ownership decides commission — so those two accounts are excluded and the records stay Unassigned, to be claimed through the flow this ticket builds.

- [ ] **Step 1: Record the before state**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be
bench --site localhost execute frappe.client.get_list --kwargs '{"doctype":"Contact","filters":{"custom_contact_owner":["is","not set"]},"fields":["count(name) as n"]}'
bench --site localhost execute frappe.client.get_list --kwargs '{"doctype":"CRM Deal","filters":{"deal_owner":["is","not set"]},"fields":["count(name) as n"]}'
```

Write both numbers into your report. Expected roughly 593 and 4.

- [ ] **Step 2: Write the patch**

Create `crm/patches/v1_0/backfill_record_owners.py`:

```python
"""Give unowned records to whoever created them, where that is a real person.

TXB-106 makes owner changes Admin-only, so anything left unowned needs an Admin to assign
it by hand. Frappe's `owner` column already records the creator, which is the honest answer
for records a person made.

`Administrator` and `Guest` are excluded deliberately. On this data set 548 of the 593
unowned contacts were created by `Administrator` during a bulk import, and handing those to
that account would be inventing ownership -- which here decides commission. They stay
Unassigned and are claimed through the Claim Request flow when someone actually works them.

Writes with `update_modified=False` so a backfill does not reorder activity feeds, and via
`db.set_value` so `guard_owner_change` is not asked to adjudicate a migration.
"""

import frappe

from crm.txb.constants import OWNER_FIELDS

EXCLUDED_CREATORS = ("Administrator", "Guest")


def execute():
	for doctype, field in OWNER_FIELDS.items():
		if not frappe.get_meta(doctype).has_field(field):
			continue

		backfill(doctype, field)


def backfill(doctype: str, field: str):
	candidates = frappe.get_all(
		doctype,
		filters={field: ("is", "not set"), "owner": ("not in", EXCLUDED_CREATORS)},
		fields=["name", "owner"],
	)

	filled = 0
	for record in candidates:
		if not frappe.db.get_value("User", record.owner, "enabled"):
			continue

		frappe.db.set_value(doctype, record.name, field, record.owner, update_modified=False)
		filled += 1

	frappe.logger().info(
		f"[backfill_record_owners] {doctype}: filled {filled} of {len(candidates)} candidates"
	)
```

Append to `crm/patches.txt`:

```
crm.patches.v1_0.backfill_record_owners
```

- [ ] **Step 3: Run it**

```bash
bench --site localhost execute crm.patches.v1_0.backfill_record_owners.execute
```

Expected: no error.

- [ ] **Step 4: Verify it filled the right rows and only those**

```bash
bench --site localhost execute frappe.client.get_list --kwargs '{"doctype":"Contact","filters":{"custom_contact_owner":["is","not set"]},"fields":["owner","count(name) as n"],"group_by":"owner","order_by":"n desc","limit_page_length":0}'
```

Expected: only `Administrator` (~548) and `Guest` (~8) remain. **No real user should appear in this list.** If one does, that user is disabled — confirm with `frappe.client.get_value` on the User before accepting it.

- [ ] **Step 5: Verify it is idempotent**

```bash
bench --site localhost execute crm.patches.v1_0.backfill_record_owners.execute
bench --site localhost execute frappe.client.get_list --kwargs '{"doctype":"Contact","filters":{"custom_contact_owner":["is","not set"]},"fields":["count(name) as n"]}'
```

Expected: the same count as Step 4. A second run has nothing left to match.

- [ ] **Step 6: Commit**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be/apps/crm
git add crm/patches/v1_0/backfill_record_owners.py crm/patches.txt
git commit -m "$(cat <<'EOF'
feat(txb): backfill unowned records from their creator

Frappe's owner column already records who made a record, which is the
honest answer for anything a person created. Administrator and Guest are
excluded: 548 of the 593 unowned contacts came from a bulk import run as
Administrator, and handing those to that account would be inventing
ownership, which here decides commission.

Those stay Unassigned and are claimed through the flow this ticket builds.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DeDbbexZuPyHuzZny3F7Vq
EOF
)"
```

---

## Task 13: Whole-branch verification and documentation

**Files:**
- Modify: `specs/owner-lock-claim-request.md`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing.

- [ ] **Step 1: Run every backend suite**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be
for m in test_ownership test_claim_request test_permissions test_doc_events test_transitions test_registration_token; do
  echo "=== $m ==="
  bench --site localhost run-tests --module crm.txb.$m 2>&1 | tail -4
done
bench --site localhost run-tests --module crm.permissions.test_org_hierarchy 2>&1 | tail -4
```

Expected: every suite OK. Record the test count for each in your report.

- [ ] **Step 2: Run the frontend suite and build**

```bash
cd apps/crm/frontend
yarn test:run 2>&1 | tail -10
yarn build 2>&1 | tail -5
```

Expected: all files pass, build succeeds. Record the total test count.

- [ ] **Step 3: Check formatting across every file this branch touched**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be/apps/crm
git diff --name-only develop...HEAD -- 'frontend/**/*.js' 'frontend/**/*.vue' | xargs npx --yes prettier@3.2.5 --check
```

Expected: all matched files use the correct style.

- [ ] **Step 4: Confirm the retired scripts and the ported code cannot both run**

```bash
cd /home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be
bench --site localhost execute frappe.client.get_list --kwargs '{"doctype":"CRM Form Script","filters":{"name":["in",["Lead Owner Read-Only","Contact_Create Opportunity"]]},"fields":["name","enabled"],"limit_page_length":0}'
bench --site localhost execute frappe.client.get_list --kwargs '{"doctype":"Server Script","filters":{"name":["in",["Auto Assign Lead Owner","Protect Lead Owner"]]},"fields":["name","disabled"],"limit_page_length":0}'
```

Expected: both form scripts `enabled: 0`. The two Server Scripts will still report `disabled: 0` on this site because `bench migrate` has not been run here — that is a **known pre-existing condition**, not something this branch introduces. Note it in the report; do not run `bench migrate`.

- [ ] **Step 5: Verify no owner-writing path was missed**

```bash
cd apps/crm
grep -rn "lead_owner\|deal_owner\|custom_contact_owner" --include=*.vue --include=*.js frontend/src | grep -v "\.test\." | grep -vi "getUser\|label\|key:"
```

Read every remaining hit and confirm each is a *read*, not a write. Any assignment to one of these fields outside `RequestOwnershipModal` is a gap — report it rather than fixing it silently.

- [ ] **Step 6: Update the spec's status**

Append to `specs/owner-lock-claim-request.md`:

```markdown
---

## Implementation status

Delivered on `feature/TXB-106-owner-lock`. All thirteen tasks complete.

Deviation from the design as written: §5 assumed the conversion rule fell out of
`claim_owner_on_insert` alone. It did not for Admins — `LEAD_DEAL_FIELD_MAP` populated
`deal_owner` from the lead, and the hook reads a populated owner as an Admin's deliberate
nomination and returns early, leaving the deal with the lead's owner. The map was emptied,
along with its client-side mirror in `ConvertToDealModal.vue`. Covered by
`test_an_admin_converting_also_owns_the_new_deal`.

Not addressed here, and each wants its own ticket:

- `stores/meta.js` `getFields()` mutates the shared `doctypesMeta` object. Task 1 works
  around it at one call site; any other code reading `options` off raw meta has the same
  latent bug.
- 548 contacts and 8 more created by `Guest` remain unowned by design, and an Admin must
  assign each one that matters.
- `Auto Assign Lead Owner` and `Protect Lead Owner` are still enabled on the localhost
  site because `bench migrate` has not run there. Their Python replacements were deleted
  by this branch, so that site double-ran neither — but the rows must be disabled before
  this reaches an environment where they are live.
```

- [ ] **Step 7: Commit**

```bash
git add specs/owner-lock-claim-request.md
git commit -m "$(cat <<'EOF'
docs(txb): record TXB-106 delivery and what it deliberately left alone

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DeDbbexZuPyHuzZny3F7Vq
EOF
)"
```

- [ ] **Step 8: Report the manual checklist**

Do not perform these — report them for the human to walk. As a **non-Admin** (a Sales User):

1. Open a deal you do not own → the owner field is greyed and cannot be edited.
2. Try `PATCH /api/resource/CRM Deal/<name>` with a new `deal_owner` → refused, message names Request Ownership.
3. Request Ownership on that deal → task appears for the approver, owner unchanged.
4. Request again → told a request is already open, no second task.
5. Add yourself as an assignee to an unowned lead → you do **not** become its owner.
6. Convert a lead owned by someone else → succeeds, and you own the resulting deal and contact.
7. Contact → Create Opportunity on a **Workshop** pipeline → succeeds. This is the PR #17 regression.
8. Open Convert to Deal *after* visiting the Deals list → renders. This is the Task 1 bug's reproduction path.
9. Convert to Deal shows Status exactly once.

As an **Admin**: change an owner on each of a lead, a contact and a deal → all succeed, and no Request Ownership button is shown.

---

## Self-review notes

**Spec coverage.** §1 → Tasks 3, 4, 9, 11. §2 decisions → Tasks 4 (owner-only lock), 6 (approver setting), 12 (backfill), 8 and 11 (script retirement), 6 and 7 (CRM Task storage). §3 → Task 3. §4 → Tasks 3, 4. §5 → Task 5. §6 → Tasks 6, 7, 10. §7 → Task 12. §8 → Tasks 8, 9, 10, 11. §9 → Tasks 1, 2. §10 → every task's test steps plus Task 13.

**Known gap, accepted.** The spec's §10 lists "the Contact → Deal modal picks the correct status per pipeline" as a frontend test. It is covered by `allowedStatusesFor`'s existing suite in `pipelineStatuses.test.js` plus the manual check at Task 13 Step 8 item 7; the component wiring itself is not unit-tested, because testing it would mean mounting a component whose only logic is a `watch` that reads the first element of an already-tested pure function.

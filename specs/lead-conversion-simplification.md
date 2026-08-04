# Lead conversion simplification (TXB-102)

Status: implemented (2026-08-04)
Date: 2026-08-04
Branch: `feature/TXB-102-simplify-lead-conversion`
Jira: [TXB-102](https://mygomtech.atlassian.net/browse/TXB-102)

## Context

Converting a Lead to a Deal (the client calls it an "Opportunity") showed two sections in
`ConvertToDealModal.vue` — **Contact** and **Organization** — each with a *Choose Existing*
toggle. A Lead that already carried an Organization still forced the user to flip the
toggle and re-pick that same Organization by hand, and the Contact section was redundant
entirely: the backend already derives the contact from the Lead's person data. Duplicated
data entry, and a real risk of attaching the contact to the wrong organization.

The backend was doing most of the right thing already. `convert_to_deal`
(`crm/fcrm/doctype/crm_lead/crm_lead.py`) resolves both records even when the modal sends
no `existing_contact` / `existing_organization`:

- `create_contact(None, throw=False)` reuses a Contact matching the lead email, else creates one.
- `create_organization(None)` exact-matches `CRM Organization` by name, else creates one.

Organization dedup is structurally guaranteed: `CRM Organization` autonames
`field:organization_name`, so the name *is* the primary key. At the time of writing, all
1293 leads-with-an-organization matched an existing `CRM Organization` exactly.

### The actual bug

`create_contact` wrote the organization into `Contact.company_name`. That field is hidden
on this site via Property Setter — the panel actually displays the custom field
`custom_organization_link` (Link → CRM Organization). Nothing in the codebase ever set it.

Worse, the enabled server script **"Sync Contact Organization"** (Contact / Before Validate)
is one-way — `custom_organization_link` → `company_name` — and its `else` branch *clears*
`company_name` when the link is empty. So the value `create_contact` wrote was wiped on
insert, leaving the contact with no organization at all.

Measured before the fix: of 16 contacts on lead-converted deals, **13 had no organization
link and 12 had no `company_name`**, despite the Deal and Lead both carrying the right one.

Because that server script propagates link → `company_name`, setting
`custom_organization_link` alone is sufficient.

### Second bug, fixed here

For a Lead with no organization, `create_organization` returned `None` silently and the Deal
was created with a blank `organization`. Since `CRM Deal.title_field` **is** `organization`,
such deals render untitled.

## Decisions (user-confirmed)

- **Contact section removed entirely.** The contact is always derived from the Lead.
- **Organization section is conditional.** Lead has one → read-only line plus a *Change*
  button. Lead has none (or the user clicked Change) → a `Link` picker.
- **Organization is now required** to convert. This is a behaviour change: previously
  org-less leads could convert into untitled deals.
- **The org link is set on newly created contacts only.** A reused contact keeps whatever
  organization it already had, which may deliberately differ from the Lead's.
- **Fix lives in Python**, not in the DB server script, so it is version-controlled,
  unit-testable, and also covers the bulk-convert path.
- **Out of scope:** `CRM Organization.custom_related_contacts` / `custom_related_leads`.
  Both child tables are empty across the entire database and nothing populates them.

## Changes

### Backend — `crm/fcrm/doctype/crm_lead/crm_lead.py`

- New module constant `CONTACT_ORGANIZATION_LINK_FIELD = "custom_organization_link"`.
- `convert_to_deal` now resolves the organization **before** creating the contact, so the
  org docname exists when the contact is built:

  ```python
  organization = lead.create_organization(existing_organization)
  contact = lead.create_contact(existing_contact, False, organization)
  _deal = lead.create_deal(contact, organization, deal)
  ```

- `create_contact(..., organization=None)` sets the link on a newly created contact, guarded
  by `frappe.get_meta("Contact").has_field(...)` so installs without the custom field are
  unaffected and the fork stays mergeable with upstream. Only the **resolved docname** is
  ever assigned — `self.organization` is free text and may not exist as a CRM Organization.

### Frontend — `frontend/src/components/Modals/ConvertToDealModal.vue`

- Contact section, `ContactsIcon`, `existingContact*` refs, their validation, and the
  `existing_contact` payload key all removed.
- Organization section keyed off `leadOrganization = computed(() => props.lead.organization || '')`
  and a `changeOrganization` toggle. The `Switch` is gone; choose-existing and create-new are
  both served by the `Link`'s own `:onCreate`, which opens `OrganizationModal` with
  `{ redirect: false, afterInsert: (doc) => (existingOrganization = doc.name) }` — the same
  pattern already used in `pages/Deal.vue`.
- Convert is blocked with `Please select or create an organization` when neither the lead nor
  the picker supplies one.
- A hint sits under the picker — *"Every opportunity needs an organization. Choose an existing
  one or create a new one."* — shown only when the picker is visible, i.e. when there is
  actually something for the user to do.
- When the lead has an organization and the user does not override it, `existingOrganization`
  stays empty on purpose, so the backend resolves by name — which is what prevents a duplicate.

New translatable strings: `Change`, `Please select or create an organization`, `Every
opportunity needs an organization. Choose an existing one or create a new one.`. There is no
`lt.po` in `crm/locale/`; Lithuanian comes from Frappe Translation records and must be added
there separately.

## Tests

- `crm/fcrm/doctype/crm_lead/test_crm_lead.py`
  - `test_contact_gets_organization_link_on_convert`
  - `test_existing_contact_organization_not_overwritten`
  - `test_convert_reuses_existing_organization`
  - Helper `create_contact_organization_link_field()` creates the custom field, otherwise the
    `has_field` guard would make the assertions pass vacuously.
  - Existing tests were unaffected: `create_contact`'s new parameter is optional.
- `e2e/tests/convert.spec.ts` — added a read-only-organization/no-contact-section spec and an
  org-less-lead validation spec. `LeadPage.openConvertToDealModal()` was extracted so both the
  new specs and the existing `convertToDeal()` share one opener.

## Known follow-ups

- **Backfill:** the 13 already-converted contacts still have no organization link. This fix is
  forward-only; repairing them needs a separate one-off patch.
- **Drift:** 23 contacts have `company_name != custom_organization_link` — pre-existing, not
  caused by conversion.
- `custom_organization_link` is a DB-only custom field, invisible to git. The Python test that
  creates it explicitly is what pins the contract.

### Pipeline type / status duplication (separate ticket)

Pipeline Type and Status in this dialog are **not** part of the Vue component — they are injected
by the `Convert Dialog - Pipeline Type` form script, which globally monkey-patches `window.fetch`
to rewrite the `convert_to_deal` payload and builds its fields as raw HTML through a
`MutationObserver`.

The pipeline-type → status map is duplicated across four form scripts and has already drifted:

| | `Convert Dialog - Pipeline Type` | `Pipeline Status Filter` |
|---|---|---|
| Individual Session | includes `Submitted` | omits `Submitted` |
| Selling Training | `Training proposal submitted` | `Training RFQ received` |

`Training RFQ received` does not exist in `CRM Deal Status` — the filter script offers a dead
status. A fourth copy lives in `CRM Wizard Framework`.

Agreed direction: give `CRM Deal Status` its own `pipeline_type` field (versioned fixture) and
serve the map from one cached whitelisted endpoint that both form scripts and Vue consume. A
plain JS constant was rejected because form scripts are DB-resident evaluated strings that cannot
import from `frontend/src`, and because the status names would still duplicate the doctype.

Known interaction risk: the observer targets `document.querySelector('[role="dialog"]')` — the
first match. The new *Create New* organization flow opens a second dialog, so this needs testing.

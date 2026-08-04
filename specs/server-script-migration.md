# Server Script migration

Status: implemented (2026-08-04)
Date: 2026-08-04
Branch: `feature/port-server-scripts-to-app` (stacked on `fix/registration-token-predictable`)

## Context

Sixteen enabled Server Scripts held business-critical automation as **rows in the
database**: no git history, no code review, no diff between environments, no unit tests,
and execution inside `safe_exec` (no imports, no real logging).

Two concrete failures came from exactly that:

- Lead conversion died with `ServerScriptNotEnabled` on any bench missing
  `server_script_enabled`. Frappe does not skip registered scripts when the feature is
  off -- it **throws** -- so a single missing config flag broke the whole flow.
- The pipeline-type/status map drifted across four Form Scripts, one of them offering
  `Training RFQ received`, a status that does not exist in `CRM Deal Status`.

This migration moves that logic into `crm/txb/`, wired through `hooks.py`.

## What moved where

| Server Script | Now lives in | Hook |
|---|---|---|
| Auto Assign Lead Owner | `txb.doc_events.lead.assign_owner` | CRM Lead `before_insert` |
| Prevent Duplicate Lead | `txb.doc_events.lead.prevent_duplicate` | CRM Lead `before_insert` |
| Protect Lead Owner | `txb.doc_events.lead.protect_owner` | CRM Lead `before_validate` |
| Require Disqualified Reason | `txb.doc_events.lead.default_disqualified_reason` | CRM Lead `before_validate` |
| Sync Contact Organization | `txb.doc_events.contact.sync_organization` | Contact `before_validate` |
| Sync Deal Contact Name | `txb.doc_events.deal.sync_contact_name` | CRM Deal `before_validate` |
| Sync Delivery Coach Name | `txb.doc_events.deal.sync_delivery_coach_name` | CRM Deal `before_validate` |
| Generate Registration Token | `txb.doc_events.deal.generate_registration_token` | CRM Deal `before_validate` |
| Default Call Log Phone Numbers | `txb.doc_events.call_log.default_phone_numbers` | CRM Call Log `before_validate` |
| Update Deal Call Count ×3 | `txb.doc_events.call_log.update_deal_call_count` | CRM Call Log `after_insert` / `on_update` / `after_delete` |
| Weekly VCS Reminder | `txb.tasks.reminders.weekly_vcs_reminder` | cron `0 9 * * 1` |
| Stale Session Run Alert | `txb.tasks.reminders.stale_session_run_alert` | cron `0 9 * * *` |
| Process Registration | `txb.api.registration.process_registration` | whitelisted, `allow_guest` |
| Validate Registration Token | `txb.api.registration.validate_registration_token` | whitelisted, `allow_guest` |

`Deal Won Slack Notification` was already disabled and was not migrated.

The three `Update Deal Call Count` scripts were **byte-identical** (same md5). They are now
one function bound to three events. The count is recomputed by query rather than
incremented, so binding it to every event yields the same correct value.

## Cutover

`crm.patches.v1_0.disable_migrated_server_scripts` must ship in the **same deploy** as the
code. Until the rows are disabled both copies run.

It also rewrites the published `registracija` Web Page. `/api/method/process_registration`
only resolved because a Server Script claimed that flat name; the whitelisted functions
answer on dotted paths instead, so the page would otherwise call endpoints that no longer
exist. The rewrite is idempotent -- an old path never occurs inside its replacement -- and
was dry-run against the real page content before shipping.

## Verification

The app targets Frappe v16 in CI (`server-tests.yml` resolves `develop` -> frappe
`develop`) while **production runs v15** per `docs/deployment-guide.md`. Consequently
`from frappe.tests import IntegrationTestCase` -- used by 25 existing test files -- cannot
import on a production-matching bench, so the suite does not run locally.

Handlers were therefore written against a plain document interface so their logic is
verifiable without the test runner. All were executed directly against Frappe 15.116.0 and
confirmed: call-log placeholders, disqualified default reason, primary-contact selection,
deal names not clobbered, `company_name` following the organization link, and the
call-count handler ignoring non-deal references.

Tests in `crm/txb/test_doc_events.py` and `crm/txb/test_registration_token.py` follow the
repo's `IntegrationTestCase` convention and will run in CI.

## Deferred issues, carried over deliberately

Behaviour was preserved so the migration is provably safe to deploy. These were **not**
fixed and each needs its own ticket.

### Public registration endpoints

`process_registration` is guest-accessible and **writes** -- Contact, Organization, Deal --
then emails the submitter.

- **No dedup.** Every submission inserts a new Contact unconditionally, unlike every other
  path in the app. A likely source of duplicate contacts.
- **No rate limiting.** Nothing throttles repeated submissions.
- **No email validation** beyond a non-empty check.
- **GDPR.** The form collects name, email, phone, birthdate and home address from an
  EU-facing public page with no consent capture and no retention rule.

The predictable-token weakness was **fixed** ahead of this migration: tokens derived from
`now() + doc.name` collapsed to the deal number in lower case
(`CRM-DEAL-2026-00356` -> `crmdeal202600356`), making them enumerable. See
`crm.patches.v1_0.reissue_registration_tokens`.

### Auto Assign Lead Owner

Overwrites `lead_owner` on every insert, discarding any value supplied by the caller. Leads
created via API or integration end up owned by whichever user that integration
authenticates as, and it fights `protect_owner`.

### Hardcoded base URL

`REGISTRATION_BASE_URL` pins `https://crm.txbconsulting.com`, so non-production
environments generate production links. Belongs in site config.

### CI tests v16, production ships v15

A green pipeline does not prove correctness on the Frappe your users run. Tracked
separately; the `main` lane already tests both majors, `develop` does not.

### Pipeline type / status map

Still duplicated across four Form Scripts, still containing a non-existent status. Agreed
direction is to give `CRM Deal Status` its own `pipeline_type` field and serve the map from
one cached endpoint. See `specs/lead-conversion-simplification.md`.

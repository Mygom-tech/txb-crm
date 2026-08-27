# Workshop Registration form (TXB-15)

Public workshop-interest form that creates a **CRM Lead** with `source = Workshop`, embeddable on
the client's site, with a confirmation email to the registrant.

Ticket: https://mygomtech.atlassian.net/browse/TXB-15

## What exists

The CRM forms builder (Settings → Forms; `crm/api/form.py`, `crm/www/crm_form.*`) already
provides: native Web Form → CRM Lead, a public page at `/crm-form/<route>`, an iframe embed with a
domain allow-list, and required/email validation. Neither local nor staging had a single CRM lead
form configured before this work.

## What this adds

### Seeded form — `crm/txb/workshop_form.py`

`seed_workshop_form()` is idempotent and runs from `crm.install.after_install` (new sites) and
`crm.patches.v1_0.seed_workshop_registration_form` (deployed sites). It creates:

- `CRM Lead Source` **Workshop** (was hand-made on live sites, never seeded by code).
- Web Form **Workshop Registration** at `/crm-form/workshop-registration`, saved through
  `crm.api.form.save_form` so it is exactly what the builder would produce and stays fully
  editable there. Fields: first name*, last name, email*, phone, website (the ticket's
  "domain"). Company is omitted: on the live sites `organization` is a Link → CRM Organization
  (Property Setter), which a public form cannot collect — the builder can't map it either.
  Hidden defaults: `status` (builder seed) and `source = Workshop`.
- Notification **Workshop Registration Confirmation**: CRM Lead / New / Email, condition
  `doc.source == "Workshop" and doc.owner == "Guest"` (public submissions run as Guest, so a
  Workshop lead a rep creates by hand never triggers it), recipient = the lead's `email`.

The form ships **unpublished**. The ticket's AC requires the client to confirm the design and
field list first; Publish is one toggle in Settings → Forms. Copy is Lithuanian.

### `source = Workshop` without touching the builder

`enrich_form_submission` applies the form's hidden defaults before stamping the generic
`"Web Form"` source, so a hidden `source` default simply wins. No API or UI change was needed;
the builder shows the hidden Source row and round-trips it.

### URL-tracked origin — `?utm_source=` (all CRM forms)

Precedence on submission: **`utm_source` (valid) > form hidden default > `"Web Form"`**.

- `crm_form.html` posts `utm_source` from the page URL as its own param next to the `accept()`
  payload.
- `crm.api.form._apply_utm_source` reads `frappe.form_dict.utm_source` and resolves it with a
  single PK lookup on `CRM Lead Source` (case-insensitive via the MariaDB `_ci` collation — the
  test `FACEBOOK → Facebook` guards that assumption). Unknown values are ignored — sources are
  never auto-created from a URL.

Example: `/crm-form/workshop-registration?utm_source=facebook` → `source = Facebook`.

**Limitation:** the iframe `src` is static, so UTM params on the _host_ page do not reach the
form. Marketers append `?utm_source=` to the shared link or to the iframe `src`. Inheriting the
host page's UTMs would need a JS-snippet embed — separate ticket.

## Deferred / red flags

- **Preferred date** — no CRM Lead field; dropped until the client confirms the field list.
- **Confirmation email is inert** until an outgoing Email Account exists (TXB-6). Failures are
  swallowed by `Notification.send` (logged in Error Log), so lead creation is unaffected.
  TXB-16 / AUTO-3 (n8n) also plans a confirmation — pick one or registrants get two.
- **No rate limit, dedup, or consent record** on public lead capture (GDPR). Same gap as the
  `registracija` page. `crm.txb.doc_events.lead.prevent_duplicate` rejects an exact
  name+email repeat, which on a public form surfaces as a submission error.
- The existing `registracija` Web Page (token-gated post-sale registration → Deal) is **not**
  this form and is currently broken on staging: its JS calls bare `process_registration` /
  `validate_registration_token`; the repoint patch edited `main_section` but the HTML lives in
  `main_section_html`. Separate fix.

## Verification

- `bench --site <site> run-tests --module crm.txb.test_workshop_form`
- `bench --site <site> run-tests --module crm.tests.test_form_api`
- `bench --site <site> migrate` → form appears (draft) in Settings → Forms with hidden
  Status + Source=Workshop; publish, submit at `/crm-form/workshop-registration` → Lead with
  `source = Workshop`; with `?utm_source=facebook` → `source = Facebook`.

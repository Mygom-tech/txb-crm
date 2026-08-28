# Workshop registration (TXB-15)

A Workshop opportunity issues a public registration link + QR code. Attendees open it, fill in
a short branded form and answer one question; the CRM creates the right records for them and
links everything back to the workshop.

Ticket: https://mygomtech.atlassian.net/browse/TXB-15

## History

The first cut (PRs #76–#78) was a generic **web form → CRM Lead** built on the Settings → Forms
builder. It was retired once the real requirement turned out to be per-workshop attendee
registration with conditional outcomes — Frappe's web-form engine always inserts its target
doctype, so "no deal for a decline" is impossible there. The token-based flow the TXB Server
Scripts already had (`registracija` page + `process_registration`) was the right foundation;
this feature fixes and extends it. Generic improvements from the first cut stay: hidden defaults
applied unconditionally, `?utm_source=` attribution, the `Web Form Field.placeholder` column,
and the branded `crm_form.html` template now parametrised by submit target.

## Link + QR — `crm/txb/api/registration.py`, `crm/www/register.py`

- **Generate link** (`generate_registration_link`, Deal-page panel `WorkshopRegistrationPanel.vue`,
  shown on Workshop opportunities at any status): mints a `secrets` token once
  (`issue_registration_link`, also run automatically at "Workshop set") and stores
  `custom_registration_link = <site>/register?token=…`. Built from `frappe.utils.get_url()`, so
  staging links point at staging. Regenerating never changes an issued link.
- **QR** (`registration_qr`): inline SVG (viewBox only — resolution-independent, embedded on the
  deal page) plus SVG/PNG downloads. PNG is rendered with Pillow at 40 px/module (~1500–2000 px)
  for slide tools that cannot place SVG. `PyQRCode` was already in the bench.
- **Page** `/register?token=…`: `crm/www/register.py` renders the same `crm_form.html` template
  the CRM web forms use (`register.html` just includes it) with a fixed field set — name*,
  last name*, email*, phone, company, job title, comments, **Ar dalyvausite?*** — and posts to
  `process_registration`. Unknown token → 404. `?embed=1` and `?utm_source=` work as on web forms.

## Submission — `process_registration` (guest, token-gated)

| Always                          | Contact reused when the email/phone is already known (`contact_exists`), else created; stamped with `custom_workshop_registration_status` (the answer) and `custom_source_workshop` (the workshop). Organization upserted by name. Confirmation email via template "Registracijos patvirtinimas" (best effort).          |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **I am in**                     | Delivering Coaching opportunity, status **Submitted**, `custom_source_deal` → the workshop, `deal_owner` = workshop owner, `source = Workshop` (or the `utm_source`), primary contact linked; comments become a note on it. A "Workshop registration" note with a link to the new opportunity goes on the workshop deal. |
| **I am in, but have questions** | No deal. A high-priority CRM Task for the workshop's `deal_owner` with the person's details and comments.                                                                                                                                                                                                                |
| **Not for me**                  | No deal, no task. "Workshop registration declined" note on the workshop deal; the Contact carries the answer.                                                                                                                                                                                                            |

The Contact fields are Custom Fields created by `crm.txb.registration_setup` (install +
patch) and shown on the Contact side panel after Designation.

## Migration — `crm.patches.v1_0.workshop_registration_v2`

Installs the Contact fields, re-points every issued `custom_registration_link` at `/register`
on the current site, deletes the retired "Workshop Registration" Web Form and its Notification,
drops the dev-only Deal fields of the interim version, and unpublishes the legacy `registracija`
Web Page. The earlier `seed_workshop_registration_form` patch is kept as a no-op for Patch Log
continuity.

## Deferred / red flags

- **No rate limiting** on `process_registration` (public, writes Contacts/Deals/Tasks) and no
  consent capture or retention rule on personal data (GDPR) — carried over, still open.
- "I am in" lands the attendee's opportunity in **Delivering Coaching / Submitted** directly,
  not in the Workshop sales pipeline; that is the requested behaviour.
- Organization dedupe is exact-name only.
- Confirmation email needs an outgoing Email Account (TXB-6); without one it is logged and skipped.
- Opening the source workshop from a Delivering Coaching deal uses the standard Link field
  (`custom_source_deal`) on the Deal side panel.

## Verification

- `bench --site <site> run-tests --module crm.txb.test_registration` (link/QR, 3 answers,
  contact reuse, utm, page context) and `--module crm.txb.test_registration_token`.
- After deploy + migrate: open a Workshop opportunity → Registration link panel → Generate →
  copy link / download QR; open `/register?token=…` logged out; submit each answer and check
  the Contact, the Delivering Coaching deal / task / note on the workshop.

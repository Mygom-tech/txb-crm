# Contact page: Emails, Comments, Events and Tasks

Narrative and mockup: https://claude.ai/artifact/WzuJuBuNUbRwNCiGPxJEDp

The Contact page shows everything that happened with the person and lets you act on it, using
the same tab bar, timeline and composers as Leads and Deals.

## Decisions

| Question | Decision |
| --- | --- |
| How Lead history reaches the Contact | Read from the archived Lead at load time, as notes and calls already are. Nothing is moved on conversion; no backfill. |
| Actions on the Contact page | Send email, add comment, create event, create task, plus the existing Log a Call and New Note |
| What an "activity note" is | A timeline entry computed from the record (email, event, comment, task). No stored note. |
| Which emails show | Filed on the Contact, its source Leads and its Opportunities, plus any email Frappe links to the Contact by address |
| Tab order | Activity, Emails, Comments, Events, Calls, Tasks, Notes, Deals |
| Email and inactivity | No new work: `human_send_email` already records a Human Contact Event with Direct attribution, which restarts the inactivity cycle |
| Opportunity on emails | Not asked. Emails are filed on the Contact only. |

Out of scope: Data, Attachments and WhatsApp tabs; moving or copying records on conversion.

## Backend (`crm/api/activities.py`)

1. **Contact's own docinfo.** `_read_direct_contact_records` also reads
   `get_docinfo("", "Contact", name)` and emits its comments and communications
   (`communications + automated_messages`) as activities. `get_docinfo` returns communications
   linked through `timeline_links` as well as `reference_*`, which covers emails matched by
   address; verify this on the installed Frappe version with a test.
2. **One reader for comments and communications.** The comment and communication loops are
   duplicated in `_read_deal_activities` and `_read_lead_activities`. Extract
   `_docinfo_activities(docinfo, *, is_lead)` and use it from all three readers.
3. **Contact's own events.** Add `_meeting_events("Contact", name, is_lead=False)` to the direct
   read. Follow-up call Events are already created with `reference_doctype = "Contact"`.
4. **Contact's own tasks.** `get_linked_task_activities(name, doctype="CRM Deal")` gains a
   `doctype` argument (Deal callers unchanged). The direct read adds the Contact's task rows
   (filtered on `reference_doctype = "Contact"`) to `tasks` and their lifecycle events to
   `activities`.
5. Everything from the direct read is tagged by `_tag_contact_source` and deduplicated as today.

## Hooks (`crm/utils/__init__.py`)

`on_comment_insert` and `on_communication_update` accept `Contact` as well as Lead and Deal, so
a new comment or email bumps the Contact's `modified`. The communication-status part stays
Lead/Deal only (Contact has no such field).

## Frontend

1. **`Activities.vue`: drop `readOnly`.** Only the Contact pages pass it. The per-row source
   badge and Lead email lookup key off `doctype === 'Contact'` instead. The header, email and
   comment composers then render on the Contact.
2. **Header actions on a Contact.** The Note and Call entries call the Contact's existing
   `useContactActivityActions` (`newNote`, `logCall`), so they keep the Opportunity picker.
   Email, comment, task and event use the generic paths, which already accept
   `doctype = "Contact"`. Attachment and WhatsApp entries are hidden on a Contact.
3. **`Contact.vue` tabs.** Activity, Emails, Comments, Events, Calls, Tasks, Notes, Deals, with
   counts. `Activities` receives this list and the shared `tabIndex`, and renders Activity,
   Emails, Comments, Events and Tasks. Notes (`ContactNotes`), Calls (`ContactCalls`) and Deals
   (`DealsListView`) stay as they are.
4. **Events tab and event rows.** `EventArea` and `useEvent` take an optional list of event
   names. On a Contact, that list is the Event names in the aggregate feed, so Events from
   source Leads and Opportunities are listed and their timeline rows open.
5. **`MobileContact.vue`** gets the same tabs and drops `readOnly`.

## Tests

- Python (`crm/fcrm/doctype/crm_lead/test_crm_lead.py`, next to the existing
  `get_contact_activities` tests):
  - A comment, email, Event and Task filed on the Contact appear, tagged `source_doctype = "Contact"`.
  - An email to the Contact's address, filed on an unrelated record, appears in the feed.
  - A Lead comment and email appear on the Contact, tagged as Lead pre-conversion.
  - Existing Lead and Deal feeds are unchanged after the docinfo extraction.
- `cd frontend && yarn test:run` stays green.
- Manual check in the browser on a converted Contact, desktop and mobile: send an email, add
  a comment, create an event and a task; each appears in its tab and in Activity.

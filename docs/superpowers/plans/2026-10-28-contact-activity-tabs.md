# Contact Emails, Comments, Events and Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Contact page shows and creates emails, comments, events and tasks, alongside the existing notes and calls, using the Lead/Deal timeline components.

**Architecture:** The Contact activity aggregate (`get_contact_activities`) adds the Contact's own docinfo (comments, communications), Events and Tasks to its direct-source read. The frontend drops `Activities.vue`'s `readOnly` mode and renders the Contact's Activity, Emails, Comments, Events and Tasks tabs through it, leaving Notes, Calls and Deals as they are.

**Tech Stack:** Frappe v15 (Python), Vue 3 + frappe-ui.

**Spec:** `specs/contact-activity-tabs.md`

## Global Constraints

- Tab order: Activity, Emails, Comments, Events, Calls, Tasks, Notes, Deals (mobile: Details first, then the same).
- Nothing moves on Lead conversion; Lead history is read from the archived Lead.
- No Data, Attachments or WhatsApp tab and no attachment/WhatsApp header action on a Contact.
- Note and Call actions on a Contact go through `useContactActivityActions` (`newNote`, `logCall`).
- Lead and Deal feeds must be byte-for-byte unchanged in shape.

## Review Focus

- Email to the Contact's address filed on an unrelated record: shown once, tagged Contact (test in Task 1).
- An email filed on a source Lead that is also timeline-linked to the Contact: shown once (dedup by canonical name; test in Task 1).
- A Contact with no Leads, no Deals and no records: every tab renders its empty state (manual check, Task 4).
- Clicking an Event row sourced from a Lead or Deal opens the event modal (manual check, Task 4).
- Contact name equal to another doc's name (e.g. a Deal named the same): Contact tasks are filtered by `reference_doctype = "Contact"` (Task 1 code).

---

### Task 1: Contact's own comments, emails, events and tasks in the aggregate

**Files:**
- Modify: `crm/api/activities.py` (`_read_deal_activities`, `_read_lead_activities`, `get_contact_activities`, `_read_direct_contact_records`, `get_linked_task_activities`)
- Test: `crm/fcrm/doctype/crm_lead/test_crm_lead.py` (next to the existing `get_contact_activities` tests)

**Interfaces:**
- Produces: `_docinfo_activities(docinfo, *, is_lead: bool) -> list[dict]` (comment + communication activities, existing shape); `get_linked_task_activities(name: str, doctype: str = "CRM Deal") -> list[dict]`; `_read_direct_contact_records(contact) -> (calls, notes, tasks, activities)`.

- [ ] **Step 1: Write failing tests**

`test_contact_feed_includes_contact_owned_comment_email_event_task`: on a fresh Contact insert a `Comment` (reference Contact), a `Communication` (reference Contact, communication_type "Communication"), an `Event` (reference_doctype "Contact"), a `CRM Task` (reference Contact). Call `get_contact_activities(contact)`; assert the activities contain one `activity_type == "comment"`, one `"communication"`, one `"event"` row and a task creation row, each with `source_doctype == "Contact"`; assert `tasks` contains the task name.

`test_contact_feed_includes_email_matched_by_address`: Communication with `reference_doctype` "ToDo" (unrelated) and `recipients` = the Contact's email; assert it appears once among `communication` activities.

`test_contact_feed_keeps_lead_comment_and_email`: converted Lead with a Comment and Communication; assert both appear on the Contact with `source_doctype == "CRM Lead"`.

- [ ] **Step 2: Run, expect FAIL**

Run: `bench --site crm.localhost run-tests --app crm --module crm.fcrm.doctype.crm_lead.test_crm_lead`
Expected: the three new tests fail (no comment/communication/event rows sourced Contact).

- [ ] **Step 3: Implement**

- Extract the comment and communication loops from `_read_deal_activities` / `_read_lead_activities` into `_docinfo_activities`; both readers call it (`is_lead` False/True).
- `get_linked_task_activities` filters on the new `doctype` argument.
- `_read_direct_contact_records` additionally runs `get_docinfo("", "Contact", contact)`, extends activities with `_docinfo_activities(docinfo, is_lead=False)`, `_meeting_events("Contact", contact, is_lead=False)` and `get_linked_task_activities(contact, "Contact")`, and returns the Contact's `CRM Task` rows (`reference_doctype = "Contact"`, same fields as `get_linked_tasks`). `get_contact_activities` tags and appends them.
- If the address-matched test fails because `get_docinfo` does not follow `timeline_links`, add the Communications linked through `Communication Link` (`link_doctype = "Contact"`) explicitly.

- [ ] **Step 4: Run the module plus the Deal/Lead activity tests, expect PASS**

Run: `bench --site crm.localhost run-tests --app crm --module crm.fcrm.doctype.crm_lead.test_crm_lead` and `bench --site crm.localhost run-tests --app crm --module crm.fcrm.doctype.crm_deal.test_crm_deal`

- [ ] **Step 5: Commit** — `feat(crm): Contact activity feed reads its own comments, emails, events and tasks`

### Task 2: Comments and emails on a Contact bump its modified time

**Files:** Modify `crm/utils/__init__.py` (`on_comment_insert`, `on_communication_update`)

- [ ] **Step 1:** `on_comment_insert` accepts `"Contact"`. `on_communication_update` accepts `"Contact"` but only sets `modified` for it (skips `communication_status`).
- [ ] **Step 2:** Re-run Task 1's test module; expect PASS.
- [ ] **Step 3: Commit** — `feat(crm): comments and emails on a Contact update its modified time`

### Task 3: `Activities.vue` works editable on a Contact

**Files:**
- Modify: `frontend/src/components/Activities/Activities.vue`, `ActivityHeader.vue`, `EventArea.vue`, `frontend/src/composables/event.js`

**Interfaces:**
- Produces: `Activities` emits `newNote` and `logCall` when `doctype === 'Contact'`; `readOnly` prop removed. `EventArea` prop `eventNames: Array | null`; `useEvent({..., names})` filters `name in names` when given.

- [ ] **Step 1:** Remove `readOnly`; replace its badge/lookup uses with `const isContact = computed(() => props.doctype === 'Contact')`. Note and task rows open on a Contact.
- [ ] **Step 2:** In `ActivityHeader`, on a Contact: Note and Log a Call options call `emit('newNote')` / `emit('logCall')` (bubbled through `Activities`); Upload Attachment and WhatsApp options are hidden; Notes/Calls title buttons are never reached (those tabs are not rendered by Activities on a Contact).
- [ ] **Step 3:** On a Contact, `EventArea` and `openEventActivity` use the Event names from the aggregate feed (`target.doctype === 'Event'`), passed as `names` to `useEvent`.
- [ ] **Step 4:** `cd frontend && yarn test:run` — expect all pass; `yarn build` — expect success.
- [ ] **Step 5: Commit** — `feat(crm): Activities timeline is editable on a Contact`

### Task 4: Contact pages get the new tabs

**Files:** Modify `frontend/src/pages/Contact.vue`, `frontend/src/pages/MobileContact.vue`

- [ ] **Step 1:** Tabs in the Global Constraints order with counts (Emails/Comments from `versions` by `activity_type`, Events from rows with `target.doctype === 'Event'` and `activity_type === 'event'`, Tasks from `tasks`). Activity, Emails, Comments, Events, Tasks render one `<Activities doctype="Contact" :docname :tabs="tabs" v-model:tabIndex="tabIndex" @newNote="newNote" @logCall="logCall">`. Notes, Calls, Deals unchanged. Remove `activityTabs` and the mobile Activity-tab Log a Call button (the header menu has it).
- [ ] **Step 2:** `yarn test:run`, `yarn build`.
- [ ] **Step 3:** Manual check on the test site, desktop and mobile width: send an email, add a comment, schedule an event, create a task, log a call, add a note on a converted Contact; each shows in its tab and in Activity with the Contact badge; Lead rows keep the Lead badge; an Event row from the Deal opens. Empty Contact shows empty states.
- [ ] **Step 4: Commit** — `feat(crm): Contact page Emails, Comments, Events and Tasks tabs`

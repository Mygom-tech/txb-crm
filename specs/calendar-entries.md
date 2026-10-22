# Calendar entries

Scheduled dates become calendar Events through `crm.txb.meetings` (one keyed Event per meeting flow).

| Date entered                               | Event flow / key                  | Behaviour                                   |
| ------------------------------------------ | --------------------------------- | ------------------------------------------- |
| Set First Coaching Call Date               | `coaching_call_1` on the Deal     | Re-running moves the same Event             |
| Log Coaching Call → Next Coaching Call     | `coaching_call_<n+1>` on the Deal | One Event per call; past calls stay put     |
| Log a Call → Next Call Date (Lead)         | `lead_follow_up` occurrence       | New date cancels the previous open one      |
| Log a Call → Next Call Date (Deal/Contact) | `call_follow_up`                  | One Event per record; rescheduling moves it |

- Deal meeting participants: deal owner, **delivery coach** (`custom_delivery_coach`), linked Contacts.
- Log a Call follow-up Events also add whoever logged the call as a participant.
- The "Next Coaching Call" task now carries `next_call_date` as its due date.
- Date-only fields (review / follow-up due dates) stay tasks, no Events.

## Calendar view

- `crm.api.event.get_calendar_events` backs the Calendar list. System Managers read without Frappe's
  Event owner/participant rule (which only exempts Administrator); everyone else gets `get_list`.
- System Managers get an "All users" button: empty user = no owner/participant filter.
- `crm.api.event.get_calendar_event` backs the event panel so admins can open any listed Event (a `has_permission` hook can't grant this on both Frappe 15 and 16); edits stay owner-only.

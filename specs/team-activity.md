# Team Activity dashboard

Admin-only page (`/team-activity`). Every figure credits whoever recorded the activity, never the
record's current owner. Periods are inclusive calendar dates in the site timezone.

## Metrics (`crm/txb/team_activity_metrics.py`)

| Metric | Counts | Credited to | Period |
| --- | --- | --- | --- |
| Dials | Outgoing CRM Call Logs (any outcome except Other) + Coaching Call Notes (any call status) | caller / note owner | when the call happened |
| Reaches | Dials whose outcome is Completed + every "Log a reach" Note | same | same |
| Meetings set | Booked meeting Events in a stream, cancelled ones excluded | whoever booked it | the **booking** day, not the meeting day |
| Meetings ahead | Open meeting Events in a stream that have not started yet | whoever booked it | ignores the period |

Coaching calls are kept in Dials/Reaches on purpose; their rows carry type `Coaching`, so they can
be dropped later by removing `_coaching_calls` from the two `_union_metric` calls.

## Meeting streams

A meeting counts only when its flow (third segment of `custom_txb_meeting_key`) is mapped:

| Stream | Flows |
| --- | --- |
| Discovery meetings | `lead_discovery`, `training_discovery` |
| Individual sessions | `bap` |
| VCS / Workshops | `vcs_call`, `workshop`, `training_proposal`, `training` |

Lead follow-ups (`lead_follow_up`) are callbacks, not meetings: they count as Dials/Reaches once the
call is logged. A test fails if a known flow is left unmapped.

## API (`crm.txb.api.team_activity`)

- `summary(from_date, to_date, member?)` — each metric with `description`, `ignores_period` and
  `groups` (`key`, `label`, `description`, `value`).
- `members(from_date, to_date)` — every metric per enabled CRM user (the team table).
- `records(metric, from_date, to_date, member?, page?, group?)` — 20 rows per page. Rows that
  reference a Lead, Opportunity or Contact carry the customer's `person` name.

## UI

- Default period: **This week** (Monday–Sunday). Presets: This week, Last week, This month,
  Last 30 days, Custom range.
- Four cards with an explainer each; Meetings set shows the stream split; Meetings ahead is dashed
  and badged "All upcoming".
- Drill-down: stream tabs with counts and an explainer; the person cell links to the record.
- "By team member" table in team mode; selecting a member switches to Person mode.

## Known limits

- "Log a reach" notes are matched by title (`REACH_NOTE_TITLE`), which `log_reach` now writes
  untranslated. A note whose title is edited by hand stops counting; a stored flag would fix that.
- Coaching calls count only when their Note has a stored call status (Log Coaching Call writes it;
  the TXB-247 patch backfilled recognizable legacy notes).
- Meeting flows are matched with `LIKE` on the key, so these queries scan Events. Fine at current
  volume; store the flow in its own indexed field if Events grow large.

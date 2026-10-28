# Dashboard: Today's tasks, Today's events, Referrals this week

Design canvas (option C chosen): https://claude.ai/artifact/EnGHbAZXz8TghawgxRZDFs

Three widgets sit above the existing chart grid on the Dashboard (the home page): Today's tasks
(wide) and Today's events side by side, and a Referrals this week band under them. Each has an
expand button that opens a dialog with the full view.

## Decisions

| Question | Decision |
| --- | --- |
| Placement | Option C. Fixed section above `DashboardGrid`, not grid items: always shown, not removable in Edit mode |
| Who sees everything | System Manager and Sales Manager (same rule as `get_dashboard`). A Sales User sees only their own tasks and events |
| Sales User filter | Applies to all three widgets. Ignored (forced to self) for a Sales User, as in `get_dashboard` |
| Date range filter | Ignored. The widgets are always today / this week |
| "Today" and "this week" | Site time zone. Week starts on System Settings' first day of the week (set it to Monday) |
| Today's tasks | Overdue (open, due before today), Today (open and due today, or assigned today), Completed today |
| Own task | `assigned_to` is the user |
| Own event | The user created it or is a participant (the rule the event reminders already use) |
| Today's event | Not cancelled, and overlaps today (starts today, or started earlier and ends today or later) |
| Referral | A CRM Lead created this week with Referred By (`custom_referred_by`) set |
| Who earns the point | The new lead's `lead_owner`, +1 per referred lead |
| Referral visibility | Leaderboard is shown to everyone. In the expanded list, a lead row appears only if the viewer can read that lead |
| Band size | Top 5 reps, plus the viewer if not in the top 5, plus a "+N more" chip that opens the dialog |

## Backend (`crm/api/dashboard.py`)

Three whitelisted functions, each `@sales_user_only`, each resolving scope the same way
(`_widget_user(user)`: a Sales User is always themselves; a manager gets `user` or `None` for
everyone).

1. **`get_today_tasks(user=None)`** returns `[{name, title, status, priority, due_date,
   assigned_to, reference_doctype, reference_docname, group}]`, `group` one of `overdue`,
   `today`, `done`.
   - Open = status not in Done, Canceled.
   - Assigned today = an open ToDo on that CRM Task created today (`assign_to` writes one per
     assignment).
   - Completed today = status Done and `modified` today. Ceiling: editing an older done task
     today brings it back into the list. Upgrade path: stamp a completed-on time when status
     turns Done.
   - Sorted: overdue by due date, today by due date (no due date last), done by modified.
2. **`get_today_events(user=None)`** returns `[{name, subject, starts_on, ends_on, all_day,
   owner, reference_doctype, reference_docname}]`, sorted by `starts_on`. It reads Events
   directly with SQL, so Private meetings and follow-ups of other users show for a manager, as
   on the record Events tab (#163).
3. **`get_week_referrals(week_of=None, user=None)`** returns `{week_start, week_end, total,
   reps: [{user, points}], leads: [{name, lead_name, referred_by_type, referred_by,
   referred_by_title, lead_owner, creation}]}`.
   - `week_of` is any date in the wanted week, defaulting to today. The dialog's previous/next
     buttons use it.
   - `reps` covers every lead owner with at least one point, highest first.
   - `leads` is filtered by read permission. `total` and `reps` are not.

## Frontend

1. **`frontend/src/utils/todayWidgets.js`** (pure, unit-tested):
   - `groupTasks(tasks)` returns the three groups, in order, with their counts.
   - `bandReps(reps, me, limit = 5)` returns `{shown, more}`: the top `limit`, plus `me` if
     missing, plus how many are left over.
2. **`components/Dashboard/TodayTasks.vue`, `TodayEvents.vue` and `WeekReferrals.vue`**. Each
   is a card with a header (icon, title, count, expand button) and a frappe-ui `Dialog` for the
   expanded view, following the canvas.
   - Tasks: grouped list; the dialog has All/Overdue/Today/Completed tabs and a table.
   - Events: agenda with a Now line; the dialog has a day timeline, one column per person for
     a manager, a single column for a Sales User.
   - Referrals: the band; the dialog has the leaderboard, the readable referred leads and the
     week stepper.
   - A row click opens the record: task → the task modal on its record, event → its record,
     lead → the lead.
3. **`Dashboard.vue`** renders the section between the filters and `DashboardGrid` and passes
   `filters.user`. Refresh reloads it with the charts. It isn't shown in Edit mode.
4. Phone width: the three stack (tasks, events, referrals); dialogs are full width.

Out of scope: the widgets on a mobile-specific page, a referral points history, bonus payout.

## Tests

- Python (`crm/api/test_dashboard_widgets.py`):
  - A task due today, one assigned today and due next week, an overdue one, one done today and
    one canceled: each lands in the right group, and the canceled one is absent.
  - A Sales User sees only tasks assigned to them, even when passing another `user`. A manager
    sees all, or only the filtered user's.
  - Events: a Private event owned by another user shows for a manager but not for a Sales User.
    It shows for the Sales User once they are a participant. A cancelled event and yesterday's
    event are absent. A multi-day event that started yesterday shows.
  - Referrals: two referred leads this week for owner A and one for B give `reps = [A:2, B:1]`.
    A lead without Referred By and a referred lead from last week don't count. `week_of` last
    week counts last week's. A Sales User who can't read B's lead still sees B's point, but not
    the lead row.
- Frontend: `tests/unit/todayWidgets.test.js` for `groupTasks` and `bandReps` (viewer in the top
  5, viewer outside it, fewer than 5 reps).
- Manual: on the test bench, as Administrator and as a Sales User, check each widget, each
  dialog and the Sales User filter.

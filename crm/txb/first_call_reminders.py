"""The first-coaching-call reminder lifecycle for Delivering Coaching Opportunities (TXB-227).

One activation, at most one reminder. The Opportunity carries an app-owned *activation cycle*:
an opaque identity minted the moment it enters Active -- whether it transitions there or the
handover flow creates it Active outright (TXB-265) -- plus the instant that cycle began.
Leaving Active throws the cycle away (and cancels whatever reminder it raised); coming back
records a fresh one, so the deadline restarts rather than resuming. Deals that were already Active when the migration ran carry no cycle at all and are
never reminded about -- only activations this app actually observed.

Exactly-once is a database property, not a heuristic. The reminder Task stores its cycle in
`FIELD_REMINDER_CYCLE`, which is UNIQUE, so a scheduler retry, an overlapping run or two workers
racing the same deal can produce at most one row; the loser recovers from the duplicate key and
moves on. The per-deal row lock makes the common case serial, and the unique index makes the
uncommon case correct. Nothing here matches on task titles and nothing expires.

A reminder is closed, never deleted: Done when the First Coaching Call Date arrives -- by hand,
through the canonical Coaching Call Note seeding path, or through the scheduler's reconciliation
pass, which covers a hook that never fired -- and Canceled when the Opportunity leaves the cycle.
Last Coaching Call Date is not touched anywhere in this module.
"""

import uuid

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from crm.txb.admin_assignment import SETTINGS_DOCTYPE, resolve_admin_task_assignee
from crm.txb.constants import (
	FIELD_ACTIVATION_CYCLE,
	FIELD_ACTIVATION_STARTED_ON,
	FIELD_DELIVERY_COACH,
	FIELD_DELIVERY_COACH_NAME,
	FIELD_FIRST_CALL_DATE,
	FIELD_REMINDER_CYCLE,
	FIRST_CALL_REMINDER_DEFAULT_MINUTES,
	FIRST_CALL_REMINDER_MIN_MINUTES,
	PIPELINE_DELIVERING_COACHING,
	SETTING_FIRST_CALL_REMINDER_MINUTES,
	STATUS_ACTIVE,
)
from crm.txb.pipelines.common import DEAL_DOCTYPE, TASK_BACKLOG, TASK_DOCTYPE, deal_link

TASK_DONE = "Done"
TASK_CANCELED = "Canceled"

# The CRM Task statuses that still mean "somebody has to act on this". Only these are closed;
# a reminder an Admin already finished or cancelled by hand is left exactly as they left it.
OPEN_TASK_STATUSES = ("Backlog", "Todo", "In Progress")

TASK_PRIORITY = "Medium"

# The savepoint one Opportunity's reminder insert rolls back to. Rolling back only to here keeps
# the rest of the scheduled run -- every other Opportunity -- working in the same transaction.
REMINDER_SAVEPOINT = "txb_first_call_reminder"

LOG_TITLE = "first_call_reminders"


def cycle_fields_installed() -> bool:
	"""Whether this site has the Opportunity's activation-cycle metadata yet."""
	meta = frappe.get_meta(DEAL_DOCTYPE)
	return meta.has_field(FIELD_ACTIVATION_CYCLE) and meta.has_field(FIELD_ACTIVATION_STARTED_ON)


def reminder_cycle_field_installed() -> bool:
	"""Whether a CRM Task can carry -- and be deduplicated by -- its activation cycle yet."""
	return frappe.get_meta(TASK_DOCTYPE).has_field(FIELD_REMINDER_CYCLE)


def reminder_delay_minutes() -> int:
	"""How long an Active Opportunity may go without a first call, in minutes.

	Read at evaluation time rather than cached, so an administrator can drop it to a minute to
	watch the flow run and put it back afterwards without a deploy. A missing, unreadable or
	out-of-range setting falls back to the documented default instead of reminding immediately.
	"""
	if not frappe.get_meta(SETTINGS_DOCTYPE).has_field(SETTING_FIRST_CALL_REMINDER_MINUTES):
		return FIRST_CALL_REMINDER_DEFAULT_MINUTES

	try:
		minutes = int(frappe.db.get_single_value(SETTINGS_DOCTYPE, SETTING_FIRST_CALL_REMINDER_MINUTES) or 0)
	except (TypeError, ValueError):
		return FIRST_CALL_REMINDER_DEFAULT_MINUTES

	if minutes < FIRST_CALL_REMINDER_MIN_MINUTES:
		return FIRST_CALL_REMINDER_DEFAULT_MINUTES
	return minutes


def validate_reminder_delay(doc, method=None):
	"""Refuse a delay below the documented minimum; blank keeps the default."""
	value = doc.get(SETTING_FIRST_CALL_REMINDER_MINUTES)
	if value in (None, ""):
		return
	if int(value) < FIRST_CALL_REMINDER_MIN_MINUTES:
		frappe.throw(
			frappe._("The first coaching call reminder delay must be at least {0} minute(s).").format(
				FIRST_CALL_REMINDER_MIN_MINUTES
			),
			frappe.ValidationError,
		)


def new_cycle_id(deal_name: str) -> str:
	"""An opaque, unrepeatable identity for one activation of one Opportunity."""
	return f"{deal_name}:{uuid.uuid4().hex}"


def record_activation_cycle(deal, method=None):
	"""Open or close the Opportunity's activation cycle as this save moves its status (TXB-227).

	Bound to `validate`, so the cycle is written by the very save that changes the status: the
	two can never disagree, and a save that fails takes the cycle with it. An insert straight
	into Active counts as an activation (TXB-265): the handover flow creates the delivery deal
	already Active, and that is an activation this app observed, unlike the rows sitting in
	Active when the migration ran, which it never saw and still never reminds about. Otherwise
	only a transition counts: a deal saved while already Active keeps the cycle and the deadline
	it already had, so an unrelated edit never resets the clock.

	Closing only clears the fields; the reminder that cycle may have raised is cancelled after
	the save commits, in `settle_activation_cycle`.
	"""
	if not cycle_fields_installed():
		return

	is_active = deal.pipeline_type == PIPELINE_DELIVERING_COACHING and deal.status == STATUS_ACTIVE

	if deal.is_new():
		# There is no prior status to compare against; the deal either arrives Active, which
		# opens its first cycle here, or arrives without one. Anything a copied document
		# carried in these app-owned fields is not this deal's cycle and is dropped.
		deal.set(FIELD_ACTIVATION_CYCLE, new_cycle_id(deal.name) if is_active else None)
		deal.set(FIELD_ACTIVATION_STARTED_ON, now_datetime() if is_active else None)
		return

	previous = _previous_status(deal)

	if is_active and previous != STATUS_ACTIVE:
		deal.set(FIELD_ACTIVATION_CYCLE, new_cycle_id(deal.name))
		deal.set(FIELD_ACTIVATION_STARTED_ON, now_datetime())
	elif not is_active and deal.get(FIELD_ACTIVATION_CYCLE):
		deal.set(FIELD_ACTIVATION_CYCLE, None)
		deal.set(FIELD_ACTIVATION_STARTED_ON, None)


def settle_activation_cycle(deal, method=None):
	"""Cancel the reminder of a cycle this save ended or replaced (TXB-227).

	Bound to `on_update`, after the new cycle state is committed, and driven by the difference
	between the stored document and the saved one rather than by the status: whatever ended the
	old cycle -- leaving Active, re-entering it, or moving off the pipeline entirely -- the
	reminder it raised stops being somebody's job.
	"""
	if not cycle_fields_installed():
		return

	before = deal.get_doc_before_save()
	if before is None:
		return

	previous_cycle = before.get(FIELD_ACTIVATION_CYCLE)
	if previous_cycle and previous_cycle != deal.get(FIELD_ACTIVATION_CYCLE):
		close_cycle_reminder(previous_cycle, TASK_CANCELED)


def complete_reminder_on_first_call_date(deal, method=None):
	"""Mark the current cycle's open reminder Done once the First Coaching Call Date arrives.

	Bound to `on_update`, so a date typed on the Opportunity closes the reminder immediately
	rather than waiting for the scheduler to reconcile it. Only the empty-to-filled edge acts:
	re-saving a deal that already had a date never reopens the question.
	"""
	if not cycle_fields_installed() or not deal.get(FIELD_FIRST_CALL_DATE):
		return

	before = deal.get_doc_before_save()
	if before is not None and before.get(FIELD_FIRST_CALL_DATE):
		return

	close_cycle_reminder(deal.get(FIELD_ACTIVATION_CYCLE), TASK_DONE)


def complete_first_call_reminder(deal_name: str) -> int:
	"""Close the Opportunity's current cycle reminder as Done, by deal name.

	The entry point for the writers that never save the Opportunity document -- the canonical
	Coaching Call Note seeding paths write the date straight to the row with
	`update_modified=False`, so no document event fires for them.
	"""
	if not deal_name or not cycle_fields_installed():
		return 0
	return close_cycle_reminder(
		frappe.db.get_value(DEAL_DOCTYPE, deal_name, FIELD_ACTIVATION_CYCLE), TASK_DONE
	)


def close_cycle_reminder(cycle: str | None, status: str) -> int:
	"""Move every still-open reminder belonging to `cycle` to `status`. Returns how many moved."""
	if not cycle or not reminder_cycle_field_installed():
		return 0

	names = frappe.get_all(
		TASK_DOCTYPE,
		filters={FIELD_REMINDER_CYCLE: cycle, "status": ("in", OPEN_TASK_STATUSES)},
		pluck="name",
	)
	for name in names:
		frappe.db.set_value(TASK_DOCTYPE, name, "status", status)
	return len(names)


def run_first_call_reminders() -> None:
	"""The scheduled pass: reconcile what the hooks missed, then remind what is overdue.

	Runs often enough that a one-minute configured delay is actually observable. Reconciliation
	goes first so a deal whose date arrived through a path that never reached a hook is closed
	before it can be considered overdue again.
	"""
	if not cycle_fields_installed() or not reminder_cycle_field_installed():
		return

	reconcile_open_reminders()

	deadline = add_to_date(now_datetime(), minutes=-reminder_delay_minutes())
	overdue = frappe.get_all(
		DEAL_DOCTYPE,
		filters={
			"pipeline_type": PIPELINE_DELIVERING_COACHING,
			"status": STATUS_ACTIVE,
			FIELD_ACTIVATION_CYCLE: ("is", "set"),
			FIELD_ACTIVATION_STARTED_ON: ("<=", deadline),
			FIELD_FIRST_CALL_DATE: ("is", "not set"),
		},
		pluck="name",
	)

	for deal_name in overdue:
		remind_once(deal_name, deadline)


def reconcile_open_reminders() -> None:
	"""Close open reminders whose cycle no longer warrants one, whatever closed it.

	The safety net under every document event: a First Coaching Call Date written by an import,
	a patch or a path whose hook did not fire still closes its reminder here, and a reminder
	left behind by an activation cycle that ended without its `on_update` running is cancelled.
	"""
	open_reminders = frappe.get_all(
		TASK_DOCTYPE,
		filters={FIELD_REMINDER_CYCLE: ("is", "set"), "status": ("in", OPEN_TASK_STATUSES)},
		fields=["name", "reference_docname", FIELD_REMINDER_CYCLE],
	)

	for task in open_reminders:
		deal = frappe.db.get_value(
			DEAL_DOCTYPE,
			task.reference_docname,
			["status", FIELD_ACTIVATION_CYCLE, FIELD_FIRST_CALL_DATE],
			as_dict=True,
		)
		if not deal:
			continue

		if deal.get(FIELD_ACTIVATION_CYCLE) != task.get(FIELD_REMINDER_CYCLE) or deal.get("status") != STATUS_ACTIVE:
			frappe.db.set_value(TASK_DOCTYPE, task.name, "status", TASK_CANCELED)
		elif deal.get(FIELD_FIRST_CALL_DATE):
			frappe.db.set_value(TASK_DOCTYPE, task.name, "status", TASK_DONE)


def remind_once(deal_name: str, deadline) -> str | None:
	"""Raise this Opportunity's one reminder for its current activation cycle, or nothing.

	Every condition is re-read under the Opportunity's row lock, because the list that led here
	was built before the lock was taken: the deal may since have left Active, started a new
	cycle or had its first call logged. A failure -- no eligible Admin, a refused insert -- is
	logged against this Opportunity and rolled back to the savepoint, which leaves the cycle
	exactly as it was and therefore retryable on the next run. It never aborts the pass.
	"""
	frappe.db.savepoint(REMINDER_SAVEPOINT)
	try:
		deal = frappe.db.get_value(
			DEAL_DOCTYPE,
			deal_name,
			[
				"name",
				"status",
				"pipeline_type",
				"organization",
				FIELD_ACTIVATION_CYCLE,
				FIELD_ACTIVATION_STARTED_ON,
				FIELD_FIRST_CALL_DATE,
				FIELD_DELIVERY_COACH,
				FIELD_DELIVERY_COACH_NAME,
			],
			as_dict=True,
			for_update=True,
		)
		if not deal or not _is_overdue_cycle(deal, deadline):
			return None

		cycle = deal.get(FIELD_ACTIVATION_CYCLE)
		if _cycle_has_task(cycle):
			return None

		return _insert_reminder(deal, cycle)
	except frappe.UniqueValidationError:
		# A concurrent run won the race for this cycle. Its task is the one reminder this
		# activation gets; roll our failed insert back so the rest of the pass stays usable.
		frappe.db.rollback(save_point=REMINDER_SAVEPOINT)
		return None
	except Exception as e:
		frappe.db.rollback(save_point=REMINDER_SAVEPOINT)
		frappe.log_error(
			title=LOG_TITLE,
			message=f"Failed to create the first coaching call reminder for {deal_name}. {e}",
		)
		return None


def reminder_message(admin_name: str, coach: str, opportunity: str) -> str:
	"""The approved Lithuanian reminder the Admin reads in their task list."""
	return f"{admin_name}, {coach} treneris nesusetino call dėl {opportunity}, susisiek."


def _insert_reminder(deal, cycle: str) -> str:
	assignee = resolve_admin_task_assignee()
	message = reminder_message(
		frappe.db.get_value("User", assignee, "first_name") or assignee,
		_coach_label(deal),
		_opportunity_label(deal),
	)

	task = frappe.get_doc(
		{
			"doctype": TASK_DOCTYPE,
			"title": message,
			"description": f"{message} - {deal_link(deal.name)}",
			"assigned_to": assignee,
			"priority": TASK_PRIORITY,
			"status": TASK_BACKLOG,
			"reference_doctype": DEAL_DOCTYPE,
			"reference_docname": deal.name,
			FIELD_REMINDER_CYCLE: cycle,
		}
	).insert(ignore_permissions=True)
	return task.name


def _is_overdue_cycle(deal, deadline) -> bool:
	"""Whether the locked Opportunity still has an Active cycle past its deadline and no date."""
	if deal.get("pipeline_type") != PIPELINE_DELIVERING_COACHING or deal.get("status") != STATUS_ACTIVE:
		return False
	if deal.get(FIELD_FIRST_CALL_DATE):
		return False

	started = deal.get(FIELD_ACTIVATION_STARTED_ON)
	if not deal.get(FIELD_ACTIVATION_CYCLE) or not started:
		return False
	return get_datetime(started) <= get_datetime(deadline)


def _cycle_has_task(cycle: str) -> bool:
	"""Whether this activation cycle already carries a reminder, open or closed.

	Closed counts: a reminder an Admin finished, or one cancelled when the deal left Active, is
	still the single reminder that cycle was owed. Only a *new* activation earns another.
	"""
	return bool(frappe.db.exists(TASK_DOCTYPE, {FIELD_REMINDER_CYCLE: cycle}))


def _coach_label(deal) -> str:
	return deal.get(FIELD_DELIVERY_COACH_NAME) or deal.get(FIELD_DELIVERY_COACH) or "Nepriskirtas"


def _opportunity_label(deal) -> str:
	return deal.get("organization") or deal.name


def _previous_status(deal) -> str | None:
	"""The status this save is moving away from, read from the document or the row."""
	before = deal.get_doc_before_save()
	if before is not None:
		return before.status
	return frappe.db.get_value(DEAL_DOCTYPE, deal.name, "status")

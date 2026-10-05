"""One reminder Task per Contact inactivity cycle (TXB-280).

Once `contact_inactivity.evaluate_due` marks a cycle Due, the Contact's owner gets exactly one CRM
Task -- with the normal assignment ToDo -- and the cycle becomes Reminded, recording the Task in
`reminder_task`. Exactly-once rests on the cycle, not on the Task: a Task is issued only while the
cycle, re-read under its Contact's row lock, is still Due without one. An overlapping run therefore
finds it Reminded, and a deleted Task is never replaced.

A Due cycle whose Contact has no owner, or whose owner's user is disabled, gets no Task; it records
why in `exception` and stays Due, so a later run reminds the owner once there is an enabled one.
Nothing here writes to a Contact: ownership changes only by an Admin's hand.

After that the Task follows the Contact: an owner change moves the open Task and its ToDo to the
new enabled owner, Done or Canceled Settles the cycle, and newer human contact Closes the cycle,
opens the next one and cancels the Task if it is still open.
"""

import frappe
from frappe.utils import getdate, now_datetime

from crm.txb.constants import OWNER_FIELDS
from crm.txb.contact_attribution import attribute_event
from crm.txb.contact_inactivity import (
	CONTACT_DOCTYPE,
	CYCLE_DOCTYPE,
	STATUS_DUE,
	evaluate_due,
	mark_reminded,
	on_qualifying_contact,
	settle,
)
from crm.txb.first_call_reminders import OPEN_TASK_STATUSES, TASK_CANCELED, TASK_DONE
from crm.txb.pipelines.common import TASK_BACKLOG, TASK_DOCTYPE

OWNER_FIELD = OWNER_FIELDS[CONTACT_DOCTYPE]

EXCEPTION_UNOWNED = "Unowned"
EXCEPTION_DISABLED_OWNER = "Disabled Owner"

TASK_PRIORITY = "Medium"

# One cycle's reminder rolls back to here on failure, so the rest of the pass keeps working.
REMIND_SAVEPOINT = "txb_contact_inactivity_reminder"

LOG_TITLE = "contact_inactivity_reminders"


def run_contact_inactivity_reminders(now=None) -> None:
	"""The scheduled pass: mark overdue cycles Due, then remind about every Due cycle.

	Every Due cycle, not only those this run marked: one left Due by an owner exception or a
	failed run is retried until it is reminded.
	"""
	evaluate_due(now)
	for cycle in frappe.get_all(
		CYCLE_DOCTYPE, filters={"status": STATUS_DUE}, pluck="name", order_by="due_at asc"
	):
		remind(cycle)


def remind(cycle: str) -> str | None:
	"""Issue the Due cycle's one reminder Task, or record why it cannot; return the Task.

	The Contact and the cycle are locked and re-read first, so of two runs racing one cycle the
	second finds it Reminded and does nothing. A failure is logged and rolled back to the
	savepoint, leaving the cycle Due for the next run.
	"""
	frappe.db.savepoint(REMIND_SAVEPOINT)
	try:
		contact = frappe.db.get_value(CYCLE_DOCTYPE, cycle, "contact")
		locked = _lock_contact(contact)
		if not locked:
			# The Contact is gone; nobody is left to remind.
			settle(cycle)
			return None

		row = frappe.db.get_value(
			CYCLE_DOCTYPE,
			cycle,
			["status", "reminder_task", "due_at", "exception"],
			as_dict=True,
			for_update=True,
		)
		if row.status != STATUS_DUE or row.reminder_task:
			return None

		owner = locked.get(OWNER_FIELD)
		if not owner:
			exception = EXCEPTION_UNOWNED
		elif not _is_enabled(owner):
			exception = EXCEPTION_DISABLED_OWNER
		else:
			exception = None
		if exception:
			if row.exception != exception:
				frappe.db.set_value(
					CYCLE_DOCTYPE, cycle, {"exception": exception, "exception_at": now_datetime()}
				)
			return None

		task = _insert_task(contact, owner, row.due_at)
		frappe.db.set_value(CYCLE_DOCTYPE, cycle, "reminder_task", task)
		mark_reminded(cycle)
		return task
	except Exception:
		frappe.db.rollback(save_point=REMIND_SAVEPOINT)
		frappe.log_error(
			title=LOG_TITLE,
			message=f"Failed to remind about inactivity cycle {cycle}.\n{frappe.get_traceback()}",
		)
		return None


def move_reminder_to_new_owner(doc, method=None):
	"""Hand the Contact's open reminder to its new owner (Contact on_update).

	CRMTask.validate moves the ToDo with it. An owner who is unset or disabled leaves the Task
	where it is.
	"""
	if not doc.meta.has_field(OWNER_FIELD) or not doc.has_value_changed(OWNER_FIELD):
		return
	owner = doc.get(OWNER_FIELD)
	task = _open_reminder(doc.name)
	if not task or not _is_enabled(owner):
		return

	task = frappe.get_doc(TASK_DOCTYPE, task)
	if task.assigned_to != owner:
		task.assigned_to = owner
		task.save(ignore_permissions=True)


def settle_on_task_close(doc, method=None):
	"""Settle the cycle whose reminder was just marked Done or Canceled (CRM Task on_update)."""
	if doc.status not in (TASK_DONE, TASK_CANCELED) or not doc.has_value_changed("status"):
		return
	cycle = frappe.db.get_value(CYCLE_DOCTYPE, {"reminder_task": doc.name}, "name")
	if cycle:
		settle(cycle)


def close_on_human_contact(doc, method=None):
	"""Restart the cycle of every Contact this human contact was with (CRM Human Contact Event
	on_update), cancelling the reminder still open on the cycle it closes.

	A voided event is attributed to no one, so it restarts nothing.
	"""
	for contact in attribute_event(doc.name):
		_lock_contact(contact)
		task = _open_reminder(contact)
		if on_qualifying_contact(contact, doc.occurred_at) and task:
			frappe.db.set_value(TASK_DOCTYPE, task, "status", TASK_CANCELED)


def _lock_contact(contact: str | None):
	"""Row-lock the Contact, as the engine does, and return it with its owner; None if missing."""
	if not contact:
		return None
	fields = ["name", OWNER_FIELD] if frappe.get_meta(CONTACT_DOCTYPE).has_field(OWNER_FIELD) else ["name"]
	return frappe.db.get_value(CONTACT_DOCTYPE, contact, fields, as_dict=True, for_update=True)


def _open_reminder(contact: str) -> str | None:
	"""The reminder Task of the Contact's live cycle, if it still exists and is open."""
	task = frappe.db.get_value(CYCLE_DOCTYPE, {"live_key": contact}, "reminder_task")
	if task and frappe.db.get_value(TASK_DOCTYPE, task, "status") in OPEN_TASK_STATUSES:
		return task
	return None


def _is_enabled(user: str | None) -> bool:
	return bool(user and frappe.db.get_value("User", user, "enabled"))


def _insert_task(contact: str, owner: str, due_at) -> str:
	name = frappe.db.get_value(CONTACT_DOCTYPE, contact, "full_name") or contact
	return (
		frappe.get_doc(
			{
				"doctype": TASK_DOCTYPE,
				"title": f"Reconnect with {name}",
				"description": (
					f"There has been no human contact with {name} for the configured inactivity "
					"interval. Reach out to keep the relationship going."
				),
				"assigned_to": owner,
				"priority": TASK_PRIORITY,
				"status": TASK_BACKLOG,
				# due_at is already site time, so its date is the site-time date.
				"due_date": getdate(due_at),
				"reference_doctype": CONTACT_DOCTYPE,
				"reference_docname": contact,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)

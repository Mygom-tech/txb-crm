"""Delivery Coach assignment notifications: recover lost jobs, retry definite failures (TXB-290).

Both re-queue the hardened worker (`process_assignment_notification`) for one channel and never
contact Slack or SMTP themselves:

* `sweep_stale_assignment_notifications` (every five minutes) takes a bounded batch of channels
  still Pending STALE_AFTER_MINUTES after the event was recorded, last claimed and last
  recovered -- the job was lost at enqueue, or the worker died before recording an outcome.
  Each row is locked and rechecked, its channel stamped recovered and committed with the
  channel's deterministic job, so a concurrent sweep finds it fresh and queues nothing more.
  Sent, Skipped and Uncertain channels are never touched, and a Failed one waits for an operator.
* `retry_assignment_notification_channel` lets a System Manager put one Failed channel back to
  Pending and re-queue it. Sent and Skipped are final, Pending is already on its way and
  Uncertain may have been delivered, so each is refused.
* `get_assignment_notification_status` gives a System Manager the event's routing and each
  channel's sanitized state.

Nothing here writes the captured mode, recipients or assignment generation; the worker rechecks
the generation, pipeline and Disabled stop as on any run.
"""

import frappe
from frappe import _
from frappe.query_builder.functions import Coalesce
from frappe.utils import add_to_date, cint, now_datetime

from crm.txb.delivery_coach_assignment import LOG_TITLE, enqueue_assignment_notification
from crm.txb.delivery_coach_notifications import CHANNELS, LEDGER_DOCTYPE, STATUS_FAILED, STATUS_PENDING
from crm.txb.permissions import is_system_manager

# Sweep tuning. The scheduler runs it every five minutes (hooks.py).
STALE_AFTER_MINUTES = 10
SWEEP_BATCH_SIZE = 50
RECOVERY_SAVEPOINT = "coach_assignment_recovery"


def sweep_stale_assignment_notifications() -> list[tuple[str, str]]:
	"""Re-queue at most SWEEP_BATCH_SIZE stale Pending channels; returns the (event, channel) recovered."""
	cutoff = add_to_date(now_datetime(), minutes=-STALE_AFTER_MINUTES)
	return [pair for pair in _stale_channels(cutoff) if _recover(*pair, cutoff)]


def _stale_channels(cutoff, event_key: str | None = None) -> list[tuple[str, str]]:
	"""(event, channel) pairs Pending with no record, claim or recovery since `cutoff`, oldest first."""
	ledger = frappe.qb.DocType(LEDGER_DOCTYPE)
	found = []
	for channel in CHANNELS:
		query = (
			frappe.qb.from_(ledger)
			.select(ledger.name, ledger.creation)
			.where(ledger[f"{channel}_status"] == STATUS_PENDING)
			.where(ledger.creation < cutoff)
			.where(Coalesce(ledger[f"{channel}_claimed_at"], ledger.creation) < cutoff)
			.where(Coalesce(ledger[f"{channel}_recovered_at"], ledger.creation) < cutoff)
			.orderby(ledger.creation)
			.limit(SWEEP_BATCH_SIZE)
		)
		if event_key:
			query = query.where(ledger.name == event_key)
		found += [(row.creation, row.name, channel) for row in query.run(as_dict=True)]
	return [(name, channel) for _creation, name, channel in sorted(found)[:SWEEP_BATCH_SIZE]]


def _recover(event_key: str, channel: str, cutoff) -> bool:
	# Locked until the commit below, so a concurrent sweep (or a running worker) is waited for,
	# and the recheck then finds the channel freshly recovered or already decided.
	if not frappe.db.get_value(LEDGER_DOCTYPE, event_key, "name", for_update=True):
		return False
	if (event_key, channel) not in _stale_channels(cutoff, event_key):
		frappe.db.commit()  # nosemgrep -- release the lock
		return False
	frappe.db.savepoint(RECOVERY_SAVEPOINT)
	try:
		frappe.db.set_value(
			LEDGER_DOCTYPE, event_key, f"{channel}_recovered_at", now_datetime(), update_modified=False
		)
		enqueue_assignment_notification(event_key, channel)
	except Exception as e:
		# The queue is unreachable: leave the channel stale for the next sweep.
		frappe.db.rollback(save_point=RECOVERY_SAVEPOINT)
		frappe.log_error(
			title=LOG_TITLE,
			message=f"event={event_key} channel={channel} error=recovery_queue_failed: {type(e).__name__}",
		)
		frappe.db.commit()  # nosemgrep -- keep the log
		return False
	frappe.db.commit()  # nosemgrep -- the recovery stamp and its job must be durable together
	return True


@frappe.whitelist(methods=["POST"])
def retry_assignment_notification_channel(event_key: str, channel: str) -> dict:
	"""Re-queue one definite Failed `channel` of `event_key` to its captured recipient.

	System Manager only. Anything but Failed is refused, without contacting a provider.
	"""
	_require_system_manager()
	if channel not in CHANNELS:
		frappe.throw(_("Unknown notification channel: {0}").format(channel), frappe.ValidationError)
	# Locked until the request commits, so a second retry of the same channel finds it Pending.
	if not frappe.db.get_value(LEDGER_DOCTYPE, event_key, "name", for_update=True):
		frappe.throw(
			_("Delivery Coach notification {0} not found.").format(event_key), frappe.DoesNotExistError
		)

	event = frappe.get_doc(LEDGER_DOCTYPE, event_key)
	status = event.get(f"{channel}_status")
	if status != STATUS_FAILED:
		frappe.throw(
			_("Only a Failed {0} delivery can be retried; this one is {1}.").format(channel, status),
			frappe.ValidationError,
		)

	# Only channel state changes; a since-deleted Deal or User must not block the retry.
	event.flags.ignore_links = True
	event.set(f"{channel}_status", STATUS_PENDING)
	event.set(f"{channel}_recovered_at", now_datetime())
	event.save(ignore_permissions=True)
	event.add_comment("Info", _("{0} retry requested by {1}").format(channel.title(), frappe.session.user))
	enqueue_assignment_notification(event_key, channel)
	return _diagnostics(event)


@frappe.whitelist()
def get_assignment_notification_status(event_key: str) -> dict:
	"""The event's Deal, routing and per-channel state, for a System Manager."""
	_require_system_manager()
	return _diagnostics(frappe.get_doc(LEDGER_DOCTYPE, event_key))


def _require_system_manager() -> None:
	if not is_system_manager():
		frappe.throw(
			_("Only a System Manager can manage Delivery Coach notifications."),
			frappe.PermissionError,
			title=_("Not permitted"),
		)


def _diagnostics(event) -> dict:
	# Identifiers and the ledger's already sanitized error summary only -- never message content.
	return {
		"event": event.name,
		"deal": event.deal,
		"intended_user": event.intended_user,
		"effective_user": event.effective_user,
		"mode": event.mode,
		"channels": {
			channel: {
				"status": event.get(f"{channel}_status"),
				"attempts": cint(event.get(f"{channel}_attempts")),
				"error": event.get(f"{channel}_error"),
				"sent_at": event.get(f"{channel}_sent_at"),
				"claimed_at": event.get(f"{channel}_claimed_at"),
				"recovered_at": event.get(f"{channel}_recovered_at"),
				"can_retry": event.get(f"{channel}_status") == STATUS_FAILED,
			}
			for channel in CHANNELS
		},
	}

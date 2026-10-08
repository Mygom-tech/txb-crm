"""Delivery Coach assignment notifications: detect the assignment, then email and Slack the coach.

Built on the TXB-269 foundation (settings, ledger, Email Template). Two halves:

* `queue_assignment_notification` (CRM Deal on_update) turns a real change of a Delivering
  Coaching deal's Delivery Coach into one ledger row, keyed by the save that made it, and
  queues the worker after commit -- so delivery can never delay, fail or roll back the save.
* `process_assignment_notification` (the worker) delivers email and Slack independently. Before
  each channel it rechecks that the event is still the Deal's current assignment generation in
  Delivering Coaching and that notifications are not Disabled; otherwise the channel is Skipped
  without contacting anyone. Each channel is claimed under a row lock and its outcome committed
  on its own, so a re-run only retries Pending/Failed channels: Sent and Skipped are final, and
  an Uncertain Slack DM (it may have been delivered) is held, never resent automatically.

The previous coach is never notified, and nothing is backfilled: only a save that changes the
coach creates an event.
"""

import frappe
from frappe.utils import get_datetime

from crm.txb.constants import FIELD_DELIVERY_COACH, PIPELINE_DELIVERING_COACHING
from crm.txb.delivery_coach_notifications import (
	CHANNELS,
	LEDGER_DOCTYPE,
	MODE_DISABLED,
	MODE_TEST_REDIRECT,
	STATUS_FAILED,
	STATUS_PENDING,
	STATUS_SENT,
	STATUS_SKIPPED,
	STATUS_UNCERTAIN,
	build_assignment_email_context,
	get_notification_config,
	get_slack_bot_token,
	render_assignment_email,
)
from crm.txb.slack import SlackError, send_direct_message

LOG_TITLE = "Delivery Coach assignment notification"
SUPERSEDED = "Superseded: the Delivery Coach changed before delivery"
LEFT_PIPELINE = "Skipped: the Deal left Delivering Coaching before delivery"
STOPPED = "Skipped: notifications were Disabled before delivery"
OUTCOME_NOT_RECORDED = "outcome_not_recorded"


class SlackOutcomeNotRecorded(Exception):
	"""A Slack DM may have been posted, but its outcome could not be saved."""


def queue_assignment_notification(deal, method=None):
	"""Record and queue one notification for a newly assigned Delivery Coach.

	Only a real change to a non-empty coach counts; unchanged saves, clearing the coach,
	assigning yourself, imports and migrations create nothing. Best-effort: the deal save must
	never fail because the notification could not be queued.
	"""
	if deal.pipeline_type != PIPELINE_DELIVERING_COACHING:
		return
	coach = deal.get(FIELD_DELIVERY_COACH)
	before = deal.get_doc_before_save()
	if not coach or (before and before.get(FIELD_DELIVERY_COACH) == coach):
		return
	if coach == frappe.session.user:
		return
	if frappe.flags.in_import or frappe.flags.in_migrate or frappe.flags.in_patch:
		return

	try:
		_record_assignment(deal, coach, frappe.session.user)
	except Exception as e:
		frappe.log_error(
			title=LOG_TITLE,
			message=f"deal={deal.name} intended_user={coach} error=queue_failed: {type(e).__name__}",
		)


def _record_assignment(deal, coach: str, assigned_by: str) -> None:
	# One save is one event: its modified timestamp keys the ledger row and the job.
	assigned_at = get_datetime(deal.modified)
	event_key = f"{deal.name}-{assigned_at:%Y%m%d%H%M%S%f}"
	if frappe.db.exists(LEDGER_DOCTYPE, event_key):
		return

	config = get_notification_config()
	mode = config["mode"]
	status = STATUS_SKIPPED if mode == MODE_DISABLED else STATUS_PENDING
	frappe.get_doc(
		{
			"doctype": LEDGER_DOCTYPE,
			"event_key": event_key,
			"deal": deal.name,
			"intended_user": coach,
			"effective_user": config["test_user"] if mode == MODE_TEST_REDIRECT else coach,
			"assigned_by": assigned_by,
			"assigned_at": assigned_at,
			"mode": mode,
			"email_status": status,
			"slack_status": status,
		}
	).insert(ignore_permissions=True)

	if mode == MODE_DISABLED:
		return
	frappe.enqueue(
		"crm.txb.delivery_coach_assignment.process_assignment_notification",
		queue="short",
		job_id=f"delivery-coach-assignment-{event_key}",
		deduplicate=True,
		enqueue_after_commit=True,
		event_key=event_key,
	)


def process_assignment_notification(event_key: str) -> None:
	"""Deliver every channel of `event_key` that is still Pending or Failed. Safe to re-run.

	Each channel is its own unit: a failure while preparing, sending or recording one is
	undone to that channel's savepoint and logged, and the other channel still runs.
	"""
	for channel in CHANNELS:
		savepoint = f"coach_assignment_{channel}"
		frappe.db.savepoint(savepoint)
		try:
			_process_channel(event_key, channel)
		except Exception as e:
			frappe.db.rollback(save_point=savepoint)
			frappe.log_error(
				title=LOG_TITLE,
				message=f"event={event_key} channel={channel} error=worker_failed: {type(e).__name__}",
			)
			if isinstance(e, SlackOutcomeNotRecorded):
				# Slack may already show the message: hold it rather than leave it to be resent.
				frappe.db.set_value(
					LEDGER_DOCTYPE,
					event_key,
					{f"{channel}_status": STATUS_UNCERTAIN, f"{channel}_error": OUTCOME_NOT_RECORDED},
					update_modified=False,
				)
			frappe.db.commit()  # nosemgrep -- keep the log and hold even if the other channel fails


def _process_channel(event_key: str, channel: str) -> None:
	# The row lock is held until `_record` commits, so a concurrent run of the same event waits
	# and then sees the channel already decided.
	if not frappe.db.get_value(LEDGER_DOCTYPE, event_key, "name", for_update=True):
		return
	event = frappe.get_doc(LEDGER_DOCTYPE, event_key)
	# Only channel state changes here; a since-deleted Deal or User must not block the outcome.
	event.flags.ignore_links = True
	if not event.is_channel_pending(channel):
		return

	# Rechecked before every channel: the other one may have taken long enough for a change.
	skip_reason = _skip_reason(event)
	if skip_reason:
		_record(event, channel, STATUS_SKIPPED, skip_reason)
		return

	event.claim_channel(channel)
	try:
		SENDERS[channel](event)
	except SlackError as e:
		status, error = (STATUS_UNCERTAIN if e.uncertain else STATUS_FAILED), e.code
	except Exception as e:
		status, error = STATUS_FAILED, f"{type(e).__name__}: {e}"
	else:
		status, error = STATUS_SENT, None

	try:
		_record(event, channel, status, error)
	except Exception as e:
		# A queued email rolls back with its unrecorded attempt; a posted Slack message cannot.
		if channel == "slack" and status in (STATUS_SENT, STATUS_UNCERTAIN):
			raise SlackOutcomeNotRecorded(type(e).__name__) from None
		raise


def _skip_reason(event) -> str | None:
	"""Why `event` may no longer be delivered, or None while it is the current assignment."""
	deal = frappe.db.get_value("CRM Deal", event.deal, ["pipeline_type", FIELD_DELIVERY_COACH], as_dict=True)
	# Reassigned, cleared or deleted -- or reassigned and back (A->B->A), which recorded a newer
	# generation: this event's coach is now a previous coach.
	if (
		not deal
		or deal.get(FIELD_DELIVERY_COACH) != event.intended_user
		or _generation(event) != _current_generation(event.deal)
	):
		return SUPERSEDED
	if deal.pipeline_type != PIPELINE_DELIVERING_COACHING:
		return LEFT_PIPELINE
	# Disabled now is an emergency stop. Any other mode leaves the captured routing alone, so a
	# queued Test redirect event never reaches the coach and a Live one never the test user.
	if get_notification_config()["mode"] == MODE_DISABLED:
		return STOPPED
	return None


def _generation(event) -> str:
	# Events recorded before generations existed are their own generation.
	return event.get("assignment_generation") or event.get("name")


def _current_generation(deal: str) -> str | None:
	latest = frappe.get_all(
		LEDGER_DOCTYPE,
		filters={"deal": deal},
		fields=["name", "assignment_generation"],
		order_by="assigned_at desc, name desc",
		limit=1,
	)
	return _generation(latest[0]) if latest else None


def _record(event, channel: str, status: str, error=None) -> None:
	event.reload()
	event.flags.ignore_links = True
	event.mark_channel(channel, status, error)
	if status in (STATUS_FAILED, STATUS_UNCERTAIN):
		# Identifiers and the sanitized provider error only -- never tokens or response data.
		frappe.log_error(
			title=LOG_TITLE,
			message=(
				f"deal={event.deal} intended_user={event.intended_user} "
				f"effective_user={event.effective_user} mode={event.mode} channel={channel} "
				f"status={status} error={event.get(f'{channel}_error')}"
			),
		)
	frappe.db.commit()  # nosemgrep -- each channel's outcome must survive the other's failure


def _recipient(event) -> str:
	recipient = frappe.db.get_value("User", event.effective_user, "email")
	if not recipient:
		raise ValueError("recipient_has_no_email")
	return recipient


def _context(event) -> dict:
	return build_assignment_email_context(
		frappe.get_doc("CRM Deal", event.deal),
		event.intended_user,
		event.assigned_by,
		event.assigned_at,
		is_test_redirect=event.mode == MODE_TEST_REDIRECT,
	)


def _send_email(event) -> None:
	# Queued, not now=True: the immediate path swallows SMTP errors, which would read as Sent.
	# Frappe's outbox owns SMTP delivery and its retries from here.
	recipient = _recipient(event)
	subject, message = render_assignment_email(_context(event))
	frappe.sendmail(recipients=[recipient], subject=subject, message=message)


def _send_slack(event) -> None:
	recipient = _recipient(event)
	text = build_slack_message(_context(event))
	token = get_slack_bot_token()
	if not token:
		raise SlackError("missing_bot_token")
	send_direct_message(token, recipient, text)


SENDERS = {"email": _send_email, "slack": _send_slack}


def _escape(value) -> str:
	return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_slack_message(context: dict) -> str:
	"""The Slack DM: the assignment email's essential content and Deal link in Slack mrkdwn."""
	values = {key: _escape(value) for key, value in context.items()}
	lines = []
	if context["is_test_redirect"]:
		lines.append(
			f":warning: *Test notification.* This assignment is intended for "
			f"*{values['intended_coach_name']}*."
		)
	lines.append(
		f"Hi {values['intended_coach_name']}, {values['assigned_by_name']} assigned a client to you "
		f"as Delivery Coach on {values['assigned_at']}."
	)
	lines.append(f"*Client:* {values['client_name']}")
	if context["organization"]:
		lines.append(f"*Organization:* {values['organization']}")
	if context["program_type"]:
		lines.append(f"*Program:* {values['program_type']}")
	lines.append(f"*Status:* {values['status']}")
	lines.append(f"<{context['opportunity_url']}|Open the Opportunity in CRM>")
	return "\n".join(lines)

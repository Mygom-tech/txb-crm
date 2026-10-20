"""Delivery Coach notifications: activation and later reassignment, by email.

Built on the TXB-269 foundation (settings, ledger, Email Templates). Two triggers record one
ledger row each and queue the worker after commit, so delivery can never delay, fail or roll
back the save:

* `record_activation` -- called by the Set First Call Date action, a Delivering Coaching deal's
  first move to Active -- emails the Delivery Coach the client's details and the client that a
  coach was assigned, both promising contact within 48 hours. In the same transaction it gives
  the coach a CRM Task due in 48 hours and tells the sales Opportunity's owner, in the CRM, that
  their sold client has a coach. Keyed per deal, so it happens once however often it is called.
* `queue_assignment_notification` (CRM Deal on_update) emails a coach assigned to a deal that is
  already Active. The client is not emailed again. Before activation a coach change sends
  nothing: activation is when the coach hears about the client.

`process_assignment_notification` (the worker) delivers each channel independently. Before each
it rechecks that the event is still the Deal's current assignment generation in Delivering
Coaching and that notifications are not Disabled; otherwise the channel is Skipped without
contacting anyone. Each channel is claimed under a row lock and its outcome committed on its
own, so a re-run only retries Pending/Failed channels: Sent and Skipped are final.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime

from crm.fcrm.doctype.crm_notification.crm_notification import notify_user
from crm.txb.constants import (
	FIELD_DELIVERY_COACH,
	FIELD_SALES_SOURCE_DEAL,
	PIPELINE_DELIVERING_COACHING,
	STATUS_ACTIVE,
)
from crm.txb.delivery_coach_notifications import (
	CHANNELS,
	CLIENT_EMAIL_TEMPLATE,
	LEDGER_DOCTYPE,
	MODE_DISABLED,
	MODE_TEST_REDIRECT,
	STATUS_FAILED,
	STATUS_PENDING,
	STATUS_SENT,
	STATUS_SKIPPED,
	build_assignment_email_context,
	client_name,
	get_notification_config,
	render_assignment_email,
)

LOG_TITLE = "Delivery Coach assignment notification"
SUPERSEDED = "Superseded: the Delivery Coach changed before delivery"
LEFT_PIPELINE = "Skipped: the Deal left Delivering Coaching before delivery"
STOPPED = "Skipped: notifications were Disabled before delivery"
NOT_ACTIVATION = "Skipped: the client is only emailed when coaching is activated"
REACH_OUT_HOURS = 48
ACTIVATION_SAVEPOINT = "coach_activation"


def record_activation(deal) -> None:
	"""Notify the coach, the client and the sales owner that coaching was activated.

	Called by the activation action before it saves the deal, inside its transaction. Best
	effort: a failure here is logged and never blocks the activation.
	"""
	event_key = f"{deal.name}-activation"
	coach = deal.get(FIELD_DELIVERY_COACH)
	if not coach or frappe.db.exists(LEDGER_DOCTYPE, event_key):
		return
	frappe.db.savepoint(ACTIVATION_SAVEPOINT)
	try:
		_add_reach_out_task(deal, coach)
		_notify_sales_owner(deal, coach)
		_record_event(deal, event_key, coach, now_datetime(), notify_client=True)
	except Exception as e:
		frappe.db.rollback(save_point=ACTIVATION_SAVEPOINT)
		frappe.log_error(
			title=LOG_TITLE,
			message=f"deal={deal.name} intended_user={coach} error=activation_failed: {type(e).__name__}",
		)


def queue_assignment_notification(deal, method=None):
	"""Record and queue one email to a coach newly assigned to an already Active deal.

	Only a real change to a non-empty coach on a deal that was Active before this save counts;
	unchanged saves, clearing the coach, assigning yourself, imports and migrations create
	nothing. Best-effort: the deal save must never fail because the notification could not be
	queued.
	"""
	if deal.pipeline_type != PIPELINE_DELIVERING_COACHING:
		return
	coach = deal.get(FIELD_DELIVERY_COACH)
	before = deal.get_doc_before_save()
	if not coach or not before or before.get(FIELD_DELIVERY_COACH) == coach:
		return
	if before.status != STATUS_ACTIVE or deal.status != STATUS_ACTIVE:
		return
	if coach == frappe.session.user:
		return
	if frappe.flags.in_import or frappe.flags.in_migrate or frappe.flags.in_patch:
		return

	# One save is one event: its modified timestamp keys the ledger row and the job.
	assigned_at = get_datetime(deal.modified)
	try:
		_record_event(deal, f"{deal.name}-{assigned_at:%Y%m%d%H%M%S%f}", coach, assigned_at)
	except Exception as e:
		frappe.log_error(
			title=LOG_TITLE,
			message=f"deal={deal.name} intended_user={coach} error=queue_failed: {type(e).__name__}",
		)


def _record_event(deal, event_key: str, coach: str, assigned_at, notify_client: bool = False) -> None:
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
			"assigned_by": frappe.session.user,
			"assigned_at": assigned_at,
			"mode": mode,
			"email_status": status,
			"client_email_status": status if notify_client else STATUS_SKIPPED,
			"client_email_error": None if notify_client else NOT_ACTIVATION,
		}
	).insert(ignore_permissions=True)

	if mode == MODE_DISABLED:
		return
	enqueue_assignment_notification(event_key)


def _add_reach_out_task(deal, coach: str) -> None:
	"""The coach's CRM Task to contact the client within 48 hours."""
	# Imported here: the pipelines import this module.
	from crm.txb.pipelines.common import add_task

	add_task(
		deal,
		"Reach out to the client",
		_("Coaching is active. Contact the client within {0} hours.").format(REACH_OUT_HOURS),
		assigned_to=coach,
		due_date=add_to_date(now_datetime(), hours=REACH_OUT_HOURS),
		priority="High",
	)


def _notify_sales_owner(deal, coach: str) -> None:
	"""Tell the owner of the sales Opportunity this deal was handed over from, in the CRM."""
	source = deal.get(FIELD_SALES_SOURCE_DEAL)
	owner = frappe.db.get_value("CRM Deal", source, "deal_owner") if source else None
	if not owner:
		return
	coach_name = frappe.db.get_value("User", coach, "full_name") or coach
	text = _("{0} is now coached by {1}.").format(client_name(deal), coach_name)
	notify_user(
		{
			"owner": frappe.session.user,
			"assigned_to": owner,
			"notification_type": "Assignment",
			"message": text,
			"notification_text": f"<p>{frappe.utils.escape_html(text)}</p>",
			"reference_doctype": "CRM Deal",
			"reference_docname": deal.name,
			"redirect_to_doctype": "CRM Deal",
			"redirect_to_docname": deal.name,
		}
	)


def enqueue_assignment_notification(event_key: str, channel: str | None = None) -> None:
	"""Queue the worker for `event_key` -- every channel, or only `channel` -- after commit.

	The job id is deterministic per event and channel, so a request for work whose job is
	still waiting is absorbed by that job instead of queueing a second one.
	"""
	frappe.enqueue(
		"crm.txb.delivery_coach_assignment.process_assignment_notification",
		queue="short",
		job_id=f"delivery-coach-assignment-{event_key}" + (f"-{channel}" if channel else ""),
		deduplicate=True,
		enqueue_after_commit=True,
		event_key=event_key,
		channel=channel,
	)


def process_assignment_notification(event_key: str, channel: str | None = None) -> None:
	"""Deliver every channel of `event_key` that is still Pending or Failed. Safe to re-run.

	A `channel` limits the run to that one channel, so recovering or retrying it never
	re-attempts the other. Each channel is its own unit: a failure while preparing, sending or
	recording one is undone to that channel's savepoint and logged, and the other still runs.
	"""
	channels = CHANNELS if channel is None else [c for c in CHANNELS if c == channel]
	for channel in channels:
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
			frappe.db.commit()  # nosemgrep -- keep the log even if the other channel fails


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
	except Exception as e:
		status, error = STATUS_FAILED, f"{type(e).__name__}: {e}"
	else:
		status, error = STATUS_SENT, None
	_record(event, channel, status, error)


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
	# queued Test redirect event never reaches the coach or client and a Live one never the test user.
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
	if status == STATUS_FAILED:
		# Identifiers and the sanitized error only -- never message content.
		frappe.log_error(
			title=LOG_TITLE,
			message=(
				f"deal={event.deal} intended_user={event.intended_user} "
				f"effective_user={event.effective_user} mode={event.mode} channel={channel} "
				f"status={status} error={event.get(f'{channel}_error')}"
			),
		)
	frappe.db.commit()  # nosemgrep -- each channel's outcome must survive the other's failure


def _user_email(user: str) -> str:
	recipient = frappe.db.get_value("User", user, "email")
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


# Queued, not now=True: the immediate path swallows SMTP errors, which would read as Sent.
# Frappe's outbox owns SMTP delivery and its retries from here.


def _send_email(event) -> None:
	subject, message = render_assignment_email(_context(event))
	frappe.sendmail(recipients=[_user_email(event.effective_user)], subject=subject, message=message)


def _send_client_email(event) -> None:
	context = _context(event)
	if event.mode == MODE_TEST_REDIRECT:
		recipient = _user_email(event.effective_user)
	else:
		# Imported here: coaching_call_recap reaches the pipelines, which import this module.
		from crm.txb.coaching_call_recap import primary_contact_email

		recipient = primary_contact_email(frappe.get_doc("CRM Deal", event.deal))[1]
		if not recipient:
			raise ValueError("client_has_no_primary_email")
	subject, message = render_assignment_email(context, CLIENT_EMAIL_TEMPLATE)
	frappe.sendmail(recipients=[recipient], subject=subject, message=message)


SENDERS = {"email": _send_email, "client_email": _send_client_email}

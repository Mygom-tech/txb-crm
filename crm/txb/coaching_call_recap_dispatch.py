"""Coaching Call recap delivery: one queued ledger row becomes one client email (TXB-274).

* `enqueue_recap` queues the worker after commit, one job per recap, so a send never delays or
  rolls back the request that queued it and never sees a row that did not commit.
* `process_recap` (the worker) locks the row and sends only a `queued` one, then commits it as
  `sent` or `failed`. A re-run, a duplicate job or a concurrent worker finds it no longer queued
  and sends nothing more; only a Retry puts a failed row back in the queue.
* `sweep_stale_recaps` re-queues rows still `queued` long after creation, e.g. when Redis was
  down at commit time. The job id dedupes it against a job that is still waiting.

The recap's own `last_error` holds a sanitized summary only -- never the email body.
"""

import json

import frappe
from frappe.utils import add_to_date, now_datetime

from crm.txb.coaching_call_recap import RECAP_DOCTYPE, STATUS_QUEUED
from crm.txb.coaching_call_recap_email import render_recap_email
from crm.txb.delivery_coach_notifications import sanitize_error

STATUS_SENT = "sent"
STATUS_FAILED = "failed"

LOG_TITLE = "Coaching Call recap email"
RECIPIENT_MISSING = "recipient_email_missing"
STALE_AFTER_MINUTES = 10


def enqueue_recap(name: str) -> None:
	"""Queue delivery of recap `name` once the current transaction commits."""
	frappe.enqueue(
		"crm.txb.coaching_call_recap_dispatch.process_recap",
		queue="short",
		job_id=f"coaching-call-recap-{name}",
		deduplicate=True,
		enqueue_after_commit=True,
		recap=name,
	)


def queue_new_recap(doc, method=None) -> None:
	"""CRM Coaching Call Recap after_insert: a row created `queued` is sent after its commit.

	Best-effort: the deduplication check reaches Redis now, and an unreachable queue must not
	fail the Coaching Call being logged. The row stays queued for `sweep_stale_recaps`.
	"""
	if doc.status != STATUS_QUEUED:
		return
	try:
		enqueue_recap(doc.name)
	except Exception as e:
		frappe.log_error(
			title=LOG_TITLE, message=f"recap={doc.name} error=queue_failed: {type(e).__name__}"
		)


def process_recap(recap: str) -> None:
	"""Send recap `recap` if it is still queued. Safe to re-run."""
	# Locked until the outcome commits, so a concurrent run waits and then finds it settled.
	row = frappe.db.get_value(
		RECAP_DOCTYPE,
		recap,
		["name", "deal", "status", "recipient_email", "content_snapshot", "attempts"],
		as_dict=True,
		for_update=True,
	)
	if not row or row.status != STATUS_QUEUED:
		return

	attempts = (row.attempts or 0) + 1
	try:
		if not row.recipient_email:
			raise ValueError(RECIPIENT_MISSING)
		subject, message = render_recap_email(json.loads(row.content_snapshot or "{}"))
		frappe.sendmail(
			recipients=[row.recipient_email],
			subject=subject,
			message=message,
			attachments=[],
			inline_images=[],
		)
	except Exception as e:
		error = sanitize_error(f"{type(e).__name__}: {e}")
		_record(row, STATUS_FAILED, attempts, last_error=error)
		frappe.log_error(title=LOG_TITLE, message=f"recap={row.name} deal={row.deal} error={error}")
	else:
		_record(row, STATUS_SENT, attempts, sent_at=now_datetime(), last_error=None)
	frappe.db.commit()  # nosemgrep -- the outcome must be durable before the lock is released


def _record(row, status: str, attempts: int, **values) -> None:
	frappe.db.set_value(RECAP_DOCTYPE, row.name, {"status": status, "attempts": attempts, **values})


def sweep_stale_recaps() -> None:
	"""Re-queue recaps left `queued` past STALE_AFTER_MINUTES, whose job was lost."""
	cutoff = add_to_date(now_datetime(), minutes=-STALE_AFTER_MINUTES)
	for name in frappe.get_all(
		RECAP_DOCTYPE, filters={"status": STATUS_QUEUED, "modified": ["<", cutoff]}, pluck="name"
	):
		enqueue_recap(name)

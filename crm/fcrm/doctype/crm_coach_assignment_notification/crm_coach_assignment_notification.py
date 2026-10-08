# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime

from crm.txb.delivery_coach_notifications import (
	CHANNEL_TRANSITIONS,
	CHANNELS,
	CLAIMABLE_STATUSES,
	STATUS_PENDING,
	STATUS_SENT,
	sanitize_error,
)


class CRMCoachAssignmentNotification(Document):
	"""One committed Delivery Coach assignment event and its per-channel delivery state.

	Named by `event_key`, so the database refuses a second row for the same event. Email and
	Slack move independently through CHANNEL_TRANSITIONS; a channel already Sent can never be
	moved again, which is what makes a retry safe, and an Uncertain one waits for
	reconciliation instead of being resent. The captured routing (mode, intended and effective
	user) and the assignment generation are set-only-once, so recovery always delivers to the
	recipient the event was recorded with.
	"""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		assigned_at: DF.Datetime
		assigned_by: DF.Link | None
		assignment_generation: DF.Data | None
		deal: DF.Link
		effective_user: DF.Link
		email_attempts: DF.Int
		email_claimed_at: DF.Datetime | None
		email_error: DF.SmallText | None
		email_recovered_at: DF.Datetime | None
		email_sent_at: DF.Datetime | None
		email_status: DF.Literal["Pending", "Sent", "Failed", "Skipped", "Uncertain"]
		event_key: DF.Data
		intended_user: DF.Link
		mode: DF.Literal["Disabled", "Test redirect", "Live"]
		slack_attempts: DF.Int
		slack_claimed_at: DF.Datetime | None
		slack_error: DF.SmallText | None
		slack_recovered_at: DF.Datetime | None
		slack_sent_at: DF.Datetime | None
		slack_status: DF.Literal["Pending", "Sent", "Failed", "Skipped", "Uncertain"]
	# end: auto-generated types

	def validate(self):
		# A new event's generation defaults to the save that recorded it. Events recorded before
		# the field existed keep a blank one: nothing is backfilled.
		if self.is_new() and not self.assignment_generation:
			self.assignment_generation = self.event_key
		previous = None if self.is_new() else self.get_doc_before_save()
		for channel in CHANNELS:
			self._validate_channel(channel, previous)

	def _validate_channel(self, channel: str, previous) -> None:
		status_field = f"{channel}_status"
		status = self.get(status_field) or STATUS_PENDING
		self.set(status_field, status)

		old = previous.get(status_field) if previous else None
		if old and status not in CHANNEL_TRANSITIONS.get(old, ()):
			frappe.throw(
				_("{0} delivery cannot move from {1} to {2}.").format(channel.title(), old, status),
				frappe.ValidationError,
			)

		attempts_field = f"{channel}_attempts"
		if previous and cint(self.get(attempts_field)) < cint(previous.get(attempts_field)):
			frappe.throw(
				_("{0} delivery attempts cannot decrease.").format(channel.title()), frappe.ValidationError
			)

		if status == STATUS_SENT and not self.get(f"{channel}_sent_at"):
			self.set(f"{channel}_sent_at", now_datetime())
		self.set(f"{channel}_error", sanitize_error(self.get(f"{channel}_error")))

	def _check_channel(self, channel: str) -> None:
		if channel not in CHANNELS:
			frappe.throw(_("Unknown notification channel: {0}").format(channel))

	def is_channel_pending(self, channel: str) -> bool:
		"""Whether `channel` may get a delivery attempt (Pending or a retryable Failed)."""
		return self.get(f"{channel}_status") in CLAIMABLE_STATUSES

	def claim_channel(self, channel: str, recovery: bool = False) -> bool:
		"""Start one delivery attempt of `channel`: count it and stamp the claim.

		Only a Pending or Failed channel can be claimed; Sent and Skipped are final and an
		Uncertain one may already have been delivered. `recovery` marks a claim taken over from
		an interrupted attempt. Saved, not committed -- the caller owns the transaction.
		"""
		self._check_channel(channel)
		if not self.is_channel_pending(channel):
			return False
		now = now_datetime()
		self.set(f"{channel}_attempts", cint(self.get(f"{channel}_attempts")) + 1)
		self.set(f"{channel}_claimed_at", now)
		if recovery:
			self.set(f"{channel}_recovered_at", now)
		self.save(ignore_permissions=True)
		return True

	def mark_channel(self, channel: str, status: str, error=None) -> None:
		"""Record one channel's outcome without touching the other channel."""
		self._check_channel(channel)
		self.set(f"{channel}_status", status)
		self.set(f"{channel}_error", error)
		self.save(ignore_permissions=True)

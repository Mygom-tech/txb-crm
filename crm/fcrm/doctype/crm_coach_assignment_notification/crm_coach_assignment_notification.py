# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from crm.txb.delivery_coach_notifications import (
	CHANNEL_TRANSITIONS,
	CHANNELS,
	STATUS_PENDING,
	STATUS_SENT,
	STATUS_SKIPPED,
	sanitize_error,
)


class CRMCoachAssignmentNotification(Document):
	"""One committed Delivery Coach assignment event and its per-channel delivery state.

	Named by `event_key`, so the database refuses a second row for the same event. Email and
	Slack move independently through CHANNEL_TRANSITIONS; a channel already Sent can never be
	moved again, which is what makes a retry safe.
	"""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		assigned_at: DF.Datetime
		assigned_by: DF.Link | None
		deal: DF.Link
		effective_user: DF.Link
		email_error: DF.SmallText | None
		email_sent_at: DF.Datetime | None
		email_status: DF.Literal["Pending", "Sent", "Failed", "Skipped"]
		event_key: DF.Data
		intended_user: DF.Link
		mode: DF.Literal["Disabled", "Test redirect", "Live"]
		slack_error: DF.SmallText | None
		slack_sent_at: DF.Datetime | None
		slack_status: DF.Literal["Pending", "Sent", "Failed", "Skipped"]
	# end: auto-generated types

	def validate(self):
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

		if status == STATUS_SENT and not self.get(f"{channel}_sent_at"):
			self.set(f"{channel}_sent_at", now_datetime())
		self.set(f"{channel}_error", sanitize_error(self.get(f"{channel}_error")))

	def is_channel_pending(self, channel: str) -> bool:
		"""Whether `channel` still needs a delivery attempt (Pending or a retryable Failed)."""
		return self.get(f"{channel}_status") not in (STATUS_SENT, STATUS_SKIPPED)

	def mark_channel(self, channel: str, status: str, error=None) -> None:
		"""Record one channel's outcome without touching the other channel."""
		if channel not in CHANNELS:
			frappe.throw(_("Unknown notification channel: {0}").format(channel))
		self.set(f"{channel}_status", status)
		self.set(f"{channel}_error", error)
		self.save(ignore_permissions=True)

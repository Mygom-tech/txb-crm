# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from crm.txb.coaching_call_recap import STATUS_OPTED_OUT


class CRMCoachingCallRecap(Document):
	"""One Log Coaching Call submission's recap decision and the content it would send (TXB-273).

	`submission_id` is unique, so the database refuses a second recap for the same submission.
	A consented recap must name the address it goes to; an opted-out one is never sent.
	"""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		attempts: DF.Int
		consent: DF.Check
		consent_at: DF.Datetime | None
		consent_by: DF.Link | None
		content_snapshot: DF.JSON | None
		created_by: DF.Link | None
		deal: DF.Link
		last_error: DF.SmallText | None
		note: DF.Link | None
		recipient_contact: DF.Link | None
		recipient_email: DF.Data | None
		revision_of: DF.Link | None
		sent_at: DF.Datetime | None
		status: DF.Literal["opted_out", "queued", "sent", "failed"]
		submission_id: DF.Data
	# end: auto-generated types

	def validate(self):
		if self.consent and not self.recipient_email:
			frappe.throw(_("A recap that will be sent needs a recipient email."), frappe.ValidationError)
		if not self.consent and self.status != STATUS_OPTED_OUT:
			frappe.throw(_("A recap without consent can only be opted out."), frappe.ValidationError)

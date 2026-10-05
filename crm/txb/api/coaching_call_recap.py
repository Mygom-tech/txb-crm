"""Coaching Call recap recovery commands for the Deal Activity feed (TXB-274).

* `retry_recap` puts a `failed` recap back in the queue.
* `send_revised_copy` sends a `sent` recap again as a new `queued` row pointing at it through
  `revision_of`, so the original send stays on record.

Both need write access to the recap's Deal and refuse any other state. Their queued row is
delivered by a job queued after commit (`crm.txb.coaching_call_recap_dispatch`).
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from crm.txb.coaching_call_recap import RECAP_DOCTYPE, STATUS_QUEUED
from crm.txb.coaching_call_recap_dispatch import STATUS_FAILED, STATUS_SENT, enqueue_recap
from crm.txb.pipelines.common import DEAL_DOCTYPE


@frappe.whitelist()
def retry_recap(recap: str) -> dict:
	"""Queue a failed recap for another attempt."""
	doc = _load_for_change(recap, STATUS_FAILED, _("Only a failed recap can be retried."))
	doc.db_set({"status": STATUS_QUEUED, "last_error": None})
	enqueue_recap(doc.name)
	return {"name": doc.name, "status": STATUS_QUEUED}


@frappe.whitelist()
def send_revised_copy(recap: str, confirm: int = 0) -> dict:
	"""Send a sent recap again as a new queued row; `confirm` must be 1."""
	if cint(confirm) != 1:
		frappe.throw(_("Confirm sending the client another copy of this recap."), frappe.ValidationError)
	source = _load_for_change(recap, STATUS_SENT, _("Only a sent recap can be sent again."))
	user = frappe.session.user
	# Inserted queued, so its after_insert queues the send once this request commits.
	copy = frappe.get_doc(
		{
			"doctype": RECAP_DOCTYPE,
			"submission_id": frappe.generate_hash(length=20),
			"deal": source.deal,
			"note": source.note,
			"revision_of": source.name,
			"consent": 1,
			"consent_by": user,
			"consent_at": now_datetime(),
			"recipient_contact": source.recipient_contact,
			"recipient_email": source.recipient_email,
			"content_snapshot": source.content_snapshot,
			"status": STATUS_QUEUED,
			"attempts": 0,
			"created_by": user,
		}
	).insert(ignore_permissions=True)
	return {"name": copy.name, "status": STATUS_QUEUED, "revision_of": source.name}


def _load_for_change(recap: str, required_status: str, message: str):
	"""The recap, row-locked, once the user may write its Deal and it is in `required_status`."""
	deal = frappe.db.get_value(RECAP_DOCTYPE, recap, "deal")
	if not deal:
		frappe.throw(_("Recap {0} not found.").format(recap), frappe.DoesNotExistError)
	if not frappe.has_permission(DEAL_DOCTYPE, "write", deal):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	# Locked so two clicks, or a click racing the worker, see one consistent status.
	status = frappe.db.get_value(RECAP_DOCTYPE, recap, "status", for_update=True)
	if status != required_status:
		frappe.throw(message, frappe.ValidationError)
	return frappe.get_doc(RECAP_DOCTYPE, recap)

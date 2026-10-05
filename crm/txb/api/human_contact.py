"""Human contact actions (TXB-277).

The endpoints a person uses to make contact. Each performs the real action and records it as a
CRM Human Contact Event in the same request, which is what lets the server tell a manual send or
a completed meeting apart from an identical-looking document a system path writes directly.
"""

import frappe
from frappe.core.doctype.communication.email import make

from crm.txb import human_contact, meetings


@frappe.whitelist(methods=["POST"])
def human_send_email(
	recipients,
	cc,
	bcc,
	subject,
	content,
	doctype,
	name,
	sender=None,
	sender_full_name=None,
	attachments=None,
) -> dict:
	"""Send the composer's email exactly as `communication.email.make` does, and record it.

	`make` keeps its own email-permission check on the referenced record.
	"""
	result = make(
		doctype=doctype,
		name=name,
		content=content,
		subject=subject,
		sender=sender,
		sender_full_name=sender_full_name,
		recipients=recipients,
		cc=cc,
		bcc=bcc,
		attachments=attachments,
		send_email=1,
	)
	communication = frappe.get_doc("Communication", result["name"])
	return {
		"communication": communication.name,
		"event": human_contact.record_human_send(communication),
	}


@frappe.whitelist(methods=["POST"])
def complete_meeting(event: str) -> dict | None:
	_check_meeting_access(event)
	return meetings.complete_meeting(event)


@frappe.whitelist(methods=["POST"])
def reopen_meeting(event: str) -> dict | None:
	_check_meeting_access(event)
	return meetings.reopen_meeting(event)


def _check_meeting_access(event: str):
	"""Whoever may act on the meeting's Lead/Deal may complete it; else the Event's own rule."""
	row = frappe.db.get_value(
		meetings.EVENT_DOCTYPE, event, ["reference_doctype", "reference_docname"], as_dict=True
	)
	if not row:
		frappe.throw(frappe._("Meeting {0} does not exist.").format(event), frappe.DoesNotExistError)
	if row.reference_doctype and row.reference_docname:
		frappe.has_permission(row.reference_doctype, "write", row.reference_docname, throw=True)
	else:
		frappe.get_doc(meetings.EVENT_DOCTYPE, event).check_permission("write")

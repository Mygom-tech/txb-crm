"""Asking an Admin for a record.

A non-Admin cannot change an owner (see `crm.txb.ownership`), so this is how they ask.
It raises exactly one CRM Task carrying everything the Admin needs to decide, and touches
nothing else -- no owner change, no conversion. The Admin opens the record, changes the
owner themselves and closes the task.
"""

import frappe
from frappe import _
from frappe.utils import get_url_to_form, now_datetime

from crm.txb.constants import ADMIN_ROLE, OWNER_FIELDS
from crm.txb.ownership import owner_field
from crm.txb.permissions import is_admin

CLOSED_TASK_STATUSES = ("Done", "Canceled")

# Fields worth naming in the task body when the doctype has them.
CONTEXT_FIELDS = ("pipeline_type", "status")


@frappe.whitelist()
def request_claim(doctype: str, name: str, requested_owner: str, reason: str) -> dict:
	"""Raise a Claim Request task, or return the requester's open one.

	Returns {"created": bool, "task": str, "message": str}.
	"""
	if doctype not in OWNER_FIELDS:
		frappe.throw(
			_("{0} records do not have an owner to claim.").format(_(doctype)),
			frappe.ValidationError,
		)

	if is_admin():
		frappe.throw(
			_("You can change the owner directly, so there is nothing to request."),
			frappe.ValidationError,
		)

	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("Say why you are claiming this record."), frappe.ValidationError)

	if not frappe.has_permission(doctype, "read", name):
		frappe.throw(
			_("You do not have access to this record."), frappe.PermissionError
		)

	requester = frappe.session.user

	existing = open_request_for(doctype, name, requester)
	if existing:
		return {
			"created": False,
			"task": existing,
			"message": _("You already have an open request for this record."),
		}

	doc = frappe.get_doc(doctype, name)
	task = frappe.get_doc(
		{
			"doctype": "CRM Task",
			"title": _("Claim Request: {0}").format(record_label(doc)),
			"description": describe(doc, requester, requested_owner, reason),
			"status": "Todo",
			"priority": "Medium",
			"assigned_to": approver(),
			"reference_doctype": doctype,
			"reference_docname": name,
			"custom_claim_requested_by": requester,
			"custom_claim_requested_owner": requested_owner or requester,
		}
	)
	# The requester is asking precisely because they cannot write to this record, so the
	# task is created on their behalf rather than under their permissions.
	task.insert(ignore_permissions=True)

	return {
		"created": True,
		"task": task.name,
		"message": _("Your request has been sent to an Admin."),
	}


def open_request_for(doctype: str, name: str, requester: str) -> str | None:
	"""The requester's still-open task for this record, if any.

	Scoped to the requester deliberately: two salesmen may both want the same deal, and the
	Admin should see both cases. The ticket only forbids one person asking twice.
	"""
	return frappe.db.get_value(
		"CRM Task",
		{
			"reference_doctype": doctype,
			"reference_docname": name,
			"custom_claim_requested_by": requester,
			"status": ("not in", CLOSED_TASK_STATUSES),
		},
		"name",
	)


def approver() -> str:
	"""Who receives Claim Request tasks.

	The setting keeps this tied to the Admin role rather than to one person. A blank
	setting degrades to the longest-standing Admin rather than breaking the request.
	"""
	configured = frappe.db.get_single_value("FCRM Settings", "custom_claim_approver")
	if configured and frappe.db.get_value("User", configured, "enabled"):
		return configured

	fallback = frappe.get_all(
		"Has Role",
		filters={"role": ADMIN_ROLE, "parenttype": "User"},
		pluck="parent",
		order_by="creation asc",
	)
	for user in fallback:
		if user != "Administrator" and frappe.db.get_value("User", user, "enabled"):
			frappe.logger().warning(
				f"[request_claim] No Claim Request Approver configured; falling back to {user}"
			)
			return user

	frappe.throw(
		_("No Claim Request Approver is configured and no Admin user was found."),
		frappe.ValidationError,
	)


def record_label(doc) -> str:
	"""A human name for the record, whatever doctype it is."""
	for fieldname in ("lead_name", "organization", "full_name", "name"):
		if doc.meta.has_field(fieldname) and doc.get(fieldname):
			return doc.get(fieldname)
	return doc.name


def describe(doc, requester: str, requested_owner: str, reason: str) -> str:
	"""Everything the Admin needs to decide, without opening anything else first."""
	field = owner_field(doc.doctype)
	current = (doc.get(field) if field and doc.meta.has_field(field) else "") or _("Unassigned")

	lines = [
		_("<b>{0}</b> is asking to own this record.").format(requester),
		"",
		_("Requester: {0}").format(requester),
		_("Requested owner: {0}").format(requested_owner or requester),
		_("Record: {0} - {1}").format(_(doc.doctype), record_label(doc)),
		_("Link: {0}").format(get_url_to_form(doc.doctype, doc.name)),
		_("Current owner: {0}").format(current),
	]

	for fieldname in CONTEXT_FIELDS:
		if doc.meta.has_field(fieldname) and doc.get(fieldname):
			label = doc.meta.get_label(fieldname)
			lines.append(f"{_(label)}: {doc.get(fieldname)}")

	lines += [
		_("Requested at: {0}").format(now_datetime()),
		"",
		_("Reason: {0}").format(reason),
		"",
		_("The owner has not been changed. Open the record and set it yourself, then close this task."),
	]

	return "<br>".join(lines)

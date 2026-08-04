"""Who may move a deal between pipeline states.

The rule lives here rather than in the UI because every status-writing path -- the status
field, Kanban drag, Take Action wizards and direct REST calls -- ends up in
`CRMDeal.validate()`. Guarding there closes all of them at once, including the API.
"""

import frappe
from frappe import _

from crm.txb.constants import ADMIN_ROLE, PIPELINE_DELIVERING_COACHING, STATUS_FIELDS

# Pipelines whose status may only be changed by particular roles.
# Everything else stays governed by the ordinary document permissions.
PIPELINE_STATUS_ROLES = {
	PIPELINE_DELIVERING_COACHING: (ADMIN_ROLE,),
}


def can_change_status(pipeline_type: str | None, user: str | None = None) -> bool:
	"""Whether `user` may change the status of a deal in `pipeline_type`."""
	required_roles = PIPELINE_STATUS_ROLES.get(pipeline_type)
	if not required_roles:
		return True

	user = user or frappe.session.user
	if user == "Administrator":
		return True

	return bool(set(required_roles) & set(frappe.get_roles(user)))


def guard_status_change(doc, method=None):
	"""Reject a restricted status change made by a user without the required role.

	Only changes are guarded, never inserts. Coach flows legitimately *create* Delivering
	Coaching deals (an Individual Session or Workshop that is won spawns one with status
	"Submitted"), and blocking that would break the daily work this rule is meant to leave
	alone.
	"""
	if doc.is_new():
		return

	if can_change_status(doc.pipeline_type):
		return

	changed = [
		fieldname
		for fieldname in STATUS_FIELDS
		if doc.meta.has_field(fieldname) and doc.has_value_changed(fieldname)
	]
	if not changed:
		return

	frappe.throw(
		_("Only users with the {0} role can change the status of a {1} opportunity.").format(
			_("Admin"), _(doc.pipeline_type)
		),
		frappe.PermissionError,
		title=_("Not permitted"),
	)

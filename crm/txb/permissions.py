"""Who may move a deal between pipeline states.

The rule lives here rather than in the UI because every status-writing path -- the status
field, Kanban drag, Take Action wizards and direct REST calls -- ends up in
`CRMDeal.validate()`. Guarding there closes all of them at once, including the API.
"""

import frappe
from frappe import _

from crm.txb.constants import ADMIN_ROLE, PIPELINE_DELIVERING_COACHING, STATUS_FIELDS
from crm.txb.pipelines.actions import PIPELINE_ACTIONS
from crm.txb.pipelines.transitions import is_allowed

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


def is_admin(user: str | None = None) -> bool:
	"""The CRM's "Admin" role, plus the Administrator account itself."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True

	return ADMIN_ROLE in frappe.get_roles(user)


def guard_transition(doc, method=None):
	"""Reject a status change that the pipeline's state machine does not describe.

	Two things are enforced, in the order a user would want to hear them:

	1. the edge must exist -- you cannot jump from Submitted to Session Run;
	2. the write must come from `execute_action` -- because a *legal* edge written bare
	   skips the handler, and a deal reaching "Session Set" with no BAP type, no date and
	   no note is exactly the inconsistency this ticket exists to remove.

	Admins are exempt from both. That is the documented recovery hatch (TXB-110 decision
	2): without it, a mis-clicked "Not Interested" would need a database edit.

	Inserts are exempt, as in `guard_status_change` -- won sessions and workshops spawn
	Delivering Coaching deals, and blocking that breaks the handover.
	"""
	if doc.is_new():
		return

	if not doc.has_value_changed("status"):
		return

	# A pipeline with no registered actions has no state machine to enforce. Stock deals
	# and any future pipeline must keep working untouched.
	if not PIPELINE_ACTIONS.get(doc.pipeline_type):
		return

	if is_admin():
		return

	previous = doc.get_doc_before_save()
	from_status = previous.status if previous else None

	if not is_allowed(doc.pipeline_type, from_status, doc.status):
		frappe.throw(
			_('A {0} opportunity cannot move from "{1}" to "{2}".').format(
				_(doc.pipeline_type), _(from_status or ""), _(doc.status or "")
			),
			frappe.ValidationError,
			title=_("Transition not allowed"),
		)

	if not frappe.flags.get("txb_action"):
		frappe.throw(
			_('Change the status of a {0} opportunity through Take Action, so the details that go with the change are recorded.').format(
				_(doc.pipeline_type)
			),
			frappe.ValidationError,
			title=_("Use Take Action"),
		)

"""Who may move a deal between pipeline states, and who may write restricted fields.

The rules live here rather than in the UI because every write path -- the status field,
the side panel, the all-fields modal, list bulk edit, Kanban drag, Take Action wizards and
direct REST calls -- ends up in `CRMDeal.validate()`. Guarding there closes all of them at
once, including the API.
"""

import frappe
from frappe import _

from crm.txb.constants import (
	ADMIN_ROLE,
	FIELD_DELIVERY_COACH,
	PIPELINE_DELIVERING_COACHING,
	STATUS_FIELDS,
)
from crm.txb.pipelines.actions import PIPELINE_ACTIONS
from crm.txb.pipelines.transitions import is_allowed

# Pipelines whose status may only be changed by particular roles.
# Everything else stays governed by the ordinary document permissions.
PIPELINE_STATUS_ROLES = {
	PIPELINE_DELIVERING_COACHING: (ADMIN_ROLE,),
}

# Fields only an Admin may write, per doctype. Deliberately a table rather than one
# hard-coded check: the next "only an Admin may set this" field is then a one-line change
# that inherits the guard, the read-only rendering and the tests together.
#
# `custom_delivery_coach` decides who delivers the coaching, so it is an assignment
# decision like ownership -- see `crm.txb.ownership`, which guards the commission-bearing
# owner fields the same way. `custom_delivery_coach_name` is not listed: it is derived by
# `sync_delivery_coach_name` from this field, so guarding the source covers it.
ADMIN_ONLY_FIELDS = {
	"CRM Deal": (FIELD_DELIVERY_COACH,),
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


def admin_only_fields(doctype: str) -> tuple[str, ...]:
	"""The fields on `doctype` that only an Admin may write."""
	return ADMIN_ONLY_FIELDS.get(doctype, ())


def guard_admin_only_fields(doc, method=None):
	"""Refuse a write to an Admin-only field by anyone else.

	Unlike the status guards, **inserts are included**. Supplying the field at creation is
	the same act as changing it, and nothing legitimate does: neither the won-session
	handover (`crm.txb.pipelines.common`) nor the guest registration endpoint sets a
	delivery coach, and the field is not on the Quick Entry layout, so the create modal
	never offers it. A non-Admin insert that simply omits the field is untouched.

	This is the boundary. `restrict_admin_only_field` only stops the UI from inviting the
	edit -- rendering a field read-only enforces nothing, which is exactly how the
	`Lead Owner Read-Only` Form Script protected nothing for years.
	"""
	fields = admin_only_fields(doc.doctype)
	if not fields:
		return

	if is_admin():
		return

	blocked = [
		fieldname
		for fieldname in fields
		if doc.meta.has_field(fieldname)
		and (doc.get(fieldname) if doc.is_new() else doc.has_value_changed(fieldname))
	]
	if not blocked:
		return

	frappe.throw(
		_("Only an Admin can change {0}.").format(
			", ".join(_(doc.meta.get_label(fieldname)) for fieldname in blocked)
		),
		frappe.PermissionError,
		title=_("Not permitted"),
	)


def restrict_admin_only_field(field, doctype: str, parent_doctype: str | None = None):
	"""Render an Admin-only field read-only for everyone else.

	Cosmetic; `guard_admin_only_fields` enforces. Mirrors `restrict_owner_field` and is
	called from the same two places in `crm_fields_layout`, so one call covers the desktop
	pages, the mobile pages, the all-fields modal and the side panel.
	"""
	if field.get("fieldname") not in admin_only_fields(doctype):
		return

	if is_admin():
		return

	field.read_only = 1


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

	if frappe.flags.get("txb_action") != doc.name:
		frappe.throw(
			_(
				"Change the status of a {0} opportunity through Take Action, so the "
				"details that go with the change are recorded."
			).format(_(doc.pipeline_type)),
			frappe.ValidationError,
			title=_("Use Take Action"),
		)

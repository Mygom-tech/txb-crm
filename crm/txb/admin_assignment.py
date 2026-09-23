"""Who receives a system-generated Admin task.

One authority, one setting, no person named in source. Automation that raises a CRM Task
for "the Admin" -- Claim Requests (TXB-106), the new Delivering Coaching deal task
(TXB-208) -- asks `resolve_admin_task_assignee()` and gets a single User back, because
`CRM Task.assigned_to` holds one User.

The setting is a preference, never a guarantee: the configured person can be disabled or
stripped of the Admin role long after they were chosen, so eligibility is re-checked at
use time rather than trusted from the save. When the setting cannot be used the resolver
degrades to a deterministic eligible Admin and says so in the log; only an installation
with no eligible Admin at all is a configuration error.
"""

import frappe
from frappe import _

from crm.txb.constants import ADMIN_ROLE

SETTINGS_DOCTYPE = "FCRM Settings"
ADMIN_TASK_ASSIGNEE_FIELD = "custom_admin_task_assignee"

# Frappe's two built-in accounts. "Administrator" is the break-glass superuser and Guest is
# the anonymous session; neither is a person who works a task list.
SPECIAL_USERS = ("Administrator", "Guest")


def is_eligible_admin(user: str | None) -> bool:
	"""A real, enabled person who currently holds the Admin role."""
	if not user or user in SPECIAL_USERS:
		return False
	if not frappe.db.get_value("User", user, "enabled"):
		return False
	return bool(
		frappe.db.exists(
			"Has Role", {"parent": user, "parenttype": "User", "role": ADMIN_ROLE}
		)
	)


def eligible_admins() -> list[str]:
	"""Every eligible Admin, longest-standing first.

	Ordered by User creation then ID so the fallback is stable across calls and sites --
	two accounts created in the same instant still resolve the same way every time.
	"""
	holders = frappe.get_all(
		"Has Role",
		filters={"role": ADMIN_ROLE, "parenttype": "User"},
		pluck="parent",
	)
	candidates = [user for user in set(holders) if user not in SPECIAL_USERS]
	if not candidates:
		return []

	return frappe.get_all(
		"User",
		filters={"name": ("in", candidates), "enabled": 1},
		pluck="name",
		order_by="creation asc, name asc",
	)


def configured_admin_task_assignee() -> str | None:
	"""The setting's value, or None when the field is not installed yet."""
	if not frappe.get_meta(SETTINGS_DOCTYPE).has_field(ADMIN_TASK_ASSIGNEE_FIELD):
		return None
	return frappe.db.get_single_value(SETTINGS_DOCTYPE, ADMIN_TASK_ASSIGNEE_FIELD)


def resolve_admin_task_assignee() -> str:
	"""The User a system-generated Admin task is assigned to.

	Raises `frappe.ValidationError` when the site has no eligible Admin, which is a setup
	problem rather than something automation can paper over.
	"""
	configured = configured_admin_task_assignee()
	if configured and is_eligible_admin(configured):
		return configured

	candidates = eligible_admins()
	if candidates:
		fallback = candidates[0]
		reason = (
			f"configured Admin Task Assignee {configured} is no longer an enabled Admin"
			if configured
			else "no Admin Task Assignee is configured"
		)
		frappe.logger().warning(
			f"[admin_assignment] {reason}; falling back to {fallback}"
		)
		return fallback

	frappe.throw(
		_("No Admin Task Assignee is configured and no eligible Admin user was found."),
		frappe.ValidationError,
	)


def validate_admin_task_assignee(doc, method=None):
	"""Refuse a setting that names someone who cannot work the tasks.

	Blank is allowed -- that is the documented "let the resolver pick" state.
	"""
	assignee = doc.get(ADMIN_TASK_ASSIGNEE_FIELD)
	if not assignee:
		return

	if assignee in SPECIAL_USERS:
		frappe.throw(
			_("{0} cannot be the Admin Task Assignee. Choose a real Admin user.").format(
				assignee
			),
			frappe.ValidationError,
		)

	if not frappe.db.get_value("User", assignee, "enabled"):
		frappe.throw(
			_("{0} is disabled and cannot be the Admin Task Assignee.").format(assignee),
			frappe.ValidationError,
		)

	if not is_eligible_admin(assignee):
		frappe.throw(
			_("{0} does not have the Admin role and cannot be the Admin Task Assignee.").format(
				assignee
			),
			frappe.ValidationError,
		)

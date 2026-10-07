"""Team Activity endpoints (TXB-286).

Admin-only: the figures cover every team member, so unlike the CRM dashboard, which Sales Managers
may read across users, these are refused to every sales role. The guard runs before any argument
is validated or any metric is queried.
"""

import frappe
from frappe import _

from crm.txb import team_activity
from crm.txb.permissions import is_admin


@frappe.whitelist()
def summary(from_date: str, to_date: str, member: str | None = None) -> dict:
	_only_admins()
	return team_activity.summary(from_date, to_date, member)


@frappe.whitelist()
def members(from_date: str, to_date: str) -> dict:
	_only_admins()
	return team_activity.members(from_date, to_date)


@frappe.whitelist()
def records(metric: str, from_date: str, to_date: str, member: str | None = None, page: int = 1) -> dict:
	_only_admins()
	return team_activity.records(metric, from_date, to_date, member, page)


def _only_admins():
	# Not `frappe.only_for`: it lets every user through under tests, which would hide a regression.
	if not is_admin():
		frappe.throw(_("Only an Admin can view Team Activity."), frappe.PermissionError)

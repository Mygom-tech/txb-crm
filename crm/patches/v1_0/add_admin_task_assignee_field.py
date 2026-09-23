"""Install the Admin Task Assignee setting and carry the old Claim Approver over (TXB-263).

The claim-specific Claim Approver is no longer the authority for system-generated Admin
tasks, but whoever a site pointed it at is still the person they chose, so the value is
copied across once -- and only when the new setting is still empty, so an administrator
who has already configured the new field is never overwritten. A copied value must satisfy
the same rules as a freshly chosen one; a stale claim approver who has since been disabled
or lost the Admin role is left behind for the resolver's fallback to handle.

The legacy field itself is left in place: nothing is dropped here.
"""

import frappe

from crm.install import add_admin_task_assignee_custom_field
from crm.txb.admin_assignment import ADMIN_TASK_ASSIGNEE_FIELD, is_eligible_admin

LEGACY_FIELD = "custom_claim_approver"


def execute():
	add_admin_task_assignee_custom_field()
	frappe.clear_cache(doctype="FCRM Settings")
	copy_legacy_claim_approver()


def copy_legacy_claim_approver():
	if frappe.db.get_single_value("FCRM Settings", ADMIN_TASK_ASSIGNEE_FIELD):
		return

	if not frappe.get_meta("FCRM Settings").has_field(LEGACY_FIELD):
		return

	legacy = frappe.db.get_single_value("FCRM Settings", LEGACY_FIELD)
	if not legacy or not is_eligible_admin(legacy):
		return

	frappe.db.set_single_value("FCRM Settings", ADMIN_TASK_ASSIGNEE_FIELD, legacy)

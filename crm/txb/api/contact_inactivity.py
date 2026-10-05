"""Contact inactivity for the Contact page and the Admin's owner exceptions (TXB-281).

The cycle rows themselves are readable only by a System Manager, so these endpoints check the
caller first and then read the rows directly: anyone who may read a Contact sees its status, and
only an Admin sees the list of Due cycles that cannot be reminded for lack of an enabled owner.
"""

import frappe
from frappe import _

from crm.txb.constants import OWNER_FIELDS
from crm.txb.contact_inactivity import (
	CONTACT_DOCTYPE,
	CYCLE_DOCTYPE,
	STATUS_CLOSED,
	STATUS_SETTLED,
	interval_minutes,
)
from crm.txb.contact_inactivity_reminders import EXCEPTION_DISABLED_OWNER, EXCEPTION_UNOWNED
from crm.txb.permissions import is_admin

CYCLE_FIELDS = ["anchor_at", "anchor_source", "due_at", "status", "reminder_task", "exception"]


@frappe.whitelist()
def get_contact_inactivity(contact: str) -> dict:
	"""The Contact's latest cycle, or every field None when it has none yet."""
	if not contact or not frappe.has_permission(CONTACT_DOCTYPE, "read", contact):
		frappe.throw(_("You do not have access to this Contact."), frappe.PermissionError)

	cycle = (
		frappe.db.get_value(
			CYCLE_DOCTYPE, {"contact": contact}, CYCLE_FIELDS, as_dict=True, order_by="cycle_no desc"
		)
		or {}
	)
	return {
		"anchor_at": cycle.get("anchor_at"),
		"anchor_source": cycle.get("anchor_source"),
		"due_on": cycle.get("due_at"),
		"status": cycle.get("status"),
		"reminder_task": cycle.get("reminder_task"),
		"exception": cycle.get("exception") or None,
		"interval_minutes": interval_minutes(),
	}


@frappe.whitelist()
def list_owner_exceptions() -> list[dict]:
	"""Each Contact whose latest cycle is still live but has no enabled owner to remind."""
	if not is_admin():
		frappe.throw(_("Only an Admin can list owner exceptions."), frappe.PermissionError)

	cycle = frappe.qb.DocType(CYCLE_DOCTYPE)
	newer = frappe.qb.DocType(CYCLE_DOCTYPE).as_("newer")
	contact = frappe.qb.DocType(CONTACT_DOCTYPE)
	return (
		frappe.qb.from_(cycle)
		.left_join(newer)
		.on((newer.contact == cycle.contact) & (newer.cycle_no > cycle.cycle_no))
		.left_join(contact)
		.on(contact.name == cycle.contact)
		.select(
			cycle.name.as_("cycle"),
			cycle.contact,
			contact.full_name.as_("contact_name"),
			contact[OWNER_FIELDS[CONTACT_DOCTYPE]].as_("owner"),
			cycle.exception,
			cycle.exception_at,
			cycle.status,
			cycle.due_at.as_("due_on"),
		)
		.where(newer.name.isnull())
		.where(cycle.exception.isin([EXCEPTION_UNOWNED, EXCEPTION_DISABLED_OWNER]))
		.where(cycle.status.notin([STATUS_SETTLED, STATUS_CLOSED]))
		.orderby(cycle.due_at)
		.run(as_dict=True)
	)

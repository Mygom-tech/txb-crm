"""CRM Lead document events.

Ported from the `Auto Assign Lead Owner`, `Protect Lead Owner`,
`Require Disqualified Reason` and `Prevent Duplicate Lead` Server Scripts.
Behaviour is preserved exactly; see specs/server-script-migration.md for known issues
that were deliberately carried over rather than fixed here.
"""

import frappe
from frappe import _

STATUS_DISQUALIFIED = "Disqualified"
DEFAULT_DISQUALIFIED_REASON = "Pending Review"
ADMINISTRATOR = "Administrator"
SYSTEM_MANAGER = "System Manager"


def protect_owner(doc, method=None):
	"""Keep non-privileged users from reassigning an existing lead."""
	if doc.is_new():
		return

	previous_owner = frappe.db.get_value("CRM Lead", doc.name, "lead_owner")
	if not previous_owner or previous_owner == doc.lead_owner:
		return

	if is_privileged():
		return

	doc.lead_owner = previous_owner
	frappe.msgprint(_("Only Administrators can change the Lead Owner."), alert=True)


def is_privileged(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == ADMINISTRATOR:
		return True

	return bool(frappe.db.exists("Has Role", {"parent": user, "role": SYSTEM_MANAGER}))


def default_disqualified_reason(doc, method=None):
	"""A disqualified lead always carries a reason, so reporting never sees a blank."""
	if doc.status == STATUS_DISQUALIFIED and not doc.lost_reason:
		doc.lost_reason = DEFAULT_DISQUALIFIED_REASON


def prevent_duplicate(doc, method=None):
	"""Reject a lead when a Contact or Lead already has the same name and email.

	All three of first name, last name and email must match. Leads without a first name
	or without an email are not checked, matching the original script.
	"""
	first_name = (doc.first_name or "").strip()
	last_name = (doc.last_name or "").strip()
	email = (doc.email or "").strip()

	if not first_name or not email:
		return

	# `("is", "not set")` is how the original expressed "last name is empty".
	last_name_filter = last_name if last_name else ("is", "not set")

	duplicate_in = None
	if frappe.db.count(
		"Contact", filters={"first_name": first_name, "last_name": last_name_filter, "email_id": email}
	):
		duplicate_in = "Contact"
	elif frappe.db.count(
		"CRM Lead", filters={"first_name": first_name, "last_name": last_name_filter, "email": email}
	):
		duplicate_in = "Lead"

	if not duplicate_in:
		return

	full_name = f"{first_name} {last_name}".strip()
	frappe.throw(
		_("A {0} with the name <b>{1}</b> and email <b>{2}</b> already exists. Cannot create a duplicate lead.").format(
			duplicate_in, full_name, email
		),
		title=_("Duplicate Found"),
	)

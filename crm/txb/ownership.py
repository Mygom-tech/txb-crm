"""Who owns a Lead, Contact or Opportunity, and who may change that.

Ownership decides commission, so the rule lives on the document lifecycle rather than in
any one screen. Every write path -- the side panel, list bulk edit, Kanban,
`frappe.client.set_value` and raw REST -- reaches `before_insert` and `validate`, so
guarding there closes all of them at once. The same reasoning put the status rules in
`crm.txb.permissions`.

Also governs `crm.txb.pipelines.common.create_coaching_deal`, where a won Individual
Session or Workshop spawns a Delivering Coaching deal -- the coach who ran the winning
action owns the new deal.

Replaces the `Auto Assign Lead Owner` and `Protect Lead Owner` Server Scripts and the
`Lead Owner Read-Only` Form Script, which enforced nothing -- it injected CSS.
"""

import frappe
from frappe import _

from crm.txb.constants import OWNER_FIELDS
from crm.txb.permissions import is_admin

GUEST = "Guest"


def owner_field(doctype: str) -> str | None:
	"""The owner fieldname for `doctype`, or None if it has no owner concept."""
	return OWNER_FIELDS.get(doctype)


def claim_owner_on_insert(doc, method=None):
	"""Own every new record as the creating user.

	Covers direct creation, Lead to Contact, and Contact to Deal, where the converting
	user is the session user and no owner reaches this hook pre-populated.

	Lead to Deal is the exception until TXB-106 Task 5 lands: `CRMLead.create_deal`
	copies `lead_owner` onto `deal_owner` through `LEAD_DEAL_FIELD_MAP` before insert,
	so for an *Admin* converter the field is already truthy and reads here as a
	deliberate nomination -- leaving the deal with the lead's owner rather than the
	Admin's. A non-Admin converter is overwritten as intended. Task 5 empties that map,
	which removes the seam for both.
	"""
	field = owner_field(doc.doctype)
	if not field or not doc.meta.has_field(field):
		return

	# Guest is never a legitimate owner. The public registration endpoint runs as Guest
	# and sets `deal_owner` deliberately, carrying it over from the source deal; leaving
	# that alone here is what keeps the flow correct without a flag to plumb through.
	if frappe.session.user == GUEST:
		return

	# An Admin may hand a new record to someone else. Everyone else owns what they create,
	# whatever the client sent -- a non-Admin cannot nominate an owner at creation either.
	if is_admin() and doc.get(field):
		return

	doc.set(field, frappe.session.user)


def guard_owner_change(doc, method=None):
	"""Refuse an owner change made by anyone but an Admin.

	Inserts are exempt -- `claim_owner_on_insert` has already decided the initial owner,
	and the two rules would otherwise contradict each other.

	This fires for unowned records too. That is the requirement, and the hole in the
	script it replaces: `protect_owner` returned early when there was no previous owner,
	so the first person to touch an unassigned record could claim it.
	"""
	if doc.is_new():
		return

	field = owner_field(doc.doctype)
	if not field or not doc.meta.has_field(field):
		return

	if not doc.has_value_changed(field):
		return

	if is_admin():
		return

	frappe.throw(
		_("Only an Admin can change the owner. Use Request Ownership to ask for this record."),
		frappe.PermissionError,
		title=_("Not permitted"),
	)

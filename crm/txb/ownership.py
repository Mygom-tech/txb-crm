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

	Covers direct creation, Lead to Contact, Contact to Deal, and Lead to Deal, where the
	converting user is the session user and no owner reaches this hook pre-populated.

	Lead to Deal used to be the exception: `CRMLead.create_deal` copied `lead_owner` onto
	`deal_owner` through `LEAD_DEAL_FIELD_MAP` before insert, so for an *Admin* converter
	the field was already truthy and read here as a deliberate nomination -- leaving the
	deal with the lead's owner rather than the Admin's. A non-Admin converter was
	overwritten as intended. TXB-106 Task 5 emptied that map, closing the seam for both.
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

	This fires for unowned records too. That is the requirement, and it closes the hole in
	`protect_owner`, the CRM Lead-only rule this replaces: it returned early when there was
	no previous owner, so the first person to touch an unassigned lead could claim it.
	Contact and CRM Deal had no owner protection at all.
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


def restrict_owner_field(field, doctype: str, parent_doctype: str | None = None):
	"""Render the owner read-only for anyone who cannot change it.

	Cosmetic only -- `guard_owner_change` is the boundary. This exists so the field does
	not invite an edit the server will refuse, and it replaces a Form Script that injected
	CSS to fake the same effect while enforcing nothing.

	Called beside `handle_perm_level_restrictions`, which is the existing hook for exactly
	this, so one call covers the desktop pages, the mobile pages, the all-fields modal and
	the Quick Entry creation modals.

	CRM Deal's deal_owner also carries permlevel 1 on this site, so
	handle_perm_level_restrictions already hides it from plain Sales Users. This rule is
	still needed: lead_owner and custom_contact_owner are permlevel 0 and unprotected,
	and permlevel keys on whoever holds a permlevel-1 DocPerm rather than on ADMIN_ROLE,
	which is the rule the ticket states.
	"""
	if field.get("fieldname") != owner_field(doctype):
		return

	if is_admin():
		return

	field.read_only = 1

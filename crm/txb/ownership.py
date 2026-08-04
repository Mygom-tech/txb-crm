"""Who owns a Lead, Contact or Opportunity, and who may change that.

Ownership decides commission, so the rule lives on the document lifecycle rather than in
any one screen. Every write path -- the side panel, list bulk edit, Kanban,
`frappe.client.set_value` and raw REST -- reaches `before_insert` and `validate`, so
guarding there closes all of them at once. The same reasoning put the status rules in
`crm.txb.permissions`.

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

	Covers direct creation and all three conversions -- Lead to Contact, Lead to Deal and
	Contact to Deal -- because in each the converting user is the session user.
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

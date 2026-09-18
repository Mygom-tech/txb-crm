"""Optional Opportunity link on Contact-owned CRM Call Logs and FCRM Notes (TXB-248).

A call or note recorded against a Contact keeps the Contact as its canonical owner
(reference_doctype/reference_docname). It may additionally name one Opportunity through the
nullable `opportunity` field so the same canonical record also surfaces in that Opportunity's
history -- it is read there, never copied. The link is only meaningful for a Contact-owned
record, and the selected Opportunity must exist, list the referenced Contact among its
contacts, and be readable by the acting user. Lead- and Deal-owned records are untouched.
"""

import frappe
from frappe import _

CONTACT_DOCTYPE = "Contact"
DEAL_DOCTYPE = "CRM Deal"
FIELD_OPPORTUNITY = "opportunity"


def validate_opportunity_link(doc, method=None):
	"""Reject an Opportunity link that is not consistent with the Contact owner.

	Only re-checked when the link (or its owner) is set or changed, so a later edit of an
	already-valid record by a user without read access to the Opportunity is not blocked.
	"""
	opportunity = doc.get(FIELD_OPPORTUNITY)
	if not opportunity:
		return

	if not (
		doc.is_new()
		or doc.has_value_changed(FIELD_OPPORTUNITY)
		or doc.has_value_changed("reference_doctype")
		or doc.has_value_changed("reference_docname")
	):
		return

	if doc.reference_doctype != CONTACT_DOCTYPE or not doc.reference_docname:
		frappe.throw(
			_("An Opportunity can only be linked to a {0} recorded against a Contact").format(
				_(doc.doctype)
			),
			frappe.ValidationError,
		)

	if not frappe.db.exists(DEAL_DOCTYPE, opportunity):
		frappe.throw(
			_("Opportunity {0} does not exist").format(frappe.bold(opportunity)),
			frappe.LinkValidationError,
		)

	if not frappe.has_permission(DEAL_DOCTYPE, "read", opportunity):
		frappe.throw(
			_("You do not have access to Opportunity {0}").format(frappe.bold(opportunity)),
			frappe.PermissionError,
		)

	if not frappe.db.exists(
		"CRM Contacts",
		{"parenttype": DEAL_DOCTYPE, "parent": opportunity, "contact": doc.reference_docname},
	):
		frappe.throw(
			_("Opportunity {0} is not linked to Contact {1}").format(
				frappe.bold(opportunity), frappe.bold(doc.reference_docname)
			),
			frappe.ValidationError,
		)

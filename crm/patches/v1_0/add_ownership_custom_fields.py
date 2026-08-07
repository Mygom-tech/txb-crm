"""Install the Claim Request fields and seed the approver.

Kept separate from the flow itself so the fields exist before any code reads them.
"""

import frappe

from crm.install import add_ownership_custom_fields

DEFAULT_APPROVER = "kristina@txbconsulting.com"


def execute():
	add_ownership_custom_fields()
	seed_approver()


def seed_approver():
	"""Point the setting at the person doing this today.

	The ticket requires the permission be tied to the Admin role rather than to one
	person, which is what the setting achieves -- reassigning is a settings edit. Seeding
	it just means the flow works on day one.
	"""
	if frappe.db.get_single_value("FCRM Settings", "custom_claim_approver"):
		return

	if not frappe.db.exists("User", DEFAULT_APPROVER):
		return

	frappe.db.set_single_value("FCRM Settings", "custom_claim_approver", DEFAULT_APPROVER)

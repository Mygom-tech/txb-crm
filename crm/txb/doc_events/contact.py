"""Contact document events.

Ported from the `Sync Contact Organization` Server Script.
"""

import frappe

CONTACT_ORGANIZATION_LINK_FIELD = "custom_organization_link"


def sync_organization(doc, method=None):
	"""Keep `company_name` following the organization link.

	The CRM Organization page lists its contacts by matching `company_name` against the
	organization name, so the two must agree exactly. `custom_organization_link` is the
	source of truth; clearing it clears `company_name` too.
	"""
	if not frappe.get_meta("Contact").has_field(CONTACT_ORGANIZATION_LINK_FIELD):
		return

	organization = doc.get(CONTACT_ORGANIZATION_LINK_FIELD)

	if organization:
		if doc.company_name != organization:
			doc.company_name = organization
	elif doc.company_name:
		doc.company_name = None

"""Install the Landing Query field on CRM Lead and CRM Deal.

Holds the raw query string (?utm_campaign=…) of the public page a record registered from, so
campaigns can be filtered in the list views and exported. Same fieldname on both doctypes, so
Lead → Deal conversion copies it. Re-runs are a no-op.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from crm.txb.constants import FIELD_LANDING_QUERY

DOCTYPES = ("CRM Lead", "CRM Deal")
FIELD = {
	"fieldname": FIELD_LANDING_QUERY,
	"fieldtype": "Small Text",
	"label": "Landing Query",
	"description": "Query string of the landing page the record registered from.",
	"read_only": 1,
	"insert_after": "source",
}


def execute():
	for doctype in DOCTYPES:
		if not frappe.get_meta(doctype).has_field(FIELD_LANDING_QUERY):
			create_custom_fields({doctype: [FIELD]})
		frappe.clear_cache(doctype=doctype)

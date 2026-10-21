"""Index CRM Lead.converted_contact on sites whose field predates its `search_index`: every
Contact page reads its source Leads by it. A column already indexed is left as it is."""

import frappe

from crm.txb.constants import FIELD_CONVERTED_CONTACT


def execute():
	if frappe.get_meta("CRM Lead").has_field(FIELD_CONVERTED_CONTACT) and not frappe.db.get_column_index(
		"tabCRM Lead", FIELD_CONVERTED_CONTACT
	):
		frappe.db.add_index("CRM Lead", [FIELD_CONVERTED_CONTACT])

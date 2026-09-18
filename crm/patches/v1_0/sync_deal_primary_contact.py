"""Repair CRM Deal.contact from the canonical contacts table (TXB-250).

The scalar ``contact`` Link is what Kanban and list views read, but older write paths never
set it, leaving some Deals blank or pointing at a Contact that is no longer primary. Each
Deal's value is recomputed with the same rule the Deal lifecycle now applies (sole or
explicitly primary Contact row, else blank -- never a guess among several unmarked rows)
and written only when it differs. Writes use ``update_modified=False`` so no record looks
freshly edited. Re-running is a no-op.
"""

import frappe

from crm.fcrm.doctype.crm_deal.crm_deal import get_effective_primary_contact


def execute():
	rows_by_deal = {}
	for row in frappe.get_all(
		"CRM Contacts",
		filters={"parenttype": "CRM Deal", "parentfield": "contacts"},
		fields=["parent", "contact", "is_primary"],
		order_by="idx asc",
	):
		rows_by_deal.setdefault(row.parent, []).append(row)

	for deal in frappe.get_all("CRM Deal", fields=["name", "contact"]):
		expected = get_effective_primary_contact(rows_by_deal.get(deal.name)) or None
		if (deal.contact or None) != expected:
			frappe.db.set_value("CRM Deal", deal.name, "contact", expected, update_modified=False)

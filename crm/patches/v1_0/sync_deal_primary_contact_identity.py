"""Repair Deal identity fields from each Deal's effective primary Contact (TXB-252).

Deal ``first_name``, ``last_name``, ``email``, ``mobile_no`` and ``phone`` -- and the primary
``CRM Contacts`` row snapshot -- used to be copied once and then drift from the Contact. Each
Deal with an effective primary Contact (sole or explicitly primary row) is reconciled from the
live Contact through the same helper Contact saves use. Writes happen only where values differ
and use ``update_modified=False``, so no record looks freshly edited. Deals without an effective
primary Contact are left untouched. Re-running is a no-op.
"""

import frappe

from crm.fcrm.doctype.crm_deal.crm_deal import (
	DEAL_IDENTITY_FIELDS,
	get_deal_contact_rows,
	persist_primary_contact_identity,
)


def execute():
	rows_by_deal = get_deal_contact_rows(None)
	for deal in frappe.get_all("CRM Deal", fields=["name", *DEAL_IDENTITY_FIELDS]):
		rows = rows_by_deal.get(deal.name)
		if rows:
			persist_primary_contact_identity(deal, rows)

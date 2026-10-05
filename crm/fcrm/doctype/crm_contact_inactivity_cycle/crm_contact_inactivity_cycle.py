# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CRMContactInactivityCycle(Document):
	"""One period a Contact goes without human contact, from its anchor until it ends.

	Written only through `crm.txb.contact_inactivity`. UNIQUE(contact, cycle_no) numbers a
	Contact's cycles once each; the UNIQUE `live_key` (the Contact while the cycle is Open, Due or
	Reminded, NULL once Closed or Settled) keeps a Contact to at most one live cycle.
	"""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		anchor_at: DF.Datetime
		anchor_source: DF.Literal["History", "Rollout", "Creation"]
		contact: DF.Link
		cycle_no: DF.Int
		due_at: DF.Datetime
		ended_at: DF.Datetime | None
		live_key: DF.Data | None
		reminded_at: DF.Datetime | None
		status: DF.Literal["Open", "Due", "Reminded", "Closed", "Settled"]
	# end: auto-generated types


def on_doctype_update():
	frappe.db.add_unique("CRM Contact Inactivity Cycle", ["contact", "cycle_no"])

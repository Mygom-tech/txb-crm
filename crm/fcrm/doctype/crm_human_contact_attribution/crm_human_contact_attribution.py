# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CRMHumanContactAttribution(Document):
	"""One Contact evidenced as the other party of one CRM Human Contact Event.

	Written only through `crm.txb.contact_attribution.attribute_event`; UNIQUE(event, contact)
	keeps a repeated or concurrent attribution to one row per pair.
	"""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		contact: DF.Link
		event: DF.Link
		evidence: DF.Literal["Direct", "Email Match", "Phone Match", "Participant"]
		occurred_at: DF.Datetime
	# end: auto-generated types


def on_doctype_update():
	frappe.db.add_unique("CRM Human Contact Attribution", ["event", "contact"])

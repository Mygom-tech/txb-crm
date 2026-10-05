# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CRMHumanContactEvent(Document):
	"""One server-verified human contact, keyed by its source document.

	Written only through `crm.txb.human_contact.sync_source`, which upserts by the UNIQUE
	`source_key` and sets `voided` when the source stops qualifying, so a source never owns more
	than one row and a retracted contact stays auditable.
	"""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		actor: DF.Link | None
		channel: DF.Literal["Email", "WhatsApp", "Call", "Coaching Call", "Meeting"]
		occurred_at: DF.Datetime
		provenance: DF.Literal["Human"]
		recipients: DF.JSON
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		source_doctype: DF.Link
		source_key: DF.Data
		source_name: DF.DynamicLink
		voided: DF.Check
	# end: auto-generated types

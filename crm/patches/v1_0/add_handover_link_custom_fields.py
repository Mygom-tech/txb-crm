"""Install the TXB-126 sales <-> delivery handover link fields on CRM Deal.

Two dedicated, app-managed Link fields cross-reference a Won sales Opportunity and the one
aggregate Delivering Coaching Opportunity it hands over to:

- `custom_sales_source_deal` lives on the delivery deal and is UNIQUE. That database
  constraint is the whole idempotency guarantee -- at most one aggregate delivery deal may
  reference a given source -- and it is what lets `create_coaching_deal` recover a
  duplicate-key race instead of spawning a second handover.
- `custom_delivery_deal` lives on the source deal and points the other way.

Both are read-only so users navigate the link but do not hand-edit the relationship, and both
are Link(CRM Deal) so the existing Deal rendering surfaces them as clickable references.

Kept out of `custom_source_deal`, which Workshop QR registration writes on its separate
attendee candidates -- overloading it would defeat the uniqueness rule and blur the flows.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from crm.txb.constants import FIELD_DELIVERY_DEAL, FIELD_SALES_SOURCE_DEAL


# Declared in order so the reverse link is inserted after the sales-source link even when a
# prior partial run left only one of them installed.
FIELD_DEFINITIONS = [
	{
		"fieldname": FIELD_SALES_SOURCE_DEAL,
		"fieldtype": "Link",
		"options": "CRM Deal",
		"label": "Sales Source Opportunity",
		"description": "The Won sales Opportunity this Delivering Coaching handover was created from.",
		"read_only": 1,
		"unique": 1,
		"no_copy": 1,
		"insert_after": "pipeline_type",
	},
	{
		"fieldname": FIELD_DELIVERY_DEAL,
		"fieldtype": "Link",
		"options": "CRM Deal",
		"label": "Delivery Opportunity",
		"description": "The aggregate Delivering Coaching Opportunity this Won deal handed over to.",
		"read_only": 1,
		"no_copy": 1,
		"insert_after": FIELD_SALES_SOURCE_DEAL,
	},
]


def execute():
	"""Install whichever of the two handover links is missing, idempotently.

	Guarding per field (rather than on both together) makes a normal `migrate` safe after a
	partial run: an environment that already has one link but not the other -- or neither --
	converges on both without erroring, and a re-run with both present is a clean no-op.
	"""
	meta = frappe.get_meta("CRM Deal")
	missing = [field for field in FIELD_DEFINITIONS if not meta.has_field(field["fieldname"])]
	if not missing:
		return

	create_custom_fields({"CRM Deal": missing})

	frappe.clear_cache(doctype="CRM Deal")

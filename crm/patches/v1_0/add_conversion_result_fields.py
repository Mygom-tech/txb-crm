"""Install the TXB-132 Lead-conversion result fields on CRM Lead.

One Lead-to-Deal conversion is a single durable event; these three app-managed fields record
its outcome on the archived Lead so a retry can resolve to the same result rather than create
a second initial Opportunity:

- `converted_contact` -- the Contact the conversion created or reused.
- `converted_deal` -- the one initial Opportunity the conversion created. `convert_to_deal`
  reads this under a row lock and, when it still points at a live Opportunity, returns it
  instead of inserting another, which is the whole idempotency guarantee.
- `converted_at` -- when the conversion committed.

All three are read-only (users navigate the links but do not hand-edit the conversion record)
and `no_copy` (an amended/duplicated Lead must not inherit another Lead's conversion outcome).
Deliberately NOT unique on `converted_deal`: CRM Deal.lead stays non-unique so later
Opportunities may still reference the archived Lead for provenance (TXB-132 non-goal).
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from crm.txb.constants import (
	FIELD_CONVERTED_AT,
	FIELD_CONVERTED_CONTACT,
	FIELD_CONVERTED_DEAL,
)


# Declared in dependency order so each field's `insert_after` target already exists even when
# a prior partial run installed only some of them.
FIELD_DEFINITIONS = [
	{
		"fieldname": FIELD_CONVERTED_CONTACT,
		"fieldtype": "Link",
		"options": "Contact",
		"label": "Converted Contact",
		"description": "The Contact this Lead's conversion created or reused.",
		"read_only": 1,
		"no_copy": 1,
		"insert_after": "converted",
	},
	{
		"fieldname": FIELD_CONVERTED_DEAL,
		"fieldtype": "Link",
		"options": "CRM Deal",
		"label": "Converted Opportunity",
		"description": "The initial Opportunity this Lead's conversion created.",
		"read_only": 1,
		"no_copy": 1,
		"insert_after": FIELD_CONVERTED_CONTACT,
	},
	{
		"fieldname": FIELD_CONVERTED_AT,
		"fieldtype": "Datetime",
		"label": "Converted At",
		"description": "When this Lead was converted.",
		"read_only": 1,
		"no_copy": 1,
		"insert_after": FIELD_CONVERTED_DEAL,
	},
]


def execute():
	"""Install whichever conversion-result fields are missing, idempotently.

	Guarding per field (rather than on all together) makes a normal `migrate` safe after a
	partial run: an environment that already has some of the fields converges on all of them
	without erroring, and a re-run with every field present is a clean no-op.
	"""
	meta = frappe.get_meta("CRM Lead")
	missing = [field for field in FIELD_DEFINITIONS if not meta.has_field(field["fieldname"])]
	if not missing:
		return

	create_custom_fields({"CRM Lead": missing})

	frappe.clear_cache(doctype="CRM Lead")

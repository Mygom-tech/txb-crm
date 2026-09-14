"""Install the CRM Organization Company Code identity fields and backfill keys (TXB-243).

Two fields carry the contract (see ``crm.fcrm.doctype.crm_organization.company_code``):

- ``custom_company_code`` -- the user-entered value. It already exists on live sites as a
  Custom Field; created here only when missing so fresh installs and test sites converge.
- ``custom_company_code_key`` -- a hidden, read-only, UNIQUE normalized key. The database
  uniqueness is the race-safe backstop for concurrent writers. It starts entirely NULL (many
  NULLs are allowed under a unique index), so adding the constraint over legacy data is safe.

The backfill claims a key only for codes held by a single Organization; blank rows and
existing normalized collision groups are left untouched and reported (logged) for business
remediation. No value is ever invented and no collision group is arbitrarily resolved, so the
patch is idempotent -- a re-run only re-asserts the unambiguous keys already set.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from crm.fcrm.doctype.crm_organization.company_code import (
	FIELD_COMPANY_CODE,
	FIELD_COMPANY_CODE_KEY,
	backfill_company_code_keys,
)

DOCTYPE = "CRM Organization"

FIELD_DEFINITIONS = [
	{
		"fieldname": FIELD_COMPANY_CODE,
		"fieldtype": "Data",
		"label": "Company Code",
		"description": "Unique identifier for this organization. Required for new organizations.",
		"insert_after": "organization_name",
	},
	{
		"fieldname": FIELD_COMPANY_CODE_KEY,
		"fieldtype": "Data",
		"label": "Company Code Key",
		"description": "Normalized (whitespace-stripped, case-folded) Company Code. System-maintained.",
		"hidden": 1,
		"read_only": 1,
		"unique": 1,
		"no_copy": 1,
		"insert_after": FIELD_COMPANY_CODE,
	},
]


def execute():
	meta = frappe.get_meta(DOCTYPE)
	missing = [field for field in FIELD_DEFINITIONS if not meta.has_field(field["fieldname"])]
	if missing:
		create_custom_fields({DOCTYPE: missing})
		frappe.clear_cache(doctype=DOCTYPE)

	report = backfill_company_code_keys()

	if report["collisions"] or report["missing"]:
		frappe.logger("txb").info(
			"[TXB-243] Company Code backfill: "
			f"{len(report['unambiguous'])} keyed, "
			f"{len(report['missing'])} blank, "
			f"{len(report['collisions'])} collision group(s) left for remediation: "
			f"{report['collisions']}"
		)

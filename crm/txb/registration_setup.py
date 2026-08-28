"""TXB-15: schema the workshop registration flow needs. Idempotent; runs from
``crm.install.after_install`` and the ``workshop_registration_v2`` patch."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from crm.txb.constants import (
	FIELD_CONTACT_REGISTRATION_STATUS,
	FIELD_CONTACT_SOURCE_WORKSHOP,
	WORKSHOP_INTEREST_OPTIONS,
)
from crm.txb.layouts import add_fields_after

CONTACT_FIELD_DEFINITIONS = [
	{
		"fieldname": FIELD_CONTACT_REGISTRATION_STATUS,
		"fieldtype": "Select",
		"label": "Workshop registration",
		"options": "\n" + "\n".join(WORKSHOP_INTEREST_OPTIONS),
		"read_only": 1,
		"insert_after": "designation",
	},
	{
		"fieldname": FIELD_CONTACT_SOURCE_WORKSHOP,
		"fieldtype": "Link",
		"options": "CRM Deal",
		"label": "Registered for workshop",
		"read_only": 1,
		"insert_after": FIELD_CONTACT_REGISTRATION_STATUS,
	},
]
CONTACT_SIDE_PANEL = "Contact-Side Panel"


def ensure_registration_setup() -> None:
	logger = frappe.logger("crm")
	logger.info("[ensure_registration_setup] Attempting to install workshop registration fields")
	try:
		meta = frappe.get_meta("Contact")
		missing = [f for f in CONTACT_FIELD_DEFINITIONS if not meta.has_field(f["fieldname"])]
		if missing:
			create_custom_fields({"Contact": missing})
			frappe.clear_cache(doctype="Contact")
		add_fields_after(
			CONTACT_SIDE_PANEL, "designation", [f["fieldname"] for f in CONTACT_FIELD_DEFINITIONS]
		)
	except Exception as e:
		logger.error(f"[ensure_registration_setup] Failed to install workshop registration fields. {e}")
		raise

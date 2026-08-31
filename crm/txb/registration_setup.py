"""TXB-15: schema the workshop registration flow needs. Idempotent; runs from
``crm.install.after_install`` and the ``workshop_registration_v2`` patch."""

import re

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from crm.txb.api.registration import CONFIRMATION_TEMPLATE
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

CONFIRMATION_SUBJECT = "Registracija sėkmingai gauta"

# The Programa row, already gated so an absent Program Type drops the whole row out (and never
# leaks a raw `{{ program_type }}`). Used verbatim when seeding a fresh template and as the
# canonical shape the update path converts existing installs to.
CONFIRMATION_PROGRAM_ROW = (
	"{% if program_type %}<tr><td>Programa: {{ program_type }}</td></tr>{% endif %}"
)
DEFAULT_CONFIRMATION_HTML = (
	"<p>Sveiki, {{ first_name }},</p>"
	"<p>Jūsų registracija sėkmingai gauta. Ačiū!</p>"
	f"<table>{CONFIRMATION_PROGRAM_ROW}</table>"
)

# One HTML element (a table row, paragraph, list item, …) that carries the `program_type`
# placeholder. Wrapping the whole element in `{% if program_type %}` keeps every other line of
# the persisted template untouched.
_PROGRAM_ROW_RE = re.compile(
	r"<(?P<tag>tr|p|div|li)\b[^>]*>(?:(?!</(?P=tag)>).)*?"
	r"\{\{\s*program_type\s*\}\}(?:(?!</(?P=tag)>).)*?</(?P=tag)>",
	re.IGNORECASE | re.DOTALL,
)


def make_program_row_conditional(html: str) -> str:
	"""Gate the Programa row of an existing confirmation body behind `{% if program_type %}`,
	leaving the rest of the body byte-for-byte. Idempotent: a body already carrying the guard
	is returned unchanged."""
	if not html or "{% if program_type %}" in html:
		return html
	return _PROGRAM_ROW_RE.sub(lambda m: "{% if program_type %}" + m.group(0) + "{% endif %}", html, count=1)


def ensure_confirmation_email_template() -> None:
	"""Create the confirmation Email Template on a fresh site, or make the Programa row of an
	existing one conditional. Idempotent so it is safe from install, the patch and the tests."""
	if not frappe.db.exists("Email Template", CONFIRMATION_TEMPLATE):
		frappe.get_doc(
			{
				"doctype": "Email Template",
				"name": CONFIRMATION_TEMPLATE,
				"subject": CONFIRMATION_SUBJECT,
				"use_html": 1,
				"response_html": DEFAULT_CONFIRMATION_HTML,
			}
		).insert(ignore_permissions=True)
		return
	doc = frappe.get_doc("Email Template", CONFIRMATION_TEMPLATE)
	updated = make_program_row_conditional(doc.response_html or "")
	if updated != doc.response_html:
		doc.response_html = updated
		doc.save(ignore_permissions=True)


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
		ensure_confirmation_email_template()
	except Exception as e:
		logger.error(f"[ensure_registration_setup] Failed to install workshop registration fields. {e}")
		raise

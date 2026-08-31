"""TXB-15: schema the workshop registration flow needs. Idempotent; runs from
``crm.install.after_install`` and the ``workshop_registration_v2`` patch."""

import re

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from crm.txb.constants import (
	CONFIRMATION_TEMPLATE,
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

# The single Programa *table row* that carries the `program_type` placeholder. The gate must
# wrap this one `<tr>` and nothing else: an earlier attempt (TXB-200) also accepted `div`, which
# matched the whole padded content container that holds the greeting, submitted fields, contact
# and closing text -- so a blank Program Type emptied the entire email body (TXB-202). Restricting
# the tag to `tr` keeps every other line of the persisted template untouched.
_PROGRAM_ROW_RE = re.compile(
	r"<(?P<tag>tr)\b[^>]*>(?:(?!</(?P=tag)>).)*?"
	r"\{\{\s*program_type\s*\}\}(?:(?!</(?P=tag)>).)*?</(?P=tag)>",
	re.IGNORECASE | re.DOTALL,
)

# The opening of the Program Type gate, and every `{% if %}`/`{% endif %}` token, so the erroneous
# outer guard can be unwrapped while its nested optional-field guards are kept balanced.
_PROGRAM_GUARD_OPEN_RE = re.compile(r"\{%-?\s*if\s+program_type\s*-?%\}", re.IGNORECASE)
_JINJA_BLOCK_RE = re.compile(r"\{%-?\s*(?P<kw>if|endif)\b[^%]*?%\}", re.IGNORECASE | re.DOTALL)


def make_program_row_conditional(html: str) -> str:
	"""Gate the Programa row of an existing confirmation body behind `{% if program_type %}`,
	leaving the rest of the body byte-for-byte. Idempotent: a body already carrying the guard
	is returned unchanged."""
	if not html or "{% if program_type %}" in html:
		return html
	return _PROGRAM_ROW_RE.sub(lambda m: "{% if program_type %}" + m.group(0) + "{% endif %}", html, count=1)


def unwrap_broad_program_guard(html: str) -> str:
	"""Undo the TXB-200 regression that wrapped the whole padded content container -- not just
	the Programa row -- in `{% if program_type %}`, which dropped the greeting, submitted fields,
	contact and closing text whenever Program Type was blank. Strip only that outer if/endif pair,
	preserving all nested optional-field guards and every byte of HTML between them. A guard that
	already fronts a `<tr>` (the correct row-only shape) is left untouched, so this is idempotent
	and a no-op on a healthy or never-patched body."""
	m = _PROGRAM_GUARD_OPEN_RE.search(html or "")
	if not m or html[m.end() :].lstrip()[:3].lower() == "<tr":
		return html
	depth = 1
	for tok in _JINJA_BLOCK_RE.finditer(html, m.end()):
		depth += 1 if tok.group("kw").lower() == "if" else -1
		if depth == 0:
			return html[: m.start()] + html[m.end() : tok.start()] + html[tok.end() :]
	return html


def repair_confirmation_body(html: str) -> str:
	"""Bring a persisted confirmation body to the canonical shape: no erroneous outer Program Type
	guard, exactly the Programa `<tr>` gated. Idempotent across a healthy, a raw, or a TXB-200
	damaged body."""
	return make_program_row_conditional(unwrap_broad_program_guard(html or ""))


def ensure_confirmation_email_template() -> None:
	"""Create the confirmation Email Template on a fresh site, or repair an existing one so only
	its Programa row is conditional. Idempotent so it is safe from install, the patch and the
	tests."""
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
	updated = repair_confirmation_body(doc.response_html or "")
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

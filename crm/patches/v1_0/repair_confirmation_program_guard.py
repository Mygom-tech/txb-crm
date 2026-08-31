"""TXB-202: repair "Registracijos patvirtinimas" Email Templates that TXB-200 wrapped whole in
`{% if program_type %}`. That guard swallowed the main padded content container, so a blank Program
Type emptied the greeting, submitted fields, contact and closing text -- leaving only header and
footer. This unwraps the erroneous outer guard (keeping all nested optional-field guards and HTML)
and re-gates only the Programa `<tr>`. Idempotent; a healthy or never-patched body is untouched."""

import frappe

from crm.txb.registration_setup import ensure_confirmation_email_template


def execute():
	ensure_confirmation_email_template()
	frappe.db.commit()

"""TXB-200: make the Programa row of the persisted "Registracijos patvirtinimas" Email
Template conditional so it renders the source deal's Program Type when present and never
leaks a raw `{{ program_type }}` when absent. Idempotent; the rest of the body is preserved."""

import frappe

from crm.txb.registration_setup import ensure_confirmation_email_template


def execute():
	ensure_confirmation_email_template()
	frappe.db.commit()

"""Move Delivery Coach notifications to activation, add the client's email, drop Slack.

1. Templates. Seed the client's Lithuanian Email Template, and bring a never-edited coach
   template to the body with the client's email, phone and the 48-hour line.
2. Slack. Remove the FCRM Settings Slack bot token field and its encrypted value.
3. Ledger. Rows recorded before the client channel existed get it Skipped, so no past event
   ever emails a client.

Idempotent: a re-run finds nothing left to change and never overwrites an edited template.
"""

import frappe

from crm.txb.delivery_coach_notifications import (
	LEDGER_DOCTYPE,
	SETTINGS_DOCTYPE,
	STATUS_SKIPPED,
	ensure_assignment_email_template,
	ensure_client_email_template,
)

SLACK_TOKEN_FIELD = "slack_bot_token"
PRE_ACTIVATION = "Skipped: recorded before the client was emailed on activation"


def execute():
	ensure_assignment_email_template()
	ensure_client_email_template()

	custom_field = f"{SETTINGS_DOCTYPE}-{SLACK_TOKEN_FIELD}"
	if frappe.db.exists("Custom Field", custom_field):
		frappe.delete_doc("Custom Field", custom_field, ignore_permissions=True)
	frappe.db.delete("__Auth", {"doctype": SETTINGS_DOCTYPE, "fieldname": SLACK_TOKEN_FIELD})
	frappe.clear_cache(doctype=SETTINGS_DOCTYPE)

	ledger = frappe.qb.DocType(LEDGER_DOCTYPE)
	(
		frappe.qb.update(ledger)
		.set(ledger.client_email_status, STATUS_SKIPPED)
		.set(ledger.client_email_error, PRE_ACTIVATION)
		.where(ledger.client_email_status.isnull() | (ledger.client_email_status == ""))
	).run()

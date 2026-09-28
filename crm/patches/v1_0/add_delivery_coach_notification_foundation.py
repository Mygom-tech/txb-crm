"""Install the Delivery Coach assignment notification foundation (TXB-269).

The `CRM Coach Assignment Notification` ledger DocType arrives with the normal model sync; this
patch adds the FCRM Settings fields (mode, Test redirect recipient, encrypted Slack bot token),
pins the mode to Disabled and seeds the editable assignment Email Template.

Deliberately no data: existing Delivery Coach values are not replayed into the ledger, so no
historical assignment ever produces a notification. Idempotent -- a re-run adds nothing, keeps
an operator's chosen mode and never overwrites an edited template.
"""

from crm.txb.delivery_coach_notifications import (
	ensure_assignment_email_template,
	ensure_notification_settings,
)


def execute():
	ensure_notification_settings()
	ensure_assignment_email_template()

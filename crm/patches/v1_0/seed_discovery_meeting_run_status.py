"""TXB-168: seed the guarded "Discovery meeting run" CRM Lead Status on deployed sites.

Idempotent. New installs get the status from ``crm.install.add_default_lead_statuses``; this
patch backfills it on sites provisioned before the status existed, so the guarded Run Discovery
Meeting transition always has its trigger status available.

The status is never a durable resting state -- a move into it routes through
``crm.txb.lead_actions.run_discovery_meeting`` and is otherwise refused by
``guard_discovery_meeting_run`` -- but it must exist as a selectable CRM Lead Status so every
surface can offer it. It is positioned immediately after "Discovery meeting set" by reading that
status's live position, so the discovery cluster stays ordered whatever the site's numbering.
"""

import frappe

TRIGGER_STATUS = "Discovery meeting run"
BOOKED_STATUS = "Discovery meeting set"


def execute():
	# Idempotent: nothing to do once the status exists (installs keep their own configuration).
	if frappe.db.exists("CRM Lead Status", TRIGGER_STATUS):
		return

	# Order it immediately after Discovery meeting set. Fall back to a sensible default when that
	# status is absent, so the patch never fails on an unexpected configuration.
	booked_position = frappe.db.get_value("CRM Lead Status", BOOKED_STATUS, "position")
	position = (booked_position or 9) + 1

	frappe.get_doc(
		{
			"doctype": "CRM Lead Status",
			"lead_status": TRIGGER_STATUS,
			"color": "orange",
			"type": "Ongoing",
			"position": position,
		}
	).insert(ignore_permissions=True)

	frappe.db.commit()

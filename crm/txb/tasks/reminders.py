"""Scheduled reminder jobs.

Ported from the `Weekly VCS Reminder` and `Stale Session Run Alert` Server Scripts.
Both create a CRM Task for a deal owner and both suppress repeats within a week.
"""

import frappe
from frappe.utils import add_days, date_diff, today

from crm.txb.constants import PIPELINE_INDIVIDUAL_SESSION, PIPELINE_WORKSHOP

REMINDER_COOLDOWN_DAYS = 7
STALE_SESSION_DAYS = 30
TASK_PRIORITY = "Medium"


def weekly_vcs_reminder():
	"""Chase Workshop deals whose workshop date is still unconfirmed. Mondays 09:00."""
	deals = frappe.get_all(
		"CRM Deal",
		filters={
			"pipeline_type": PIPELINE_WORKSHOP,
			"status": "VCS call run",
			"custom_workshop_date_confirmed": ["!=", "Yes"],
		},
		fields=["name", "organization", "deal_owner"],
	)

	for deal in deals:
		if was_reminded_recently(deal.name, "%VCS reminder%workshop date%"):
			continue

		create_reminder(
			deal,
			title=f"VCS reminder: Confirm workshop date - {label_for(deal)}",
			description=(
				"Workshop date is still unconfirmed for this deal. "
				"Please follow up to confirm the date."
			),
		)


def stale_session_run_alert():
	"""Flag Individual Session deals sitting in Session Run for 30+ days. Daily 09:00."""
	deals = frappe.get_all(
		"CRM Deal",
		filters={"pipeline_type": PIPELINE_INDIVIDUAL_SESSION, "status": "Session Run"},
		fields=["name", "organization", "deal_owner", "modified"],
	)

	for deal in deals:
		days_stuck = date_diff(today(), str(deal.modified).split(" ")[0])
		if days_stuck < STALE_SESSION_DAYS:
			continue

		if was_reminded_recently(deal.name, "%stale Session Run%"):
			continue

		create_reminder(
			deal,
			title=f"Stale Session Run: {label_for(deal)} ({days_stuck} days)",
			description=(
				f"This deal has been in Session Run status for {days_stuck} days. "
				"Please review and take action."
			),
		)


def was_reminded_recently(deal_name: str, title_pattern: str) -> bool:
	"""True when a matching reminder already exists inside the cooldown window."""
	return bool(
		frappe.get_all(
			"CRM Task",
			filters={
				"reference_docname": deal_name,
				"title": ["like", title_pattern],
				"creation": [">=", add_days(today(), -REMINDER_COOLDOWN_DAYS)],
			},
			limit=1,
		)
	)


def label_for(deal) -> str:
	return deal.organization or deal.name


def create_reminder(deal, title: str, description: str):
	try:
		frappe.get_doc(
			{
				"doctype": "CRM Task",
				"title": title,
				"description": description,
				"assigned_to": deal.deal_owner or frappe.session.user,
				"priority": TASK_PRIORITY,
				"reference_doctype": "CRM Deal",
				"reference_docname": deal.name,
			}
		).insert(ignore_permissions=True)
	except Exception as e:
		# One bad deal must not abort the whole scheduled run.
		frappe.log_error(
			f"[create_reminder] Failed to create reminder for {deal.name}. {e}",
			"TXB Reminders",
		)

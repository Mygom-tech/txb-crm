"""Install the first-coaching-call reminder's app-owned metadata (TXB-227).

Three fields, and deliberately no data:

* `CRM Deal.custom_activation_cycle` / `custom_activation_started_on` -- the Opportunity's
  current Delivering Coaching activation cycle and when it began. Every existing Deal is left
  with both empty, **including the ones sitting in Active right now**: this app has not observed
  those activations, so it cannot say when their deadline started and must not invent one. They
  become eligible the next time they genuinely transition into Active.
* `CRM Task.custom_txb_reminder_cycle` -- the activation cycle a reminder belongs to, UNIQUE.
  That constraint is the exactly-once guarantee; `crm.txb.first_call_reminders` leans on it to
  recover a race between concurrent scheduler runs rather than to detect one after the fact.
* `FCRM Settings.custom_first_call_reminder_minutes` -- the configurable delay, defaulting to
  1,440 minutes (a day) and read at evaluation time, so it can be shortened for a test run.

All three are hidden, read-only where they are app-managed, and no-copy. Idempotent: a re-run
with the fields present is a no-op, and a field is only ever added, never redefined or dropped.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from crm.txb.admin_assignment import ADMIN_TASK_ASSIGNEE_FIELD, SETTINGS_DOCTYPE
from crm.txb.constants import (
	FIELD_ACTIVATION_CYCLE,
	FIELD_ACTIVATION_STARTED_ON,
	FIELD_REMINDER_CYCLE,
	FIRST_CALL_REMINDER_DEFAULT_MINUTES,
	SETTING_FIRST_CALL_REMINDER_MINUTES,
)
from crm.txb.first_call_reminders import DEAL_DOCTYPE, TASK_DOCTYPE

DEAL_FIELDS = [
	{
		"fieldname": FIELD_ACTIVATION_CYCLE,
		"fieldtype": "Data",
		"label": "Activation Cycle",
		"description": "Identity of the Opportunity's current Delivering Coaching activation.",
		"read_only": 1,
		"no_copy": 1,
		"hidden": 1,
		"insert_after": "status",
	},
	{
		"fieldname": FIELD_ACTIVATION_STARTED_ON,
		"fieldtype": "Datetime",
		"label": "Activation Started On",
		"description": "When the current activation cycle began; the first-call deadline is measured from here.",
		"read_only": 1,
		"no_copy": 1,
		"hidden": 1,
		"insert_after": FIELD_ACTIVATION_CYCLE,
	},
]

TASK_FIELDS = [
	{
		"fieldname": FIELD_REMINDER_CYCLE,
		"fieldtype": "Data",
		"label": "TxB Reminder Cycle",
		"description": "The activation cycle this reminder belongs to. One reminder per cycle.",
		"read_only": 1,
		"unique": 1,
		"no_copy": 1,
		"hidden": 1,
		"insert_after": "reference_docname",
	}
]

SETTINGS_FIELDS = [
	{
		"fieldname": SETTING_FIRST_CALL_REMINDER_MINUTES,
		"fieldtype": "Int",
		"label": "First Coaching Call Reminder Delay (minutes)",
		"description": (
			"How long an Active Delivering Coaching Opportunity may go without a First Coaching "
			"Call Date before the Admin is reminded. Minimum 1; blank keeps the default of "
			f"{FIRST_CALL_REMINDER_DEFAULT_MINUTES} minutes."
		),
		"default": str(FIRST_CALL_REMINDER_DEFAULT_MINUTES),
		"non_negative": 1,
		"insert_after": ADMIN_TASK_ASSIGNEE_FIELD,
	}
]


def execute():
	_install(DEAL_DOCTYPE, DEAL_FIELDS)
	_install(TASK_DOCTYPE, TASK_FIELDS)
	_install(SETTINGS_DOCTYPE, SETTINGS_FIELDS)


def _install(doctype: str, definitions: list[dict]) -> None:
	"""Add only the definitions this site is missing, then refresh the doctype's cached meta."""
	meta = frappe.get_meta(doctype)
	missing = [field for field in definitions if not meta.has_field(field["fieldname"])]
	if not missing:
		return

	create_custom_fields({doctype: missing})
	frappe.clear_cache(doctype=doctype)

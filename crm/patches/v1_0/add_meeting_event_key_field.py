"""Install the TXB-209 stable meeting-identity field on Frappe Event.

`custom_txb_meeting_key` carries `<reference_doctype>:<reference_docname>:<flow>` for every Event
a TxB scheduling action upserts, and is UNIQUE. That database constraint is the whole idempotency
guarantee -- at most one canonical Event per (source, meeting flow) -- and it is what lets
`crm.txb.meetings.sync_meeting_event` recover a duplicate-key race by reusing the winner instead of
spawning a second meeting Event.

Read-only and no_copy: the key is app-managed identity, not a user-editable field, and must never
be carried onto a copied Event.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from crm.txb.constants import FIELD_MEETING_KEY

FIELD_DEFINITION = {
	"fieldname": FIELD_MEETING_KEY,
	"fieldtype": "Data",
	"label": "TxB Meeting Key",
	"description": "Stable identity linking this Event to its source Lead/Opportunity meeting flow.",
	"read_only": 1,
	"unique": 1,
	"no_copy": 1,
	"hidden": 1,
	"insert_after": "reference_docname",
}


def execute():
	"""Install the meeting-key field if absent, idempotently. A re-run with it present is a no-op."""
	if frappe.get_meta("Event").has_field(FIELD_MEETING_KEY):
		return

	create_custom_fields({"Event": [FIELD_DEFINITION]})
	frappe.clear_cache(doctype="Event")

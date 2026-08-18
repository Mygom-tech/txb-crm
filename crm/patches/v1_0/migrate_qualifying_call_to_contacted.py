"""TXB-128: migrate the retired "Qualifying call" Lead status onto "Contacted".

Silent and idempotent. Every write is a direct DB update -- no `doc.save()`, so no
document-save hooks fire and no notifications are emitted -- and the whole patch is a
no-op once the legacy status is gone, so re-running `bench migrate` cannot double-apply
or disturb already-migrated Leads.

Leads and their status history are preserved: Leads carrying the old status are repointed
to Contacted, and the `from`/`to` columns of the status change log are rewritten so the
timeline still reads back consistently. Only then is the orphaned status row removed.
"""

import frappe

LEGACY_STATUS = "Qualifying call"
CANONICAL_STATUS = "Contacted"


def execute():
	# Idempotent guard: nothing to migrate once the retired status is gone.
	if not frappe.db.exists("CRM Lead Status", LEGACY_STATUS):
		return

	ensure_canonical_status()

	# Repoint Leads without running document hooks or emitting notifications. A direct
	# UPDATE leaves `modified`/`modified_by` untouched so the migration is invisible in the
	# audit trail, matching "silent".
	frappe.db.set_value(
		"CRM Lead",
		{"status": LEGACY_STATUS},
		"status",
		CANONICAL_STATUS,
		update_modified=False,
	)

	# Preserve status history: rewrite the change-log rows that name the retired status on
	# either side of a transition.
	for column in ("from", "to"):
		frappe.db.sql(
			f"""
			UPDATE `tabCRM Status Change Log`
			SET `{column}` = %(canonical)s
			WHERE `{column}` = %(legacy)s
			""",
			{"canonical": CANONICAL_STATUS, "legacy": LEGACY_STATUS},
		)

	# Nothing references the retired status now, so remove it permanently and without hooks.
	frappe.delete_doc(
		"CRM Lead Status",
		LEGACY_STATUS,
		ignore_permissions=True,
		force=True,
		delete_permanently=True,
	)

	frappe.db.commit()


def ensure_canonical_status():
	"""Guarantee "Contacted" exists before repointing, carrying the legacy row's styling.

	On a site that only ever had "Qualifying call", the canonical status may be absent; it
	is created from the retired row's own color/type/position so the board keeps its
	appearance. Where "Contacted" already exists it is left exactly as-is.
	"""
	if frappe.db.exists("CRM Lead Status", CANONICAL_STATUS):
		return

	legacy = frappe.get_doc("CRM Lead Status", LEGACY_STATUS)
	frappe.get_doc(
		{
			"doctype": "CRM Lead Status",
			"lead_status": CANONICAL_STATUS,
			"color": legacy.color or "orange",
			"type": legacy.type or "Ongoing",
			"position": legacy.position or 2,
		}
	).insert(ignore_permissions=True)

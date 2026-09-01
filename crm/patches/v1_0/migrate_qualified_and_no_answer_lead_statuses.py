"""TXB-210: retire the obsolete "Qualified" and "No Answer" Lead statuses.

Silent and idempotent, and modelled on `migrate_qualifying_call_to_contacted`. Two legacy
CRM Lead Status rows are folded onto canonical targets and then removed:

* "Qualified" -> "Contacted". Conversion no longer rests a lead at the "Qualified"
  intermediate (it uses "Converted" now, see `crm.fcrm.doctype.crm_lead.crm_lead
  .convert_to_deal`), so any lead still carrying "Qualified" is a pre-migration artifact and
  folds onto "Contacted".
* "No Answer" -> "Contact attempted". An unanswered dial now lands a lead on "Contact
  attempted" (see `crm.txb.lead_actions.log_a_dial`), so the retired "No Answer" *Lead status*
  folds onto it.

Every write is a direct DB update -- no `doc.save()`, so no document-save hooks fire (the
Follow-up/Nurture/reach/discovery guards never see these writes) and no notifications are
emitted. Leads and their status history are preserved: leads carrying an obsolete status are
repointed, and the `from`/`to` columns of the status change log are rewritten so the timeline
still reads back consistently. Only then is each orphaned status row removed. The whole patch
is a no-op once both legacy statuses are gone, so re-running `bench migrate` cannot
double-apply or disturb already-migrated leads.

The independent "No Answer" *CRM Call Log* result is untouched: that lives on the CRM Call Log
`status` Select (see `crm.txb.constants.DIAL_RESULTS`), not on CRM Lead Status, and this patch
never writes CRM Call Log. Retiring the Lead status leaves the manual-call result valid.
"""

import frappe

# Legacy CRM Lead Status -> the canonical status it folds onto. Ordered, but independent: each
# mapping is applied only if its legacy row still exists, so a partially-applied prior run
# (only one retired) still completes cleanly.
STATUS_MIGRATIONS = {
	"Qualified": "Contacted",
	"No Answer": "Contact attempted",
}


def execute():
	# Idempotent guard: nothing to migrate once every retired status is gone.
	if not any(frappe.db.exists("CRM Lead Status", legacy) for legacy in STATUS_MIGRATIONS):
		return

	for legacy_status, canonical_status in STATUS_MIGRATIONS.items():
		migrate_status(legacy_status, canonical_status)

	frappe.db.commit()


def migrate_status(legacy_status: str, canonical_status: str):
	"""Fold one legacy Lead status onto its canonical target, then retire the legacy row."""
	if not frappe.db.exists("CRM Lead Status", legacy_status):
		return

	ensure_canonical_status(legacy_status, canonical_status)

	# Repoint leads without running document hooks or emitting notifications. A direct UPDATE
	# leaves `modified`/`modified_by` untouched so the migration is invisible in the audit trail.
	frappe.db.set_value(
		"CRM Lead",
		{"status": legacy_status},
		"status",
		canonical_status,
		update_modified=False,
	)

	# Preserve status history: rewrite the change-log rows that name the retired status on either
	# side of a transition, so the timeline still reads back consistently.
	for column in ("from", "to"):
		frappe.db.sql(
			f"""
			UPDATE `tabCRM Status Change Log`
			SET `{column}` = %(canonical)s
			WHERE `{column}` = %(legacy)s
			""",
			{"canonical": canonical_status, "legacy": legacy_status},
		)

	# Nothing references the retired status now, so remove it permanently and without hooks.
	frappe.delete_doc(
		"CRM Lead Status",
		legacy_status,
		ignore_permissions=True,
		force=True,
		delete_permanently=True,
	)


def ensure_canonical_status(legacy_status: str, canonical_status: str):
	"""Guarantee the canonical status exists before repointing, carrying the legacy row's styling.

	On a site that only ever had the legacy status, the canonical one may be absent; it is created
	from the retired row's own color/type/position so the board keeps its appearance. Where the
	canonical status already exists it is left exactly as-is.
	"""
	if frappe.db.exists("CRM Lead Status", canonical_status):
		return

	legacy = frappe.get_doc("CRM Lead Status", legacy_status)
	frappe.get_doc(
		{
			"doctype": "CRM Lead Status",
			"lead_status": canonical_status,
			"color": legacy.color or "orange",
			"type": legacy.type or "Ongoing",
			"position": legacy.position or 2,
		}
	).insert(ignore_permissions=True)

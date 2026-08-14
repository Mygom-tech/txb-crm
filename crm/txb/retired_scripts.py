"""Retire the database scripts whose behaviour now lives in `crm/txb/`.

Server Scripts and Form Scripts are database rows: invisible to git, unreviewable, and
free to drift between environments. Every name below has a Python or Vue replacement that
ships with the app, so a row left enabled means both copies run. For `CRM Wizard
Framework` that is visible: it injects its own **Take Action** menu, which appears next to
the native one on every deal page.

Each of these was already retired once by a `crm.patches.v1_0.disable_*` patch. A patch
runs **once per site, forever** — recorded in `tabPatch Log` and never revisited. So a row
that comes back afterwards has nothing to catch it:

- someone flips `enabled` in the desk UI,
- a site is restored from a dump taken before the patch landed,
- an environment is rebuilt from a backup whose Patch Log already lists the patch, so
  migrate skips it while the row it was meant to disable is enabled again.

This module is therefore wired into `after_migrate` (`hooks.py`) rather than into a patch:
it re-asserts the retirement on **every** `bench migrate`, which is the one command every
deploy already runs. Bringing a script back is then a deliberate code change — removing
its name here — instead of an undetected click.
"""

import frappe

FORM_SCRIPT = "CRM Form Script"
SERVER_SCRIPT = "Server Script"

# Form Scripts, keyed off `enabled`. Replacements, in order:
# the wizard -> crm/txb/pipelines/ + crm.txb.api.actions (the duplicate Take Action menu);
# the convert dialog -> ConvertToDealModal.vue; the status filter -> crm.txb.api.pipelines;
# owner read-only -> restrict_owner_field/guard_owner_change;
# create opportunity -> CreateDealFromContactModal.vue;
# disqualified reason prompt -> Lead.vue LostReasonModal auto-open;
# lead creation redirect -> Lead.vue native Data-tab default;
# auto refresh call count -> Deal.vue reloads the canonical deal after coaching actions and
#   completed call-log changes (native reactivity, no DOM polling);
# notes tab rename -> Deal.vue computes the "Coaching Notes" tab label for coaching deals;
# hide call duration -> CallArea.vue `hideDuration` option, set from Deal.vue;
# workshop datetime modal -> the native Set Workshop action collects
#   custom_workshop_scheduled_at, and crm.txb.doc_events.deal.require_workshop_schedule
#   enforces it server-side (TXB-149).
# pipeline section visibility -> committed pipeline depends_on (frontend/src/utils/
#   pipelineLayout.js, applied in Deal.vue getParsedSections) evaluated reactively by
#   SidePanelLayout.vue, with the stale `pipeline_type == "Training"` condition corrected
#   to "Selling Training"; empty sections collapse through the standard fields-layout rule;
# forecasting -> probability is derived server-side (CRM Deal update_default_probability)
#   and Deal.vue reloads the document after a status save so the value shows with no reload.
RETIRED_FORM_SCRIPTS = (
	"CRM Wizard Framework",
	"Convert Dialog - Pipeline Type",
	"Pipeline Status Filter",
	"Lead Owner Read-Only",
	"Contact_Create Opportunity",
	"Disqualified Reason Prompt",
	"Lead Creation Redirect",
	"Auto Refresh Call Count",
	"Notes Tab Rename",
	"Hide Call Duration",
	"Workshop Datetime Modal",
	"Pipeline Section Visibility",
	"Forecasting Script",
)

# Server Scripts, keyed off `disabled`. All now live in `crm/txb/doc_events`,
# `crm/txb/tasks` and `crm/txb/api`, wired through hooks.py.
RETIRED_SERVER_SCRIPTS = (
	# CRM Lead
	"Auto Assign Lead Owner",
	"Protect Lead Owner",
	"Require Disqualified Reason",
	"Prevent Duplicate Lead",
	# Contact
	"Sync Contact Organization",
	# CRM Deal
	"Sync Deal Contact Name",
	"Sync Delivery Coach Name",
	# CRM Call Log
	"Default Call Log Phone Numbers",
	"Update Deal Call Count",
	"Update Deal Call Count On Save",
	"Update Deal Call Count On Delete",
	# Scheduler
	"Weekly VCS Reminder",
	"Stale Session Run Alert",
	# API — answered on their dotted paths now, see disable_migrated_server_scripts
	"Process Registration",
	"Validate Registration Token",
)


def retire_scripts() -> None:
	"""Disable every superseded script that is still live. Idempotent."""
	frappe.logger().info("[retire_scripts] Attempting to disable superseded database scripts")

	retired = _disable(FORM_SCRIPT, RETIRED_FORM_SCRIPTS, "enabled", 0)
	retired += _disable(SERVER_SCRIPT, RETIRED_SERVER_SCRIPTS, "disabled", 1)

	if not retired:
		return

	# The form script reaches the browser through get_form_script -> get_data, and both
	# doctypes are cached documents; without this the menu survives until the next restart.
	frappe.clear_cache()
	frappe.logger().info(f"[retire_scripts] Disabled {len(retired)} script(s): {', '.join(retired)}")


def _disable(doctype: str, names: tuple[str, ...], field: str, off_value: int) -> list[str]:
	"""Set `field` to `off_value` on any of `names` that is still on.

	A failure here must never abort `bench migrate` — a half-applied deploy is worse than a
	script that survives one more release — so each row is handled independently.
	"""
	disabled = []

	for name in names:
		try:
			if not frappe.db.exists(doctype, name):
				continue

			if frappe.db.get_value(doctype, name, field) == off_value:
				continue

			frappe.db.set_value(doctype, name, field, off_value)
			disabled.append(name)
		except Exception as error:
			frappe.log_error(
				title="retire_scripts",
				message=f"[_disable] Failed to disable {doctype} '{name}'. {error}",
			)

	return disabled

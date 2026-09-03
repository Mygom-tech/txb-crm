"""Keep the effective CRM Call Log `status` options in step with the committed schema.

The DocType JSON (`crm/fcrm/doctype/crm_call_log/crm_call_log.json`) is the canonical contract for
the `status` field: the full provider lifecycle plus the terminal outcomes a manually logged dial
records, including `No Answer` (see `crm.txb.constants.CALL_LOG_STATUS_OPTIONS`). `crm.txb.lead_actions.log_a_dial`
writes the selected dial result straight into `CRM Call Log.status`, so that field must accept every
approved dial result for the atomic move into "Contact attempted" to succeed.

A **Property Setter** can override a field's `options` at runtime without touching git. On the live
site one had drifted the `status` options to a foreign set (`Completed`, `Missed`, `No Charge`),
which are neither the committed options nor the approved dial results. The effect: Frappe's own
Select validation rejected the canonical `No Answer` result before the Lead could be saved --
`Status cannot be "No Answer". It should be one of "Completed", "Missed", "No Charge"` -- so Log a
dial could no longer move a Lead to Contact attempted. The repository JSON was already correct; only
the deployed metadata had drifted.

The repair is the smallest idempotent reconciliation: a conflicting `CRM Call Log-status-options`
Property Setter is removed so the committed DocType options take effect again, and the DocType's
metadata cache is cleared so the corrected options are served without a restart. A Property Setter
that already matches the canonical options is left untouched, and a site that never carried one is a
no-op, so the reconciliation is safe to re-run on every deploy.

Wired into `after_install` and `after_migrate` (see `crm/install.py` and `crm/hooks.py`) rather than
a one-shot patch: a patch runs once per site forever, so a Property Setter re-created afterwards --
by a click in Customize Form, or a restore from a pre-fix backup -- would have nothing to catch it.
Re-asserting on every `bench migrate` makes reintroducing the override a deliberate act, mirroring
`crm.txb.retired_scripts`.
"""

import frappe

from crm.txb.constants import CALL_LOG_STATUS_OPTIONS

CALL_LOG_DOCTYPE = "CRM Call Log"
STATUS_FIELD = "status"

# Property Setters are named `<doctype>-<fieldname>-<property>`; this one overrides the `status`
# field's `options`. Removing it lets the committed DocType options govern the effective metadata.
STATUS_OPTIONS_PROPERTY_SETTER = f"{CALL_LOG_DOCTYPE}-{STATUS_FIELD}-options"

# The canonical options as a single newline-joined string, matching how Frappe stores a Select
# field's `options`. A Property Setter carrying exactly this value is harmless and left in place.
CANONICAL_STATUS_OPTIONS = "\n".join(CALL_LOG_STATUS_OPTIONS)


def reconcile_call_log_status_options() -> None:
	"""Remove any runtime override that drifts CRM Call Log `status` off its committed options.

	Idempotent: no Property Setter, or one that already matches the canonical options, is a no-op;
	a drifted one is deleted and the DocType metadata cache cleared so the committed options -- which
	include every approved dial result -- take effect immediately. A failure here must never abort
	`bench migrate`, so it is caught and logged rather than raised.
	"""
	try:
		if not frappe.db.exists("Property Setter", STATUS_OPTIONS_PROPERTY_SETTER):
			return

		current = frappe.db.get_value(
			"Property Setter", STATUS_OPTIONS_PROPERTY_SETTER, "value"
		)
		if current == CANONICAL_STATUS_OPTIONS:
			# The override agrees with the committed schema; nothing to reconcile.
			return

		frappe.delete_doc(
			"Property Setter",
			STATUS_OPTIONS_PROPERTY_SETTER,
			ignore_permissions=True,
			force=True,
		)

		# The Select options reach validation through the cached DocType meta; without this the
		# stale options keep rejecting approved dial results until the next restart.
		frappe.clear_cache(doctype=CALL_LOG_DOCTYPE)

		frappe.logger().info(
			f"[reconcile_call_log_status_options] Removed drifted override "
			f"'{STATUS_OPTIONS_PROPERTY_SETTER}' (was {current!r}); restored committed options."
		)
	except Exception as error:
		frappe.log_error(
			title="reconcile_call_log_status_options",
			message=f"Failed to reconcile CRM Call Log status options. {error}",
		)

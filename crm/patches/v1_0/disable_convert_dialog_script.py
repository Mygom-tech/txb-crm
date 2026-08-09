"""Retire the `Convert Dialog - Pipeline Type` Form Script.

The convert dialog renders Pipeline Type and Status natively now
(`frontend/src/components/Modals/ConvertToDealModal.vue`), writing both into the payload
`convert_to_deal` already applies.

The script existed because `get_fields_layout` synthesises the dialog's "Required Fields"
from fields that are `reqd` with no default: `status` is required so it appeared, while
`pipeline_type` is not, so it never did. Rather than change the field, the script:

- replaced `window.fetch` for the whole SPA session -- never restored -- to intercept
  `convert_to_deal` and splice the two values into the request body;
- built both selects as raw HTML through a `MutationObserver` on `document.body`;
- hid the real status field with an injected `<style>` tag;
- carried a fourth copy of the pipeline-to-status map, alongside the copies already
  removed with `Pipeline Status Filter` and the wizard;
- redirected by assigning `window.location` from hardcoded view ids (16/17/18/28).

The status list now comes from `crm.txb.api.pipelines.get_pipeline_statuses`, and the
post-convert redirect looks the board up by label instead of by id.

This must ship in the same deploy as the frontend change. Until the row is disabled the
script keeps injecting its own fields, so the dialog would show two Pipeline Type selects
and the patched `fetch` would overwrite what the native fields sent.

Kept for the record; the retirement itself now re-runs on every migrate via
`crm.txb.retired_scripts`, which owns the list this file used to hold.
"""

from crm.txb.retired_scripts import retire_scripts


def execute():
	retire_scripts()

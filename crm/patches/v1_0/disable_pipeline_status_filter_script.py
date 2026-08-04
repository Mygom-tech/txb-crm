"""Retire the `Pipeline Status Filter` Form Script.

The script never worked. Its entry point began:

    _filterStatuses() {
      if (!this._statuses) return;

`_statuses` was never set on the controller. `setupFormController` copies the document's
own keys onto the instance at construction time, and `document._statuses` is only assigned
afterwards, from the script instance itself -- so `this._statuses` was permanently
undefined and the method returned immediately every time. That is why every status showed
in every pipeline.

Its fallback path was worse: a global MutationObserver that hid `<li>` elements by
matching their text, then injected replacement list items which PUT straight to
`/api/resource/CRM Deal/<name>` and forced a page reload.

Filtering now lives in the app: `crm.txb.api.pipelines.get_pipeline_statuses` serves one
mapping, consumed by the deal pages and the side panel status field.
"""

import frappe

SCRIPT = "Pipeline Status Filter"


def execute():
	if not frappe.db.exists("CRM Form Script", SCRIPT):
		return

	if frappe.db.get_value("CRM Form Script", SCRIPT, "enabled") == 0:
		return

	frappe.db.set_value("CRM Form Script", SCRIPT, "enabled", 0)
	frappe.clear_cache()
	frappe.logger().info(f"[disable_pipeline_status_filter_script] Disabled {SCRIPT}")

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

Kept for the record; the retirement itself now re-runs on every migrate via
`crm.txb.retired_scripts`, which owns the list this file used to hold.
"""

from crm.txb.retired_scripts import retire_scripts


def execute():
	retire_scripts()

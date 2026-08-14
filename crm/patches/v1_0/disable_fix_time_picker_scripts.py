"""Retire the `Fix Time Picker` and `Fix Time Picker - Lead` Form Scripts.

Both scripts patched the Lead/Deal time picker at runtime: a `MutationObserver`
watched the popover into existence and an injected `<style>` element raised its
z-index and constrained its minute list so it scrolled instead of clipping.
That behaviour lived only as database rows — invisible to git, unreviewable, and
free to drift between environments — and one observer ran per page for the whole
session.

Both corrections now ship in source: `Field.vue` and `SidePanelLayout.vue` tag
every Lead/Deal Time and Datetime picker with the shared `.crm-datetime-picker`
class (`frontend/src/utils/timePicker.js`), and the matching rules in
`frontend/src/index.css` lift the popover above dialogs/side panels and bound the
time option list so a long minute list scrolls without clipping.

This must ship in the same deploy as the frontend change. Until the rows are
disabled the scripts keep observing and injecting, duplicating the source-level
fix.

Kept for the record; the retirement itself now re-runs on every migrate via
`crm.txb.retired_scripts`, which owns the list this file used to hold.
"""

from crm.txb.retired_scripts import retire_scripts


def execute():
	retire_scripts()

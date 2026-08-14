/**
 * Shared source-level correction for the Lead and Deal time pickers (TXB-151).
 *
 * Two Form Scripts — `Fix Time Picker` (Deal) and `Fix Time Picker - Lead` —
 * used a MutationObserver plus an injected `<style>` element to keep the
 * frappe-ui Date/Time picker popover above dialogs and side panels and to stop
 * its long minute list from clipping. Database Form Scripts are invisible to
 * git and drift between environments, and one MutationObserver runs per page
 * for the whole session.
 *
 * The same two corrections now ship in code: every Lead/Deal Time and Datetime
 * picker tags its root with {@link TIME_PICKER_POPOVER_CLASS}, and the matching
 * rules in `frontend/src/index.css` (a) lift the popover above overlays and
 * (b) bound the time option list so a long minute list scrolls without
 * clipping. Keeping the class here — as a single exported constant both render
 * sites (`Field.vue` and `SidePanelLayout.vue`) import — guarantees Lead and
 * Deal get the identical fix and that the CSS hook cannot silently diverge.
 *
 * The two Form Scripts are retired in `crm/txb/retired_scripts.py`.
 */

/** Class the CSS stacking + scroll-containment rules are keyed on. */
export const TIME_PICKER_POPOVER_CLASS = 'crm-datetime-picker'

/**
 * Attributes bound onto the shared Time/Datetime pickers so Lead and Deal
 * render the corrected popover from one definition.
 *
 * @param {string} [extra] additional space-separated classes to keep.
 * @returns {{ class: string }}
 */
export function timePickerAttrs(extra = '') {
  const classes = [TIME_PICKER_POPOVER_CLASS, extra].filter(Boolean)
  return { class: classes.join(' ') }
}

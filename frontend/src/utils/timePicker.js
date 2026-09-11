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

/** Daily end of the generated coaching option list — the last 15-minute slot. */
export const DEFAULT_TIME_OPTIONS_END = '23:45'

const toMinutes = (hhmm) => {
  const [h, m] = String(hhmm).split(':').map(Number)
  return h * 60 + m
}

const pad2 = (n) => String(n).padStart(2, '0')

/**
 * 15-minute clock labels, inclusive, for a coaching Datetime dropdown (TXB-238).
 *
 * A field declares its daily start (e.g. `"07:00"`) and this yields
 * `['07:00', '07:15', … '23:45']` — the menu the user picks from. The list is a
 * suggestion only: the control still accepts an earlier time typed by hand, so
 * the business-hour default never blocks a valid exception (see AC-2).
 *
 * @param {string} [start='07:00'] first option as `"HH:mm"`.
 * @param {string} [end=DEFAULT_TIME_OPTIONS_END] last option as `"HH:mm"`.
 * @param {number} [stepMinutes=15] spacing between options.
 * @returns {string[]} ordered `"HH:mm"` labels.
 */
export function generateTimeOptions(
  start = '07:00',
  end = DEFAULT_TIME_OPTIONS_END,
  stepMinutes = 15,
) {
  const options = []
  for (let t = toMinutes(start); t <= toMinutes(end); t += stepMinutes) {
    options.push(`${pad2(Math.floor(t / 60))}:${pad2(t % 60)}`)
  }
  return options
}

/**
 * Split a stored `"YYYY-MM-DD HH:mm:ss"` datetime into its date and time halves.
 *
 * The composed control edits the two halves independently, so a blank or
 * partial value has to degrade cleanly rather than throw. Anything falsy or
 * non-string reads as two empty strings.
 *
 * @param {string} value stored datetime.
 * @returns {{ date: string, time: string }}
 */
export function splitDatetime(value) {
  if (!value || typeof value !== 'string') return { date: '', time: '' }
  const [date = '', time = ''] = value.trim().split(' ')
  return { date, time }
}

/**
 * Recombine a date and time into the canonical `"YYYY-MM-DD HH:mm:ss"` string.
 *
 * Seconds are padded so a dropdown pick (`"07:00"`) and a typed exception
 * (`"06:30"`) both round-trip to the exact datetime the server persists (AC-2).
 * With no date there is nothing to store yet; with a date but no time the
 * date-only value is returned so the date edit is not lost mid-entry.
 *
 * @param {string} date `"YYYY-MM-DD"`.
 * @param {string} time `"HH:mm"` or `"HH:mm:ss"`.
 * @returns {string} combined datetime, or `''` when no date is set.
 */
export function combineDatetime(date, time) {
  if (!date) return ''
  if (!time) return date
  const parts = String(time).split(':').slice(0, 3)
  while (parts.length < 3) parts.push('00')
  return `${date} ${parts.map(pad2).join(':')}`
}

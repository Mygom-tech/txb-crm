/**
 * Take Action: run a pipeline transition against a deal.
 *
 * The server owns the transition table. This module only renders what
 * `get_available_actions` offers and posts the result back to `execute_action`,
 * which re-checks the from-state and the role. Nothing here is a security boundary.
 *
 * Replaces a form script that injected its own DOM, decided visibility in the browser,
 * and fired several sequential writes that could half-apply.
 */

import { renderFieldLayoutDialog } from '@/utils/renderFieldLayoutDialog'

/**
 * Build the dropdown entries for the available actions.
 *
 * Pure so it can be tested without a browser: given what the server returned, decide
 * what the menu shows.
 *
 * TXB-192: actions flagged `hidden_from_menu` (Cancel a BAP, Cancel Workshop) stay in the
 * available-actions collection so status-change and Kanban routing can still resolve them,
 * but they are not offered as direct entries in the Take Action dropdown.
 *
 * @param {Array} actions - from get_available_actions
 * @param {Function} onSelect - called with the action when its entry is clicked
 * @returns {Array} dropdown options
 */
export function actionOptions(actions, onSelect) {
  return (actions || [])
    .filter((action) => !action.hidden_from_menu)
    .map((action) => ({
      label: action.label,
      onClick: () => onSelect(action),
    }))
}

/**
 * Fields to render for an action, with defaults resolved.
 *
 * `default: 'Today'` is Frappe's convention; the dialog does not expand it, so it is
 * resolved here rather than shipping a literal "Today" string to the server.
 *
 * @param {Object} action
 * @param {string} today - ISO date, injected so the function stays pure
 */
export function actionFields(action, today) {
  return (action?.fields || []).map((field) => {
    if (field.default === 'Today') {
      return { ...field, default: today }
    }
    return { ...field }
  })
}

/** Fieldnames the server will reject if empty. */
export function requiredFieldnames(action) {
  return (action?.fields || [])
    .filter((field) => field.reqd)
    .map((field) => field.fieldname)
}

/**
 * Seed values for the dialog's reactive document, from each field's resolved default.
 *
 * The dialog builds its reactive document from `defaults`, not from field metadata, so a
 * server-owned read-only value (Log Coaching Call's Total Completed Calls, defaulted per
 * deal from the canonical total_completed_calls) must be handed over here or it renders as
 * an empty read-only box. `default: 'Today'` is resolved to a real date, matching
 * `actionFields`. Caller-supplied `extra` defaults (e.g. a kanban branch value) win.
 *
 * @param {Object} action
 * @param {string} today - ISO date, injected so the function stays pure
 * @param {Object} [extra] - defaults that override field-level ones
 */
export function actionDefaults(action, today, extra = {}) {
  const seeded = {}
  for (const field of action?.fields || []) {
    if (field.default === undefined) continue
    seeded[field.fieldname] = field.default === 'Today' ? today : field.default
  }
  return { ...seeded, ...extra }
}

/**
 * Open an action's form and execute it.
 *
 * Resolves with the server's response, or null when the user cancels.
 */
export async function runAction(deal, action, { today, defaults } = {}) {
  const isoToday = today || new Date().toISOString().split('T')[0]

  const data = await renderFieldLayoutDialog({
    title: __(action.label),
    fields: actionFields(action, isoToday),
    required: requiredFieldnames(action),
    // Seed the reactive document from the server-resolved field defaults so a read-only
    // value (Total Completed Calls) actually renders, then let a kanban drop's branch
    // value override — it pre-selects the column's branch but stays editable, and the
    // card follows the result.
    defaults: actionDefaults(action, isoToday, defaults || {}),
    submitLabel: __('Confirm'),
    cancelLabel: __('Cancel'),
  })

  if (!data) return null

  // Imported lazily so the pure helpers above stay unit-testable: pulling frappe-ui in
  // at module level drags its resource plugin into the test environment.
  const { call } = await import('frappe-ui')

  return await call('crm.txb.api.actions.execute_action', {
    deal,
    action: action.name,
    data,
  })
}

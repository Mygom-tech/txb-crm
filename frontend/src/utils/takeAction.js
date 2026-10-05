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
import {
  actionLandsOn,
  completeActivationReadiness,
  READINESS_CANCELLED,
  READINESS_SAVED,
  STATUS_ACTIVE,
} from '@/utils/activationReadiness'
import { isCoachingPipeline } from '@/utils/dealPresentation'

/** The coaching action the Coaching Notes tab creates through (TXB-275). */
const LOG_COACHING_CALL = 'log_coaching_call'

/** Server error code when a recap is requested but the client has no usable email. */
const RECAP_RECIPIENT_MISSING = 'RECAP_RECIPIENT_MISSING'

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
 * The action the Coaching Notes tab's create control opens, or null when it does not apply.
 *
 * A Coaching Call Note is only ever written by Log Coaching Call, so the Notes tab of a
 * coaching deal offers that action rather than a generic note. Null when the tab is not
 * Coaching Notes or the server does not currently offer the action (wrong state or role).
 *
 * @param {Array} actions - from get_available_actions
 * @param {string} tabName - the active tab's name ('Notes' under either label)
 * @param {string} pipelineType - the deal's pipeline_type
 */
export function coachingNoteAction(actions, tabName, pipelineType) {
  if (tabName !== 'Notes' || !isCoachingPipeline(pipelineType)) return null
  return (
    (actions || []).find((action) => action.name === LOG_COACHING_CALL) || null
  )
}

/** A fresh identity for one dialog open; retries of that open reuse it. */
function newSubmissionId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  // randomUUID needs a secure context; a plain-HTTP site still needs a unique token.
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

/**
 * The `data` posted to execute_action.
 *
 * Every attempt carries the dialog's submission_id so the server replays a retried submit
 * instead of writing it twice. An action offering `send_recap` posts it as 0|1: the coach
 * unticking it is a deliberate opt-out, while a missing value keeps the server default (1).
 *
 * @param {Object} action
 * @param {Object} data - the dialog's submitted values
 * @param {string} submissionId
 */
function actionPayload(action, data, submissionId) {
  const payload = { ...data, submission_id: submissionId }
  if ((action?.fields || []).some((field) => field.fieldname === 'send_recap')) {
    const value = data?.send_recap
    payload.send_recap = value === undefined || value === null || value ? 1 : 0
  }
  return payload
}

/**
 * The in-form message for a failure the coach can fix without losing their entries, or null.
 *
 * A missing recap recipient is fixed by correcting the email or unticking the recap; a
 * network failure by submitting again. Either way the same submission_id is resent.
 */
function recoverableFailureMessage(error) {
  const texts = [error?.message, ...(error?.messages || [])]
  if (texts.some((text) => String(text || '').includes(RECAP_RECIPIENT_MISSING))) {
    return __(
      'The client has no usable primary email. Fix the email on the primary contact, or untick "Email the recap to the client" to log the call without a recap.',
    )
  }
  // fetch rejects with a TypeError when the request never reached the server.
  if (error instanceof TypeError) {
    return __('Could not reach the server. Your entries are kept; submit again to retry.')
  }
  return null
}

/**
 * Open an action's form and execute it.
 *
 * Resolves with the server's response, or null when the user cancels. A recoverable failure
 * (see recoverableFailureMessage) keeps the form open with its values and the same
 * submission_id until the coach resubmits or cancels; any other failure closes the form
 * and rejects, as before.
 */
export async function runAction(deal, action, { today, defaults } = {}) {
  const isoToday = today || new Date().toISOString().split('T')[0]
  const submissionId = newSubmissionId()

  // Imported lazily so the pure helpers above stay unit-testable: pulling frappe-ui in
  // at module level drags its resource plugin into the test environment.
  const { call } = await import('frappe-ui')
  const post = (values) =>
    call('crm.txb.api.actions.execute_action', {
      deal,
      action: action.name,
      data: actionPayload(action, values, submissionId),
    })

  let result = null
  let failure = null
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
    // Throwing keeps the form open with the message and the entered values.
    async onSubmit(values) {
      // Active-bound actions post after the readiness check below, once the form closes.
      if (actionLandsOn(action, values) === STATUS_ACTIVE) return
      failure = null
      try {
        result = await post(values)
      } catch (error) {
        const message = recoverableFailureMessage(error)
        if (message) throw new Error(message)
        failure = error
      }
    },
  })

  if (!data) return null
  if (failure) throw failure
  if (actionLandsOn(action, data) !== STATUS_ACTIVE) return result

  // TXB-259: an action landing on Active may first need delivery readiness completed. The
  // helper asks the server, and when something is missing it collects it and resumes this
  // exact action through the atomic complete_activation; otherwise we post as before.
  const readiness = await completeActivationReadiness(deal, {
    action: action.name,
    data: actionPayload(action, data, submissionId),
  })
  if (readiness.outcome === READINESS_SAVED) return readiness.result
  if (readiness.outcome === READINESS_CANCELLED) return null

  return await post(data)
}

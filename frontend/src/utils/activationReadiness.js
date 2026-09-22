/**
 * Delivery readiness completion for Active-bound Delivering Coaching transitions (TXB-259).
 *
 * Every interactive path that moves a Delivering Coaching opportunity into Active -- Take
 * Action, a Kanban drop, the desktop header/side-panel status control and the mobile status
 * control -- routes through `completeActivationReadiness` before it commits anything. The
 * server owns the rules: `get_activation_readiness` says whether the deal is ready and which
 * editable fields are missing, and `complete_activation` fills them and runs the original
 * transition in one savepoint. Nothing here re-implements the readiness conditions.
 */

import { renderFieldLayoutDialog } from '@/utils/renderFieldLayoutDialog'
import { PIPELINE_DELIVERING_COACHING } from '@/utils/pipelineLayout'

export const STATUS_ACTIVE = 'Active'

/** The deal was not missing anything (or the preflight did not apply): run the original path. */
export const READINESS_NOT_REQUIRED = 'not_required'
/** The readiness patch and the transition were committed together. */
export const READINESS_SAVED = 'saved'
/** The user dismissed the dialog; nothing was written. */
export const READINESS_CANCELLED = 'cancelled'

/**
 * Where an action lands given the values the user filled in, mirroring the server's
 * `resolve_to_state`: a fixed `to_state` wins, otherwise `to_state_map` picks a branch.
 */
export function actionLandsOn(action, data = {}) {
  if (action?.to_state) return action.to_state
  for (const [fieldname, targets] of Object.entries(action?.to_state_map || {})) {
    const target = targets?.[data?.[fieldname]]
    if (target) return target
  }
  return null
}

/** Whether a direct status write needs the readiness preflight. */
export function isActivationStatusChange(pipeline, from, to) {
  return (
    pipeline === PIPELINE_DELIVERING_COACHING &&
    to === STATUS_ACTIVE &&
    from !== STATUS_ACTIVE
  )
}

/**
 * Dialog fields from the server's `fields` contract: only the missing editable inputs, in
 * the server's order. A field the user may not edit (Delivery Coach for a non-Admin) is shown
 * read-only so the user can see what blocks the move.
 */
export function readinessFields(fields) {
  return (fields || []).map((field) => ({
    fieldname: field.fieldname,
    label: field.label,
    fieldtype: field.fieldtype,
    options: field.options,
    reqd: field.read_only ? 0 : 1,
    read_only: field.read_only ? 1 : 0,
  }))
}

/** Seed the dialog with each missing field's current value. */
export function readinessDefaults(fields) {
  const defaults = {}
  for (const field of fields || []) {
    if (field.value !== undefined && field.value !== null) {
      defaults[field.fieldname] = field.value
    }
  }
  return defaults
}

/**
 * The allowlisted patch to send: only rendered, editable fields. Fields the deal already
 * satisfies are never in the contract, so they are never sent and never overwritten.
 */
export function readinessPatch(fields, data) {
  const patch = {}
  for (const field of fields || []) {
    if (field.read_only) continue
    if (data && field.fieldname in data) patch[field.fieldname] = data[field.fieldname]
  }
  return patch
}

/**
 * Field-level check against the server-provided `required_value` (e.g. Contract Signed?
 * must be "Yes"). Returns the label of the first field whose answer cannot satisfy the
 * server, or null.
 */
export function unmetRequiredValue(fields, data) {
  for (const field of fields || []) {
    if (field.read_only || !field.required_value) continue
    if (data?.[field.fieldname] !== field.required_value) return field
  }
  return null
}

export function serverMessage(error, fallback) {
  const message = error?.messages?.[0] || error?.message || fallback
  // Frappe messages may carry markup; the dialog renders plain text.
  return String(message).replace(/<[^>]*>/g, '')
}

/**
 * Preflight the Active-bound request and, when readiness is missing, open one completion
 * dialog and commit the patch plus the original transition atomically.
 *
 * @param {string} deal
 * @param {Object} request - `{ action, data }` for a Take Action, or `{ status: 'Active' }`
 *                           for a direct status write
 * @returns {Promise<{outcome: string, result?: Object}>}
 *   READINESS_NOT_REQUIRED -> the caller runs its original request unchanged;
 *   READINESS_SAVED -> `result` is the server response, the caller must not write again;
 *   READINESS_CANCELLED -> nothing was written, the caller restores the prior state.
 */
export async function completeActivationReadiness(deal, request) {
  // Lazy, as in takeAction.js, so the pure helpers stay unit-testable.
  const { call } = await import('frappe-ui')

  let readiness
  try {
    readiness = await call('crm.txb.api.actions.get_activation_readiness', {
      deal,
      action: request.action || null,
      status: request.status || null,
    })
  } catch (error) {
    // The preflight refuses requests it does not gate (another pipeline, a non-Active
    // branch) and ones the user may not make. The original path re-checks everything and
    // reports its own error, so defer to it rather than inventing one here.
    return { outcome: READINESS_NOT_REQUIRED }
  }

  if (!readiness || readiness.ready || !readiness.fields?.length) {
    return { outcome: READINESS_NOT_REQUIRED }
  }

  const fields = readiness.fields
  let result = null

  const data = await renderFieldLayoutDialog({
    title: __('Complete delivery readiness'),
    fields: readinessFields(fields),
    defaults: readinessDefaults(fields),
    required: fields.filter((f) => !f.read_only).map((f) => f.fieldname),
    submitLabel: __('Save and move to {0}', [__(STATUS_ACTIVE)]),
    cancelLabel: __('Cancel'),
    // Throwing keeps the dialog open with the message, so an invalid answer or a server /
    // stale-state refusal is shown in place; the savepoint has already undone any write.
    async onSubmit(values) {
      const unmet = unmetRequiredValue(fields, values)
      if (unmet) {
        throw new Error(
          __('{0} must be "{1}" before this opportunity can become {2}.', [
            __(unmet.label),
            __(unmet.required_value),
            __(STATUS_ACTIVE),
          ]),
        )
      }
      try {
        result = await call('crm.txb.api.actions.complete_activation', {
          deal,
          readiness: readinessPatch(fields, values),
          action: request.action || null,
          data: request.action ? request.data || {} : null,
          status: request.action ? null : request.status,
          expected_status: readiness.status,
        })
      } catch (error) {
        throw new Error(serverMessage(error, __('Could not complete delivery readiness')))
      }
    },
  })

  if (!data || !result) return { outcome: READINESS_CANCELLED }
  return { outcome: READINESS_SAVED, result }
}

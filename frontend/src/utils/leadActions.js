/**
 * Lead actions: the shared contract every entry point into "Contacted" must honour.
 *
 * TXB-128 replaces the old "Qualifying call" status with "Contacted". Moving a Lead
 * into Contacted is no longer a bare status flip: it must record a canonical reach
 * activity ("Log a reach"). This module owns the shape and validation of that reach so
 * the sidebar, the kanban drop, and the status dropdown all enforce the same rule
 * instead of each re-deciding it in the browser.
 *
 * Everything here is pure so it can be unit-tested without a browser. The server still
 * owns the write; the atomic status+activity save lives behind
 * `crm.txb.api.actions.log_reach`, which re-checks the actor and the from-state.
 */

/** Canonical target status a reach unlocks. */
export const CONTACTED_STATUS = 'Contacted'

/** Legacy status name migrated into {@link CONTACTED_STATUS}. Kept so a stale board
 * column or a cached filter still resolves to the reach gate. */
export const LEGACY_CONTACTED_STATUS = 'Qualifying call'

/**
 * Does moving from `fromStatus` to `toStatus` require a Log a reach?
 *
 * Entering Contacted (from anywhere but Contacted itself) is the only gate. Re-saving a
 * Lead that is already Contacted, or moving it onward, does not re-prompt — the reach is
 * recorded once, on entry. The legacy name resolves to the same target so an
 * un-migrated board still gates correctly.
 */
export function requiresReach(fromStatus, toStatus) {
  const target = normalizeStatus(toStatus)
  if (target !== CONTACTED_STATUS) return false
  return normalizeStatus(fromStatus) !== CONTACTED_STATUS
}

/** Fold the retired status name onto its canonical replacement. */
export function normalizeStatus(status) {
  return status === LEGACY_CONTACTED_STATUS ? CONTACTED_STATUS : status
}

/** Fields the Log a reach dialog renders. Required flags mirror the server contract. */
export function reachFields() {
  return [
    {
      fieldname: 'summary',
      label: __('Reach summary'),
      fieldtype: 'Small Text',
      reqd: 1,
    },
    {
      fieldname: 'follow_up_context',
      label: __('Follow-up context'),
      fieldtype: 'Small Text',
      reqd: 1,
    },
    {
      fieldname: 'follow_up_date',
      label: __('Follow-up date'),
      fieldtype: 'Date',
      reqd: 0,
    },
  ]
}

/** Fieldnames the server will reject if empty. */
export function requiredReachFields() {
  return reachFields()
    .filter((field) => field.reqd)
    .map((field) => field.fieldname)
}

/**
 * Validate a Log a reach payload.
 *
 * Returns the list of missing required fieldnames; an empty list means valid. Whitespace
 * is not a value, so a summary of spaces is treated as absent — the same rule the server
 * enforces, checked here so the dialog can block submit before the round trip.
 */
export function validateReach(data) {
  const doc = data || {}
  return requiredReachFields().filter((fieldname) => !isFilled(doc[fieldname]))
}

/** True when the reach payload has every required field. */
export function isReachValid(data) {
  return validateReach(data).length === 0
}

function isFilled(value) {
  if (value === undefined || value === null) return false
  return String(value).trim().length > 0
}

/**
 * Build the atomic reach payload: the activity plus the status it unlocks.
 *
 * `timestamp` and `actor` are stamped here from injected values so the function stays
 * pure and testable. The server re-stamps them authoritatively; sending them keeps the
 * optimistic UI honest. Returns null when the payload is invalid, so a caller cannot
 * accidentally post an empty reach.
 */
export function buildReachActivity(data, { actor, now } = {}) {
  if (!isReachValid(data)) return null
  const doc = data || {}
  const followUp = isFilled(doc.follow_up_date) ? doc.follow_up_date : null
  return {
    status: CONTACTED_STATUS,
    activity: {
      type: 'reach',
      timestamp: now || new Date().toISOString(),
      actor: actor || null,
      summary: String(doc.summary).trim(),
      follow_up_context: String(doc.follow_up_context).trim(),
      follow_up_date: followUp,
    },
  }
}

/**
 * Prompt for a reach, then atomically save the activity and the Contacted status.
 *
 * Resolves with the server's response, or null when the user cancels — cancelling leaves
 * the Lead's status untouched, since nothing is posted.
 */
export async function logReach(lead, { actor, today } = {}) {
  const { renderFieldLayoutDialog } = await import('@/utils/renderFieldLayoutDialog')

  const data = await renderFieldLayoutDialog({
    title: __('Log a reach'),
    fields: reachFields(),
    required: requiredReachFields(),
    submitLabel: __('Log reach'),
    cancelLabel: __('Cancel'),
  })

  // Cancel (or an invalid payload the dialog should have blocked): do not touch status.
  if (!data) return null

  const payload = buildReachActivity(data, {
    actor,
    now: today ? `${today}T00:00:00` : new Date().toISOString(),
  })
  if (!payload) return null

  // Imported lazily so the pure helpers above stay unit-testable without dragging
  // frappe-ui's resource plugin into the test environment.
  const { call } = await import('frappe-ui')

  return await call('crm.txb.api.actions.log_reach', {
    lead,
    status: payload.status,
    activity: payload.activity,
  })
}

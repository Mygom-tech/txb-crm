/**
 * Lead actions: the shared client-side contracts for the two guarded Lead transitions.
 *
 * Two independent gates live here, each mirroring a server rule so every surface (sidebar,
 * Kanban drop, status dropdown, Take Action) renders one identical dialog and cannot drift
 * from the server:
 *
 * - Log a reach (TXB-128): moving a Lead into "Contacted" (which replaces the retired
 *   "Qualifying call" status) is no longer a bare status flip -- it must record a canonical
 *   reach activity. The atomic status+activity save lives behind
 *   `crm.txb.api.actions.log_reach`, which re-checks the actor and the from-state.
 * - Log a dial: "Contact attempted" is reachable only by logging a dial. The server owns the
 *   rule (crm.txb.lead_actions); `guard_contact_attempted` refuses any other write to that
 *   status, and the atomic save lives behind `crm.txb.lead_actions.log_a_dial`.
 *
 * The pure helpers stay framework-free so the unit suite can exercise both contracts without a
 * browser; frappe-ui is imported lazily inside logReach/logADial for the same reason.
 */

import { renderFieldLayoutDialog } from '@/utils/renderFieldLayoutDialog'

// -----------------------------------------------------------------------------------------
// Log a reach (TXB-128): entering "Contacted"
// -----------------------------------------------------------------------------------------

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

// -----------------------------------------------------------------------------------------
// Log a dial: entering "Contact attempted"
// -----------------------------------------------------------------------------------------

/** The status a Lead may only enter by logging a dial. */
export const CONTACT_ATTEMPTED_STATUS = 'Contact attempted'

/**
 * The dial contract. The result is a read-only, server-fixed field: a dial that only reaches
 * Contact attempted is a "No answer" by definition, so it is never asked of the user and the
 * server re-asserts it regardless of what a client sends.
 */
export const LOG_A_DIAL = {
  name: 'log_a_dial',
  label: 'Log a dial',
  to_state: CONTACT_ATTEMPTED_STATUS,
  changes_status: true,
  fields: [
    {
      fieldname: 'dialed_at',
      label: 'Dialed At',
      fieldtype: 'Datetime',
      reqd: 1,
      default: 'Now',
    },
    {
      fieldname: 'dial_result',
      label: 'Result',
      fieldtype: 'Data',
      read_only: 1,
      default: 'No answer',
    },
    { fieldname: 'notes', label: 'Notes', fieldtype: 'Small Text' },
    {
      fieldname: 'follow_up_date',
      label: 'Follow-up Date',
      fieldtype: 'Datetime',
    },
  ],
}

/**
 * The action's fields with Frappe's magic defaults resolved. "Today" is a date, "Now" a
 * datetime; anything else passes through. The source action is never mutated -- the shared
 * LOG_A_DIAL constant is reused on every open.
 *
 * @param {Object} action
 * @param {string} now - ISO datetime, injected so the function stays pure
 */
export function dialFields(action = LOG_A_DIAL, now) {
  const today = (now || '').split('T')[0]
  return (action?.fields || []).map((field) => {
    if (field.default === 'Now') return { ...field, default: now }
    if (field.default === 'Today') return { ...field, default: today }
    return { ...field }
  })
}

/**
 * Fieldnames the user must fill. Read-only fields carry a server-fixed value and are never
 * asked of the user, so they are excluded even when marked required.
 */
export function requiredDialFields(action = LOG_A_DIAL) {
  return (action?.fields || [])
    .filter((field) => field.reqd && !field.read_only)
    .map((field) => field.fieldname)
}

/**
 * Seed values for the dialog's reactive document, from each field's resolved default. The
 * fixed result is included so a submit that never touched it still carries one. Caller-supplied
 * `extra` defaults win.
 */
export function dialDefaults(action = LOG_A_DIAL, now, extra = {}) {
  const seeded = {}
  for (const field of dialFields(action, now)) {
    if (field.default !== undefined) seeded[field.fieldname] = field.default
  }
  return { ...seeded, ...extra }
}

/** Whether every required field holds a value; empty string, null and undefined are missing. */
export function canLogDial(values = {}, action = LOG_A_DIAL) {
  return requiredDialFields(action).every((fieldname) => {
    const value = values[fieldname]
    return value !== undefined && value !== null && value !== ''
  })
}

/**
 * Whether a move into `status` is guarded by the dial contract. The status dropdown and the
 * Kanban board call this to decide whether to open Log a dial instead of writing the status.
 */
export function requiresDial(status) {
  return status === CONTACT_ATTEMPTED_STATUS
}

/**
 * The payload posted to crm.txb.lead_actions.log_a_dial. Only the contract's own fields
 * travel, and the fixed result is re-asserted so a client that dropped the read-only field
 * cannot submit a dial without one. The server re-derives the actor and re-validates.
 */
export function dialPayload(values = {}, action = LOG_A_DIAL) {
  const names = (action?.fields || []).map((field) => field.fieldname)
  const payload = {}
  for (const name of names) {
    if (values[name] !== undefined) payload[name] = values[name]
  }
  payload.dial_result = 'No answer'
  return payload
}

/**
 * Open the Log a dial form and, on submit, record the dial atomically server-side.
 *
 * Resolves with the server's response, or null when the user cancels -- and a cancel leaves
 * the Lead's status untouched, because nothing is sent. The status only ever moves as part of
 * the server's atomic log_a_dial, never optimistically here.
 *
 * @param {string} lead - Lead docname
 * @param {Object} [opts]
 * @param {string} [opts.now] - ISO datetime, injected so callers can pin it in tests
 * @param {Object} [opts.defaults] - extra field defaults (e.g. a Kanban prefill)
 */
export async function logADial(lead, { now, defaults } = {}) {
  const isoNow = now || new Date().toISOString().slice(0, 19)

  const data = await renderFieldLayoutDialog({
    title: __(LOG_A_DIAL.label),
    fields: dialFields(LOG_A_DIAL, isoNow),
    required: requiredDialFields(LOG_A_DIAL),
    defaults: dialDefaults(LOG_A_DIAL, isoNow, defaults || {}),
    submitLabel: __('Log a dial'),
    cancelLabel: __('Cancel'),
  })

  // Cancel: no request, so the status stays exactly where it was.
  if (!data) return null

  // Imported lazily so the pure helpers above stay unit-testable without frappe-ui's resource
  // plugin being dragged into the test environment.
  const { call } = await import('frappe-ui')

  return await call('crm.txb.lead_actions.log_a_dial', {
    lead,
    data: dialPayload(data),
  })
}

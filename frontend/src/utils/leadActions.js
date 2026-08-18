/**
 * Log a dial: the one guarded Lead transition, rendered for the browser.
 *
 * "Contact attempted" is reachable only by logging a dial. The server owns the rule
 * (crm.txb.lead_actions): a Kanban drag, the status dropdown and Take Action all route
 * through it, and crm.txb.lead_actions.guard_contact_attempted refuses any other write to
 * that status. This module is the single client-side description of the same action, so the
 * surfaces that open the form all render one identical dialog and cannot drift from the
 * server -- mirroring how @/utils/takeAction relates to crm.txb.api.actions for deals.
 *
 * The pure helpers stay framework-free so the unit suite can exercise the contract without a
 * browser; frappe-ui is imported lazily inside logADial for the same reason.
 */

import { renderFieldLayoutDialog } from '@/utils/renderFieldLayoutDialog'
import { conversionPipelineTypes } from '@/utils/pipelineStatuses'

/** The status a Lead may only enter by logging a dial. */
export const CONTACT_ATTEMPTED_STATUS = 'Contact attempted'

/**
 * The booked-meeting status Run Discovery Meeting acts from. It is a guarded action available
 * only here, never a durable "meeting run" resting status -- the submit applies one outcome and
 * moves the lead on in the same server transaction. Mirrors the server's
 * LEAD_STATUS_DISCOVERY_MEETING_SET (crm/txb/constants.py).
 */
export const DISCOVERY_MEETING_SET_STATUS = 'Discovery meeting set'

/**
 * The three discovery outcomes that keep the lead a lead, each a resting Lead status.
 * "Not interested" and "Disqualified" are terminal -- Admin-reopenable only, enforced by the
 * server; "Nurture" is a warm resting state that stays freely movable.
 */
export const DISCOVERY_STATUS_OUTCOMES = ['Nurture', 'Not interested', 'Disqualified']

/** The terminal discovery outcomes: once a lead rests here, only an Admin may reopen it. */
export const DISCOVERY_TERMINAL_OUTCOMES = ['Not interested', 'Disqualified']

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

/**
 * The Run Discovery Meeting contract, kept in step with the server's copy in
 * crm/txb/lead_actions.py. A guarded action from Discovery meeting set that requires notes and
 * exactly one of six approved outcomes; the server re-derives and enforces the same rule.
 */
export const RUN_DISCOVERY_MEETING = {
  name: 'run_discovery_meeting',
  label: 'Run Discovery Meeting',
  from_state: DISCOVERY_MEETING_SET_STATUS,
  changes_status: true,
  fields: [
    { fieldname: 'notes', label: 'Meeting Notes', fieldtype: 'Small Text', reqd: 1 },
    { fieldname: 'outcome', label: 'Outcome', fieldtype: 'Select', reqd: 1 },
  ],
}

/**
 * The six approved discovery outcomes, in display order: the three non-conversion resting
 * statuses followed by the three convertible pipelines. Matches the server's
 * discovery_outcomes(), so the two offer exactly the same choices.
 *
 * @returns {string[]}
 */
export function discoveryOutcomes() {
  return [...DISCOVERY_STATUS_OUTCOMES, ...conversionPipelineTypes()]
}

/** Whether an outcome converts the lead rather than resting it at a Lead status. */
export function isConversionOutcome(outcome) {
  return conversionPipelineTypes().includes(outcome)
}

/** Whether an outcome is terminal -- reopenable only by an Admin, once applied. */
export function isTerminalDiscoveryOutcome(outcome) {
  return DISCOVERY_TERMINAL_OUTCOMES.includes(outcome)
}

/**
 * The action's fields with the outcome Select's options resolved to the six approved outcomes.
 * Options are the newline-delimited string a Frappe Select expects (the dialog's FieldLayout
 * splits it), matching the server contract's copy. The source contract is never mutated.
 */
export function discoveryFields(action = RUN_DISCOVERY_MEETING) {
  return (action?.fields || []).map((field) =>
    field.fieldname === 'outcome'
      ? { ...field, options: discoveryOutcomes().join('\n') }
      : { ...field },
  )
}

/** Fieldnames the user must fill: the notes and the single outcome. */
export function requiredDiscoveryFields(action = RUN_DISCOVERY_MEETING) {
  return (action?.fields || [])
    .filter((field) => field.reqd)
    .map((field) => field.fieldname)
}

/**
 * Whether a discovery meeting may be run from `status`. The header action and any status
 * control call this to decide whether Run Discovery Meeting applies.
 */
export function requiresDiscovery(status) {
  return status === DISCOVERY_MEETING_SET_STATUS
}

/**
 * Whether a submission is complete: non-empty notes and exactly one of the six approved
 * outcomes. A blank outcome or one outside the set is rejected, as is missing notes.
 */
export function canRunDiscovery(values = {}) {
  const notes = typeof values.notes === 'string' ? values.notes.trim() : ''
  if (!notes) return false
  return discoveryOutcomes().includes(values.outcome)
}

/**
 * The payload posted to crm.txb.lead_actions.run_discovery_meeting. Only the contract's own
 * fields travel; the server re-derives the actor and re-validates notes and outcome.
 */
export function discoveryPayload(values = {}, action = RUN_DISCOVERY_MEETING) {
  const names = (action?.fields || []).map((field) => field.fieldname)
  const payload = {}
  for (const name of names) {
    if (values[name] !== undefined) payload[name] = values[name]
  }
  return payload
}

/**
 * Open the Run Discovery Meeting form and, on submit, apply the outcome atomically server-side.
 *
 * Resolves with the server's response, or null when the user cancels -- and a cancel leaves the
 * lead at Discovery meeting set, because nothing is sent. The status only ever moves as part of
 * the server's atomic run_discovery_meeting, which records the notes and, for a conversion
 * outcome, reuses the Contact/Opportunity conversion authority; never optimistically here.
 *
 * @param {string} lead - Lead docname
 * @param {Object} [opts]
 * @param {Object} [opts.defaults] - extra field defaults
 */
export async function runDiscoveryMeeting(lead, { defaults } = {}) {
  const data = await renderFieldLayoutDialog({
    title: __(RUN_DISCOVERY_MEETING.label),
    fields: discoveryFields(RUN_DISCOVERY_MEETING),
    required: requiredDiscoveryFields(RUN_DISCOVERY_MEETING),
    defaults: { ...(defaults || {}) },
    submitLabel: __('Run Discovery Meeting'),
    cancelLabel: __('Cancel'),
  })

  // Cancel: no request, so the lead stays exactly at Discovery meeting set.
  if (!data) return null

  const { call } = await import('frappe-ui')

  return await call('crm.txb.lead_actions.run_discovery_meeting', {
    lead,
    data: discoveryPayload(data),
  })
}

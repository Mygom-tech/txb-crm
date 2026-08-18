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
import { conversionPipelineTypes } from '@/utils/pipelineStatuses'

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

// -----------------------------------------------------------------------------------------
// Schedule Discovery meeting (TXB-129): entering "Discovery meeting set"
// -----------------------------------------------------------------------------------------

/** The status a Lead may only enter by scheduling a discovery meeting. Ordered right after
 * Contacted: a lead is contacted, then a discovery meeting is set. Aliases the Run Discovery
 * Meeting contract's booked-meeting status so the two features share one literal. */
export const DISCOVERY_STATUS = DISCOVERY_MEETING_SET_STATUS

/** The two meeting types. Each unlocks a different required location detail. */
export const DISCOVERY_TYPE_VIRTUAL = 'Virtual'
export const DISCOVERY_TYPE_ONSITE = 'Onsite'

/**
 * Does moving from `fromStatus` to `toStatus` require scheduling a discovery meeting?
 *
 * Entering Discovery meeting set (from anywhere but itself) is the only gate. Re-saving a
 * Lead already in that status, or moving it onward, does not re-prompt — the meeting is
 * scheduled once, on entry.
 */
export function requiresDiscoverySchedule(fromStatus, toStatus) {
  if (normalizeStatus(toStatus) !== DISCOVERY_STATUS) return false
  return normalizeStatus(fromStatus) !== DISCOVERY_STATUS
}

/**
 * Fields the Schedule Discovery meeting dialog renders. Date, time and type are always
 * required; the manual link is required (and shown) only for a Virtual meeting, the address
 * only for an Onsite one. No link is generated and no calendar entry is created — the link is
 * whatever the user types.
 */
export function discoveryScheduleFields() {
  const virtual = `eval:doc.meeting_type == '${DISCOVERY_TYPE_VIRTUAL}'`
  const onsite = `eval:doc.meeting_type == '${DISCOVERY_TYPE_ONSITE}'`
  return [
    {
      fieldname: 'meeting_date',
      label: __('Meeting date'),
      fieldtype: 'Date',
      reqd: 1,
    },
    {
      fieldname: 'meeting_time',
      label: __('Meeting time'),
      fieldtype: 'Time',
      reqd: 1,
    },
    {
      fieldname: 'meeting_type',
      label: __('Meeting type'),
      fieldtype: 'Select',
      options: `${DISCOVERY_TYPE_VIRTUAL}\n${DISCOVERY_TYPE_ONSITE}`,
      reqd: 1,
    },
    {
      fieldname: 'meeting_link',
      label: __('Meeting link'),
      fieldtype: 'Data',
      depends_on: virtual,
      mandatory_depends_on: virtual,
    },
    {
      fieldname: 'meeting_address',
      label: __('Meeting address'),
      fieldtype: 'Small Text',
      depends_on: onsite,
      mandatory_depends_on: onsite,
    },
  ]
}

/**
 * The fieldnames that must be filled for the given payload. Date, time and type are always
 * required; the conditional location detail depends on the chosen type. Mirrors the server
 * contract so the dialog can block an incomplete submit before the round trip.
 */
export function requiredDiscoveryScheduleFields(data) {
  const base = ['meeting_date', 'meeting_time', 'meeting_type']
  const type = (data || {}).meeting_type
  if (type === DISCOVERY_TYPE_VIRTUAL) return [...base, 'meeting_link']
  if (type === DISCOVERY_TYPE_ONSITE) return [...base, 'meeting_address']
  return base
}

/**
 * Validate a discovery payload. Returns the list of missing required fieldnames; an empty
 * list means valid. Whitespace is not a value, matching the server's emptiness rule.
 */
export function validateDiscovery(data) {
  const doc = data || {}
  return requiredDiscoveryScheduleFields(doc).filter((fieldname) => !isFilled(doc[fieldname]))
}

/** True when the discovery payload has every required field for its type. */
export function isDiscoveryValid(data) {
  return validateDiscovery(data).length === 0
}

/**
 * Build the atomic discovery payload: the scheduling activity plus the status it unlocks.
 *
 * Only the location detail matching the chosen type travels — an Onsite meeting never carries
 * a link and a Virtual one never carries an address. Returns null when the payload is invalid,
 * so a caller cannot post an incomplete schedule. The server re-validates authoritatively.
 */
export function buildDiscoveryActivity(data, { actor, now } = {}) {
  if (!isDiscoveryValid(data)) return null
  const doc = data || {}
  const type = doc.meeting_type
  return {
    status: DISCOVERY_STATUS,
    activity: {
      type: 'discovery',
      timestamp: now || new Date().toISOString(),
      actor: actor || null,
      meeting_date: String(doc.meeting_date).trim(),
      meeting_time: String(doc.meeting_time).trim(),
      meeting_type: type,
      meeting_link:
        type === DISCOVERY_TYPE_VIRTUAL ? String(doc.meeting_link).trim() : null,
      meeting_address:
        type === DISCOVERY_TYPE_ONSITE ? String(doc.meeting_address).trim() : null,
    },
  }
}

/**
 * Prompt for the discovery details, then atomically save the scheduling activity and the
 * Discovery meeting set status server-side.
 *
 * The conditional required fields (link for Virtual, address for Onsite) are re-checked in
 * `onSubmit`, which throws to keep the dialog open on an incomplete submit. Resolves with the
 * server's response, or null when the user cancels — a cancel posts nothing, so the status is
 * left untouched.
 */
export async function logDiscovery(lead, { actor, now } = {}) {
  const isoNow = now || new Date().toISOString()

  const data = await renderFieldLayoutDialog({
    title: __('Schedule Discovery meeting'),
    fields: discoveryScheduleFields(),
    required: ['meeting_date', 'meeting_time', 'meeting_type'],
    submitLabel: __('Schedule meeting'),
    cancelLabel: __('Cancel'),
    onSubmit: (formData) => {
      // Conditional requiredness the static `required` list cannot express: keep the dialog
      // open until the location detail for the chosen type is supplied.
      if (validateDiscovery(formData).length) {
        throw new Error(
          __('Provide the meeting date, time, type, and the required location detail.'),
        )
      }
    },
  })

  // Cancel (or a submit the guard above kept out): do not touch status, nothing is posted.
  if (!data) return null

  const payload = buildDiscoveryActivity(data, { actor, now: isoNow })
  if (!payload) return null

  // Imported lazily so the pure helpers above stay unit-testable without dragging frappe-ui's
  // resource plugin into the test environment.
  const { call } = await import('frappe-ui')

  return await call('crm.txb.api.actions.schedule_discovery', {
    lead,
    status: payload.status,
    activity: payload.activity,
  })
}

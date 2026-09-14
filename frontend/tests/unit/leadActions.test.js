import { readFileSync } from 'node:fs'

import { describe, it, expect, vi, beforeEach } from 'vitest'

// TXB-236: the Discovery scheduling regressions below drive the REAL logDiscovery and the shared
// resolveLeadStatusTransition / requestKanbanTransition routing, mocking only the two outermost
// seams — frappe-ui's `call` (the server round trip) and renderFieldLayoutDialog (the shared
// dialog). The dialog mock is driven with the exact snapshot a committed TimePicker selection /
// typed time produces and reproduces FieldLayoutDialog's submit semantics (run onSubmit; a throw
// keeps it open and posts nothing; otherwise resolve the localDoc snapshot). The pure-helper
// suites above never touch these seams, so the mocks do not affect them.
const discoveryMocks = vi.hoisted(() => ({
  call: vi.fn(),
  dialogDoc: { current: null }, // the committed localDoc; `null` models a cancel/dismiss
}))

vi.mock('frappe-ui', () => ({ call: discoveryMocks.call }))

vi.mock('@/utils/renderFieldLayoutDialog', () => ({
  renderFieldLayoutDialog: async (options) => {
    const doc = discoveryMocks.dialogDoc.current
    if (doc === null) return null
    try {
      if (options.onSubmit) await options.onSubmit({ ...doc })
    } catch {
      return null // validation threw: dialog stays open, nothing posted
    }
    return { ...doc } // the submit snapshot FieldLayoutDialog emits on success
  },
}))

// Kanban pulls in the generic-confirm dialog seam; stub it as kanbanTransitions.test.js does.
vi.mock('@/utils/dialogs', () => ({ createDialog: vi.fn() }))

import {
  CONTACT_ATTEMPTED_STATUS,
  DISCOVERY_STATUS,
  discoveryScheduleFields,
  logDiscovery,
  resolveLeadStatusTransition,
  LEAD_TRANSITION_SAVED,
  LEAD_TRANSITION_CANCELLED,
  LEAD_TRANSITION_FAILED,
  LOG_A_DIAL,
  reachFields,
  requiresDial,
  requiredDialFields,
  dialFields,
  dialDefaults,
  canLogDial,
  dialPayload,
  FOLLOW_UP_STATUS,
  NURTURE_STATUS,
  requiresFollowUp,
  requiresNurture,
  followUpFields,
  requiredFollowUpFields,
  validateFollowUp,
  isFollowUpValid,
  buildFollowUpActivity,
  nurtureFields,
  requiredNurtureFields,
  validateNurture,
  isNurtureValid,
  buildNurtureActivity,
  isRetiredLeadStatus,
  RETIRED_LEAD_STATUSES,
} from '@/utils/leadActions'
import { requestKanbanTransition } from '@/utils/kanbanTransitions'

// The Log a dial contract is the single shared gate every surface -- desktop Lead.vue, the
// responsive MobileLead.vue header and Details/Data controls, and the Kanban board -- routes
// through before writing "Contact attempted". These pure-helper tests pin that contract so the
// surfaces cannot drift apart or weaken the guard.
describe('requiresDial — the Contact attempted gate', () => {
  it('guards a move into Contact attempted', () => {
    expect(requiresDial(CONTACT_ATTEMPTED_STATUS)).toBe(true)
    expect(requiresDial('Contact attempted')).toBe(true)
    expect(requiresDial('Contact Attempted')).toBe(true)
  })

  it('leaves every other status unguarded', () => {
    expect(requiresDial('Open')).toBe(false)
    expect(requiresDial('Contacted')).toBe(false)
    expect(requiresDial('Nurture')).toBe(false)
    expect(requiresDial('Lost')).toBe(false)
    expect(requiresDial('CONTACT ATTEMPTED')).toBe(false)
    expect(requiresDial(' Contact attempted ')).toBe(false)
    expect(requiresDial(undefined)).toBe(false)
  })
})

describe('the dial payload the server receives', () => {
  it('requires the timestamp, selected result, and notes', () => {
    expect(requiredDialFields()).toEqual(['dialed_at', 'dial_result', 'notes'])
  })

  it('preserves the result selected by the user', () => {
    const payload = dialPayload({
      dialed_at: '2026-08-18T09:00:00',
      dial_result: 'Busy',
      notes: 'ring ring',
    })
    expect(payload.dial_result).toBe('Busy')
    expect(payload.dialed_at).toBe('2026-08-18T09:00:00')
    expect(payload.notes).toBe('ring ring')
  })

  it('carries only the contract fields, dropping anything extraneous', () => {
    const payload = dialPayload({ dialed_at: '2026-08-18T09:00:00', status: 'hacked' })
    expect(payload).not.toHaveProperty('status')
  })

  it('renders Result as an enabled Select with the approved final outcomes', () => {
    const result = dialFields(LOG_A_DIAL, '2026-08-18T09:00:00').find(
      (field) => field.fieldname === 'dial_result',
    )
    expect(result.fieldtype).toBe('Select')
    expect(result.read_only).toBeUndefined()
    expect(result.reqd).toBe(1)
    expect(result.options).toBe('Completed\nFailed\nBusy\nNo Answer\nCanceled')
  })
})

describe('dial dialog seeding and validation', () => {
  const now = '2026-08-18T09:00:00'

  it('resolves the "Now" default to the injected timestamp', () => {
    const dialedAt = dialFields(LOG_A_DIAL, now).find((f) => f.fieldname === 'dialed_at')
    expect(dialedAt.default).toBe(now)
  })

  it('seeds the reactive doc with the default result and resolved dialed_at', () => {
    const defaults = dialDefaults(LOG_A_DIAL, now)
    expect(defaults.dialed_at).toBe(now)
    expect(defaults.dial_result).toBe('No Answer')
  })

  it('blocks submit until dialed_at, dial_result, and notes are filled', () => {
    expect(canLogDial({})).toBe(false)
    expect(canLogDial({ dialed_at: now })).toBe(false)
    expect(canLogDial({ dialed_at: '', dial_result: 'No Answer' })).toBe(false)
    expect(canLogDial({ dialed_at: now, dial_result: 'Busy' })).toBe(false)
    expect(canLogDial({ dialed_at: now, dial_result: 'Busy', notes: 'Call back tomorrow' })).toBe(
      true,
    )
  })
})

// TXB-210/211: the two governed warm-resting transitions. Both share the reach/discovery
// activity-plus-status contract and are routed through resolveLeadStatusTransition, so these
// pure-helper tests pin their required fields exactly as the server re-validates them.
describe('Follow-up requires a datetime and context', () => {
  it('guards entering Follow-up from anywhere but Follow-up itself', () => {
    expect(requiresFollowUp('Open', FOLLOW_UP_STATUS)).toBe(true)
    expect(requiresFollowUp(undefined, FOLLOW_UP_STATUS)).toBe(true)
    expect(requiresFollowUp(FOLLOW_UP_STATUS, FOLLOW_UP_STATUS)).toBe(false)
    expect(requiresFollowUp('Open', 'Contacted')).toBe(false)
  })

  it('renders a required follow-up datetime and context', () => {
    const fields = followUpFields()
    const date = fields.find((f) => f.fieldname === 'follow_up_date')
    expect(date.fieldtype).toBe('Datetime')
    expect(date.reqd).toBe(1)
    expect(requiredFollowUpFields()).toEqual(['follow_up_date', 'follow_up_context'])
  })

  it('rejects a follow-up missing the datetime or the context', () => {
    expect(validateFollowUp({})).toEqual(['follow_up_date', 'follow_up_context'])
    expect(isFollowUpValid({ follow_up_context: 'Send the deck' })).toBe(false)
    expect(isFollowUpValid({ follow_up_date: '2026-09-01 09:00:00' })).toBe(false)
    // Whitespace is not a value, matching the server's emptiness rule.
    expect(isFollowUpValid({ follow_up_date: '   ', follow_up_context: 'x' })).toBe(false)
    expect(isFollowUpValid({ follow_up_date: '2026-09-01 09:00:00', follow_up_context: 'x' })).toBe(
      true,
    )
  })

  it('builds the atomic follow-up payload only when complete', () => {
    expect(buildFollowUpActivity({ follow_up_context: 'x' })).toBeNull()
    const payload = buildFollowUpActivity(
      { follow_up_date: '2026-09-01 09:00:00', follow_up_context: 'Send the deck' },
      { actor: 'agent@txb', now: '2026-09-01T00:00:00' },
    )
    expect(payload.status).toBe(FOLLOW_UP_STATUS)
    expect(payload.activity).toMatchObject({
      type: 'follow_up',
      follow_up_date: '2026-09-01 09:00:00',
      follow_up_context: 'Send the deck',
      actor: 'agent@txb',
    })
  })
})

// TXB-239: the Log a Dial Follow-up Date and the Follow-up transition date-time join the
// coaching call-date fields in declaring `time_options_start: '07:00'`, so Field.vue routes
// them through the shared DateTimeWithOptions control (07:00-start option list). Log a Reach's
// follow-up stays a plain Date and Log a Dial's `dialed_at` stays a plain Datetime, so neither
// changes its picker. These pin that field-level contract; dateTimeWithOptions.test.js exercises
// the rendered control the contract selects.
describe('07:00 datetime option-start metadata (TXB-239)', () => {
  it('marks Log a Dial Follow-up Date as a 07:00 Datetime without touching its requiredness', () => {
    const followUp = LOG_A_DIAL.fields.find((f) => f.fieldname === 'follow_up_date')
    expect(followUp.fieldtype).toBe('Datetime')
    expect(followUp.time_options_start).toBe('07:00')
    // Non-goal: requiredness is unchanged — the Follow-up Date stays optional on a dial.
    expect(followUp.reqd).toBeUndefined()
  })

  it('marks the Follow-up transition date-time as a 07:00 Datetime, still required', () => {
    const date = followUpFields().find((f) => f.fieldname === 'follow_up_date')
    expect(date.fieldtype).toBe('Datetime')
    expect(date.time_options_start).toBe('07:00')
    expect(date.reqd).toBe(1)
  })

  it('leaves Log a Dial\'s dialed_at a plain Datetime on the shared DateTimePicker', () => {
    const dialedAt = LOG_A_DIAL.fields.find((f) => f.fieldname === 'dialed_at')
    expect(dialedAt.fieldtype).toBe('Datetime')
    expect(dialedAt.time_options_start).toBeUndefined()
  })

  it('keeps Log a Reach\'s follow-up a date-only field with no time options', () => {
    const followUp = reachFields().find((f) => f.fieldname === 'follow_up_date')
    expect(followUp.fieldtype).toBe('Date')
    expect(followUp.time_options_start).toBeUndefined()
  })
})

describe('Nurture requires context and next action, with an optional date', () => {
  it('guards entering Nurture from anywhere but Nurture itself', () => {
    expect(requiresNurture('Contacted', NURTURE_STATUS)).toBe(true)
    expect(requiresNurture(undefined, NURTURE_STATUS)).toBe(true)
    expect(requiresNurture(NURTURE_STATUS, NURTURE_STATUS)).toBe(false)
    expect(requiresNurture('Contacted', 'Lost')).toBe(false)
  })

  it('requires only the context and next action; the next-action date stays optional', () => {
    expect(requiredNurtureFields()).toEqual(['nurture_context', 'next_action'])
    const date = nurtureFields().find((f) => f.fieldname === 'next_action_date')
    expect(date.reqd).toBe(0)
    expect(date.fieldtype).toBe('Datetime')
  })

  it('accepts a nurture plan with no next-action date but rejects a missing next action', () => {
    expect(validateNurture({ nurture_context: 'Warm', next_action: '' })).toEqual(['next_action'])
    expect(isNurtureValid({ nurture_context: 'Warm', next_action: 'Email in Q4' })).toBe(true)
    expect(
      isNurtureValid({
        nurture_context: 'Warm',
        next_action: 'Email in Q4',
        next_action_date: '',
      }),
    ).toBe(true)
  })

  it('carries the optional next-action date only when supplied, null otherwise', () => {
    const withDate = buildNurtureActivity({
      nurture_context: 'Warm',
      next_action: 'Email in Q4',
      next_action_date: '2026-12-01 09:00:00',
    })
    expect(withDate.status).toBe(NURTURE_STATUS)
    expect(withDate.activity.next_action_date).toBe('2026-12-01 09:00:00')

    const withoutDate = buildNurtureActivity({ nurture_context: 'Warm', next_action: 'Email in Q4' })
    expect(withoutDate.activity.next_action_date).toBeNull()
  })
})

// TXB-211: Qualified and the legacy "No Answer" *Lead status* are retired and filtered at the
// central option source. The independent "No Answer" *dial result* is deliberately not retired --
// it still appears among LOG_A_DIAL's options (asserted above), so a manual call can log it.
describe('retired Lead statuses are recognised centrally', () => {
  it('flags Qualified and the legacy No Answer Lead status', () => {
    expect(RETIRED_LEAD_STATUSES).toEqual(['Qualified', 'No Answer'])
    expect(isRetiredLeadStatus('Qualified')).toBe(true)
    expect(isRetiredLeadStatus('No Answer')).toBe(true)
  })

  it('leaves every live Lead status selectable', () => {
    expect(isRetiredLeadStatus('Contacted')).toBe(false)
    expect(isRetiredLeadStatus(CONTACT_ATTEMPTED_STATUS)).toBe(false)
    expect(isRetiredLeadStatus(FOLLOW_UP_STATUS)).toBe(false)
    expect(isRetiredLeadStatus(NURTURE_STATUS)).toBe(false)
  })
})

// -----------------------------------------------------------------------------------------
// TXB-236: Persist the Discovery meeting time through every existing scheduling entry point.
//
// Regression for the TXB-234 defect — a selected or typed meeting time silently disappeared on
// submit because the shared FieldLayout Time control was bound on the deprecated frappe-ui
// TimePicker `:value`/`@change` contract while the installed TimePicker only emits
// `update:modelValue`: fieldChange never ran, the value never committed to the dialog's reactive
// localDoc, and FieldLayoutDialog snapshotted an empty meeting_time.
//
// These exercise the real production path from the dialog submit snapshot onward — logDiscovery →
// buildDiscoveryActivity/validateDiscovery → the single crm.txb.api.actions.schedule_discovery
// endpoint — through BOTH existing routing surfaces: Lead detail/Take Action
// (resolveLeadStatusTransition) and the Kanban board (requestKanbanTransition). The
// one-canonical-Event idempotency on retry/reschedule is owned and proven server-side by the
// TXB-209 sync_meeting_event upsert (crm/txb/test_meetings.py::test_repeated_submit_is_idempotent
// and ::test_reschedule_moves_the_same_event); the client's obligation, asserted here, is that
// every retry routes through that one endpoint with the identical payload.
// -----------------------------------------------------------------------------------------

const DISCOVERY_LEAD = 'CRM-LEAD-01'
const DISCOVERY_NOW = '2026-09-11T00:00:00'

// A Virtual schedule carrying a TYPED valid time that is not a round value the dialog might
// helpfully default — it must survive verbatim.
const TYPED_VIRTUAL = {
  meeting_date: '2026-09-10',
  meeting_time: '14:37:00',
  meeting_type: 'Virtual',
  meeting_link: 'https://meet.example/xyz',
}

// An Onsite schedule carrying a picker-selected time, proving the type-dependent address (never a
// link) travels for the other meeting type.
const SELECTED_ONSITE = {
  meeting_date: '2026-09-12',
  meeting_time: '09:15:00',
  meeting_type: 'Onsite',
  meeting_address: '1 Example Plaza, Floor 3',
}

function discoveryKanbanCtx(overrides = {}) {
  return {
    doctype: 'CRM Lead',
    itemName: DISCOVERY_LEAD,
    fieldname: 'status',
    fieldLabel: 'Status',
    from: 'Contacted',
    to: DISCOVERY_STATUS,
    ...overrides,
  }
}

/** The latest payload posted to the one canonical scheduling endpoint. */
function lastSchedulePayload() {
  const posted = discoveryMocks.call.mock.calls.filter(
    (args) => args[0] === 'crm.txb.api.actions.schedule_discovery',
  )
  expect(posted.length).toBeGreaterThan(0)
  return posted.at(-1)[1]
}

describe('TXB-236 ac-1 — a selected/typed meeting time survives to the canonical payload', () => {
  beforeEach(() => {
    discoveryMocks.call.mockReset()
    discoveryMocks.call.mockResolvedValue({ lead: DISCOVERY_LEAD, status: DISCOVERY_STATUS })
    discoveryMocks.dialogDoc.current = null
  })

  it('carries a typed Virtual time and link verbatim from the Lead-detail/Take Action path', async () => {
    discoveryMocks.dialogDoc.current = { ...TYPED_VIRTUAL }

    const routed = await resolveLeadStatusTransition('Contacted', DISCOVERY_STATUS, DISCOVERY_LEAD, {
      now: DISCOVERY_NOW,
    })

    expect(routed.outcome).toBe(LEAD_TRANSITION_SAVED)
    expect(routed.status).toBe(DISCOVERY_STATUS)

    const payload = lastSchedulePayload()
    expect(payload.lead).toBe(DISCOVERY_LEAD)
    expect(payload.status).toBe(DISCOVERY_STATUS)
    // The exact typed time is retained — the heart of the TXB-234 regression.
    expect(payload.activity.meeting_time).toBe('14:37:00')
    expect(payload.activity.meeting_date).toBe('2026-09-10')
    expect(payload.activity.meeting_type).toBe('Virtual')
    expect(payload.activity.meeting_link).toBe('https://meet.example/xyz')
    expect(payload.activity.meeting_address).toBeNull() // Virtual never carries an address
  })

  it('carries a picker-selected Onsite time and address from the Kanban path', async () => {
    discoveryMocks.dialogDoc.current = { ...SELECTED_ONSITE }

    const result = await requestKanbanTransition(discoveryKanbanCtx())

    expect(result).toEqual({
      proceed: true,
      alreadySaved: true,
      finalStatus: DISCOVERY_STATUS,
    })

    const payload = lastSchedulePayload()
    expect(payload.activity.meeting_time).toBe('09:15:00')
    expect(payload.activity.meeting_type).toBe('Onsite')
    expect(payload.activity.meeting_address).toBe('1 Example Plaza, Floor 3')
    expect(payload.activity.meeting_link).toBeNull() // Onsite never carries a link
  })

  it('routes both surfaces to the identical payload for the same committed time', async () => {
    discoveryMocks.dialogDoc.current = { ...TYPED_VIRTUAL }
    await resolveLeadStatusTransition('Contacted', DISCOVERY_STATUS, DISCOVERY_LEAD, {
      now: DISCOVERY_NOW,
    })
    const detail = lastSchedulePayload().activity

    discoveryMocks.call.mockClear()
    discoveryMocks.dialogDoc.current = { ...TYPED_VIRTUAL }
    await requestKanbanTransition(discoveryKanbanCtx())
    const kanban = lastSchedulePayload().activity

    expect(kanban.meeting_date).toBe(detail.meeting_date)
    expect(kanban.meeting_time).toBe(detail.meeting_time)
    expect(kanban.meeting_type).toBe(detail.meeting_type)
    expect(kanban.meeting_link).toBe(detail.meeting_link)
  })
})

describe('TXB-236 ac-2 — fail closed before the status transition, no partial write', () => {
  beforeEach(() => {
    discoveryMocks.call.mockReset()
    discoveryMocks.call.mockResolvedValue({ lead: DISCOVERY_LEAD, status: DISCOVERY_STATUS })
    discoveryMocks.dialogDoc.current = null
  })

  it('leaves the prior status unchanged and posts nothing on cancel', async () => {
    discoveryMocks.dialogDoc.current = null // dismissed

    const routed = await resolveLeadStatusTransition('Contacted', DISCOVERY_STATUS, DISCOVERY_LEAD, {
      now: DISCOVERY_NOW,
    })

    expect(routed.outcome).toBe(LEAD_TRANSITION_CANCELLED)
    expect(routed.status).toBe('Contacted')
    expect(discoveryMocks.call).not.toHaveBeenCalled()
  })

  it('blocks an incomplete schedule (missing the required time) at the dialog and posts nothing', async () => {
    // FieldLayoutDialog's onSubmit gate throws on a missing required date/time/type and keeps the
    // dialog open, so nothing commits and no status moves. TXB-245: the location detail is no
    // longer part of that required set — only date, time and type are.
    discoveryMocks.dialogDoc.current = {
      meeting_date: '2026-09-10',
      meeting_type: 'Virtual',
      meeting_link: 'https://meet.example/xyz',
    }

    const routed = await resolveLeadStatusTransition('Contacted', DISCOVERY_STATUS, DISCOVERY_LEAD, {
      now: DISCOVERY_NOW,
    })

    expect(routed.outcome).toBe(LEAD_TRANSITION_CANCELLED)
    expect(routed.status).toBe('Contacted')
    expect(discoveryMocks.call).not.toHaveBeenCalled()
  })

  it('preserves the prior status when persistence fails, with no second write', async () => {
    discoveryMocks.dialogDoc.current = { ...TYPED_VIRTUAL }
    const failure = new Error('schedule_discovery failed')
    discoveryMocks.call.mockRejectedValueOnce(failure)

    const routed = await resolveLeadStatusTransition('Contacted', DISCOVERY_STATUS, DISCOVERY_LEAD, {
      now: DISCOVERY_NOW,
    })

    expect(routed.outcome).toBe(LEAD_TRANSITION_FAILED)
    expect(routed.status).toBe('Contacted')
    expect(routed.error).toBe(failure)
    // The one atomic endpoint was attempted exactly once; no optimistic second status write.
    expect(discoveryMocks.call).toHaveBeenCalledTimes(1)
  })

  it('re-throws a Kanban persistence failure so the caller reverts the card', async () => {
    discoveryMocks.dialogDoc.current = { ...SELECTED_ONSITE }
    discoveryMocks.call.mockRejectedValueOnce(new Error('schedule_discovery failed'))

    await expect(requestKanbanTransition(discoveryKanbanCtx())).rejects.toThrow(
      'schedule_discovery failed',
    )
  })
})

// -----------------------------------------------------------------------------------------
// TXB-245: the type-specific location detail is optional.
//
// A Virtual meeting may be scheduled without a link and an Onsite one without an address. The
// dialog no longer gates on the detail (only date/time/type stay required), the field metadata
// drops its mandatory_depends_on, and the built payload carries null for a blank detail so the
// server timeline/Event omit it cleanly. The backend re-validation is proven in
// crm/txb/test_transitions.py.
// -----------------------------------------------------------------------------------------
describe('TXB-245 — optional type-specific location detail', () => {
  beforeEach(() => {
    discoveryMocks.call.mockReset()
    discoveryMocks.call.mockResolvedValue({ lead: DISCOVERY_LEAD, status: DISCOVERY_STATUS })
    discoveryMocks.dialogDoc.current = null
  })

  it('schedules a Virtual meeting without a link, posting meeting_link null', async () => {
    discoveryMocks.dialogDoc.current = {
      meeting_date: '2026-09-10',
      meeting_time: '14:37:00',
      meeting_type: 'Virtual',
    }

    const routed = await resolveLeadStatusTransition('Contacted', DISCOVERY_STATUS, DISCOVERY_LEAD, {
      now: DISCOVERY_NOW,
    })

    expect(routed.outcome).toBe(LEAD_TRANSITION_SAVED)
    expect(routed.status).toBe(DISCOVERY_STATUS)
    const payload = lastSchedulePayload()
    expect(payload.activity.meeting_type).toBe('Virtual')
    expect(payload.activity.meeting_link).toBeNull()
    expect(payload.activity.meeting_address).toBeNull()
  })

  it('schedules an Onsite meeting without an address, posting meeting_address null', async () => {
    discoveryMocks.dialogDoc.current = {
      meeting_date: '2026-09-12',
      meeting_time: '09:15:00',
      meeting_type: 'Onsite',
    }

    const result = await requestKanbanTransition(discoveryKanbanCtx())

    expect(result.proceed).toBe(true)
    const payload = lastSchedulePayload()
    expect(payload.activity.meeting_type).toBe('Onsite')
    expect(payload.activity.meeting_address).toBeNull()
    expect(payload.activity.meeting_link).toBeNull()
  })

  it('shows but does not mandate the type-specific detail in the field metadata', () => {
    const fields = discoveryScheduleFields()
    const link = fields.find((f) => f.fieldname === 'meeting_link')
    const address = fields.find((f) => f.fieldname === 'meeting_address')
    // Still conditionally shown for the matching type…
    expect(link.depends_on).toMatch(/Virtual/)
    expect(address.depends_on).toMatch(/Onsite/)
    // …but never mandatory (TXB-245).
    expect(link.mandatory_depends_on).toBeUndefined()
    expect(address.mandatory_depends_on).toBeUndefined()
  })
})

describe('TXB-236 ac-3 — retry routes through the single canonical endpoint, never a parallel create', () => {
  beforeEach(() => {
    discoveryMocks.call.mockReset()
    discoveryMocks.call.mockResolvedValue({ lead: DISCOVERY_LEAD, status: DISCOVERY_STATUS })
    discoveryMocks.dialogDoc.current = null
  })

  it('posts the identical schedule to the same endpoint on repeated submit', async () => {
    discoveryMocks.dialogDoc.current = { ...TYPED_VIRTUAL }
    await logDiscovery(DISCOVERY_LEAD, { now: DISCOVERY_NOW })
    discoveryMocks.dialogDoc.current = { ...TYPED_VIRTUAL }
    await logDiscovery(DISCOVERY_LEAD, { now: DISCOVERY_NOW })

    const endpoints = discoveryMocks.call.mock.calls.map((args) => args[0])
    // Both attempts hit the one canonical endpoint — the server's sync_meeting_event upsert then
    // reuses the single Event (crm/txb/test_meetings.py::test_repeated_submit_is_idempotent).
    expect(endpoints).toEqual([
      'crm.txb.api.actions.schedule_discovery',
      'crm.txb.api.actions.schedule_discovery',
    ])
    const [first, second] = discoveryMocks.call.mock.calls.map((args) => args[1].activity)
    expect(second.meeting_time).toBe(first.meeting_time)
    expect(second.meeting_date).toBe(first.meeting_date)
    expect(second.meeting_type).toBe(first.meeting_type)
    expect(second.meeting_link).toBe(first.meeting_link)
  })

  it('a reschedule to a new time still targets the one canonical endpoint', async () => {
    discoveryMocks.dialogDoc.current = { ...TYPED_VIRTUAL }
    await requestKanbanTransition(discoveryKanbanCtx())
    expect(lastSchedulePayload().activity.meeting_time).toBe('14:37:00')

    discoveryMocks.call.mockClear()
    discoveryMocks.dialogDoc.current = { ...TYPED_VIRTUAL, meeting_time: '16:05:00' }
    await requestKanbanTransition(discoveryKanbanCtx())

    const payload = lastSchedulePayload()
    expect(payload.activity.meeting_time).toBe('16:05:00')
    // Still the one endpoint — the reschedule moves the single Event server-side
    // (crm/txb/test_meetings.py::test_reschedule_moves_the_same_event), never a new create.
    expect(discoveryMocks.call).toHaveBeenCalledTimes(1)
  })
})

describe('TXB-236 ac-1/ac-3 — the shared FieldLayout Time control commits before the dialog snapshots', () => {
  const fieldSource = readFileSync(
    new URL('../../src/components/FieldLayout/Field.vue', import.meta.url),
    'utf-8',
  )
  const dialogSource = readFileSync(
    new URL('../../src/components/Modals/FieldLayoutDialog.vue', import.meta.url),
    'utf-8',
  )

  // The exact <TimePicker> element rendered for a Time field.
  const tpStart = fieldSource.indexOf('<TimePicker')
  const timePickerBlock = fieldSource.slice(
    tpStart,
    tpStart + fieldSource.slice(tpStart).indexOf('/>') + 2,
  )

  it('binds the TimePicker on the canonical modelValue/update:modelValue contract', () => {
    // The installed frappe-ui TimePicker only emits update:modelValue, so this is the sole
    // contract that commits a selected/typed time into the FieldLayout data.
    expect(timePickerBlock).toMatch(/:model-value="data\[field\.fieldname\]"/)
    expect(timePickerBlock).toMatch(/@update:model-value="\(v\) => fieldChange\(v, field\)"/)
  })

  it('no longer relies on the deprecated :value/@change contract the picker stopped emitting', () => {
    // The regression: these were the only bindings before TXB-236, so nothing ever committed.
    expect(timePickerBlock).not.toMatch(/:value="data\[field\.fieldname\]"/)
    expect(timePickerBlock).not.toMatch(/@change=/)
  })

  it('fieldChange commits into the same data the dialog snapshots on submit', () => {
    // fieldChange → triggerOnChange writes the committed value into the FieldLayout `data`, and
    // FieldLayoutDialog's submit snapshots that reactive doc ({ ...localDoc }); together they
    // guarantee a committed time is present in the posted payload.
    expect(fieldSource).toMatch(/function fieldChange\(value, df\)/)
    expect(fieldSource).toMatch(/triggerOnChange\(df\.fieldname, value\)/)
    // The default submit path snapshots the reactive localDoc and resolves it to logDiscovery.
    expect(dialogSource).toMatch(/const data = \{ \.\.\.localDoc \}/)
    expect(dialogSource).toMatch(/submit\(data\)/)
  })
})

// -----------------------------------------------------------------------------------------
// TXB-241: Start Discovery meeting time options at 07:00.
//
// Discovery renders separate Date and Time fields, so the TXB-239 Datetime-only option-start
// path never reached its `meeting_time`; the shared standalone TimePicker still generated its
// default 00:00-start list. The fix declares `time_options_start: '07:00'` on the meeting Time
// field and teaches Field.vue's standalone Time branch to feed the real frappe-ui TimePicker the
// generated 07:00–23:45 option list only when that metadata is present. These pin the field-level
// contract and the Field.vue wiring; discoveryMeetingTime.test.js mounts the rendered picker.
// -----------------------------------------------------------------------------------------
describe('TXB-241 — Discovery meeting time option-start metadata', () => {
  const fields = discoveryScheduleFields()
  const meetingTime = fields.find((f) => f.fieldname === 'meeting_time')

  it('marks the meeting Time field with a 07:00 option start without touching its requiredness', () => {
    // Still a standalone Time field — not swapped to Datetime or a manual-only control (AC-3).
    expect(meetingTime.fieldtype).toBe('Time')
    expect(meetingTime.reqd).toBe(1)
    // The declarative hook Field.vue routes on to offer the business-hour dropdown (AC-1).
    expect(meetingTime.time_options_start).toBe('07:00')
  })

  it('leaves the sibling Date and Type fields free of time-option metadata (AC-3)', () => {
    const date = fields.find((f) => f.fieldname === 'meeting_date')
    const type = fields.find((f) => f.fieldname === 'meeting_type')
    expect(date.fieldtype).toBe('Date')
    expect(date.time_options_start).toBeUndefined()
    expect(type.time_options_start).toBeUndefined()
  })
})

describe('TXB-241 — the standalone Time control offers a field-scoped option list', () => {
  const fieldSource = readFileSync(
    new URL('../../src/components/FieldLayout/Field.vue', import.meta.url),
    'utf-8',
  )

  const tpStart = fieldSource.indexOf('<TimePicker')
  const timePickerBlock = fieldSource.slice(
    tpStart,
    tpStart + fieldSource.slice(tpStart).indexOf('/>') + 2,
  )

  it('feeds the standalone TimePicker a field-scoped option list', () => {
    // The real TimePicker renders `options` as its dropdown; passing the generated 07:00 list
    // constrains only the displayed options (AC-1) without touching typing.
    expect(timePickerBlock).toMatch(/:options="timeFieldOptions\(field\)"/)
  })

  it('keeps the TXB-236 modelValue/update:modelValue persistence contract intact (AC-3)', () => {
    // The option list is additive — the commit route the picker actually emits is unchanged.
    expect(timePickerBlock).toMatch(/:model-value="data\[field\.fieldname\]"/)
    expect(timePickerBlock).toMatch(/@update:model-value="\(v\) => fieldChange\(v, field\)"/)
    expect(timePickerBlock).not.toMatch(/@change=/)
  })

  it('derives the list from generateTimeOptions only when time_options_start is present (AC-3)', () => {
    // A metadata-free Time field returns null, so frappe-ui keeps its default option list and
    // every other Time field is untouched; the declared start seeds the generated list.
    expect(fieldSource).toMatch(/const timeFieldOptions = \(field\) => \{/)
    expect(fieldSource).toMatch(/if \(!field\.time_options_start\) return null/)
    expect(fieldSource).toMatch(/generateTimeOptions\(field\.time_options_start\)/)
  })

  it('renders no plain/manual-only replacement for the Time field (AC-3)', () => {
    // The Time branch stays the real frappe-ui TimePicker, not a bare TextInput/FormControl.
    expect(timePickerBlock).toMatch(/^<TimePicker/)
    expect(timePickerBlock).not.toMatch(/TextInput|FormControl/)
  })
})

// TXB-241 ac-2 end-to-end: an *earlier* manual exception (before the 07:00 dropdown start) must
// still commit and persist verbatim through BOTH scheduling entry points — the dropdown start is
// a suggestion, never a validation minimum. TXB-236's suites already cover a typed mid-day time
// and a picker-selected time; these pin the earlier-exception clause specifically.
describe('TXB-241 ac-2 — an earlier manual time exception persists through both paths', () => {
  const EARLY_EXCEPTION = {
    meeting_date: '2026-09-10',
    meeting_time: '06:30:00',
    meeting_type: 'Virtual',
    meeting_link: 'https://meet.example/early',
  }

  beforeEach(() => {
    discoveryMocks.call.mockReset()
    discoveryMocks.call.mockResolvedValue({ lead: DISCOVERY_LEAD, status: DISCOVERY_STATUS })
    discoveryMocks.dialogDoc.current = null
  })

  it('carries a 06:30 exception verbatim from the Lead-detail/Take Action path', async () => {
    discoveryMocks.dialogDoc.current = { ...EARLY_EXCEPTION }
    const routed = await resolveLeadStatusTransition(
      'Contacted',
      DISCOVERY_STATUS,
      DISCOVERY_LEAD,
      { now: DISCOVERY_NOW },
    )
    expect(routed.outcome).toBe(LEAD_TRANSITION_SAVED)
    expect(lastSchedulePayload().activity.meeting_time).toBe('06:30:00')
  })

  it('carries the same 06:30 exception verbatim from the Kanban path', async () => {
    discoveryMocks.dialogDoc.current = { ...EARLY_EXCEPTION }
    const result = await requestKanbanTransition(discoveryKanbanCtx())
    expect(result.proceed).toBe(true)
    expect(lastSchedulePayload().activity.meeting_time).toBe('06:30:00')
  })
})

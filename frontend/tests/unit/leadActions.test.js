import { describe, it, expect } from 'vitest'

import {
  CONTACT_ATTEMPTED_STATUS,
  LOG_A_DIAL,
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

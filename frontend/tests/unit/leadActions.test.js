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

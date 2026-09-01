import { readFileSync } from 'node:fs'

import { describe, it, expect, vi } from 'vitest'

import {
  CONTACT_ATTEMPTED_STATUS,
  CONTACTED_STATUS,
  DISCOVERY_STATUS,
  FOLLOW_UP_STATUS,
  NURTURE_STATUS,
  isGuardedLeadTransition,
  resolveLeadStatusTransition,
  LEAD_TRANSITION_SAVED,
  LEAD_TRANSITION_CANCELLED,
  LEAD_TRANSITION_FAILED,
  LEAD_TRANSITION_PLAIN,
} from '@/utils/leadActions'

/**
 * TXB-166: one production authority owns the guarded existing-Lead status routing, and every
 * existing-Lead status caller delegates to it:
 *
 *   - desktop Lead.vue header dropdown        (triggerStatusChange)
 *   - desktop Lead.vue side-panel/activities  (beforeStatusChange)
 *   - responsive MobileLead.vue header         (triggerStatusChange)
 *   - responsive MobileLead.vue Details/Data   (beforeStatusChange)
 *   - Lead Kanban board                        (requestKanbanTransition -> the authority)
 *
 * These tests exercise the real {@link resolveLeadStatusTransition} (no re-implemented routing)
 * with injected action stubs, and read the caller source so a surface cannot drift apart, skip
 * the authority, or write a guarded status behind the backend guard.
 */

const LEAD = 'CRM-LEAD-01'

// Injected action stubs: the authority calls exactly one of these, never the real dialogs.
function stubActions(overrides = {}) {
  return {
    logReach: vi.fn().mockResolvedValue({ status: CONTACTED_STATUS }),
    logADial: vi.fn().mockResolvedValue({ status: CONTACT_ATTEMPTED_STATUS }),
    logDiscovery: vi.fn().mockResolvedValue({ status: DISCOVERY_STATUS }),
    scheduleFollowUp: vi.fn().mockResolvedValue({ status: FOLLOW_UP_STATUS }),
    setNurture: vi.fn().mockResolvedValue({ status: NURTURE_STATUS }),
    ...overrides,
  }
}

function readSource(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf-8')
}

describe('resolveLeadStatusTransition — the one guarded routing authority', () => {
  it('routes entering Contacted through Log a reach, never Log a dial', async () => {
    const actions = stubActions()
    const routed = await resolveLeadStatusTransition('Open', CONTACTED_STATUS, LEAD, { actions })

    expect(actions.logReach).toHaveBeenCalledOnce()
    expect(actions.logReach).toHaveBeenCalledWith(LEAD, expect.anything())
    expect(actions.logADial).not.toHaveBeenCalled()
    expect(actions.logDiscovery).not.toHaveBeenCalled()
    expect(routed).toMatchObject({
      outcome: LEAD_TRANSITION_SAVED,
      guarded: true,
      status: CONTACTED_STATUS,
    })
  })

  it('routes Contact attempted through Log a dial before any status write', async () => {
    const actions = stubActions()
    const routed = await resolveLeadStatusTransition(
      'Open',
      CONTACT_ATTEMPTED_STATUS,
      LEAD,
      { actions },
    )

    expect(actions.logADial).toHaveBeenCalledOnce()
    expect(actions.logReach).not.toHaveBeenCalled()
    expect(actions.logDiscovery).not.toHaveBeenCalled()
    expect(routed.outcome).toBe(LEAD_TRANSITION_SAVED)
    expect(routed.status).toBe(CONTACT_ATTEMPTED_STATUS)
  })

  it('routes entering Discovery meeting set through Schedule Discovery meeting', async () => {
    const actions = stubActions()
    const routed = await resolveLeadStatusTransition('Contacted', DISCOVERY_STATUS, LEAD, {
      actions,
    })

    expect(actions.logDiscovery).toHaveBeenCalledOnce()
    expect(actions.logReach).not.toHaveBeenCalled()
    expect(actions.logADial).not.toHaveBeenCalled()
    expect(routed.outcome).toBe(LEAD_TRANSITION_SAVED)
  })

  it('gates a target-only caller (optimistic from) into the same guarded action', async () => {
    // The side-panel / Details controls overwrite the in-memory status first, so they pass
    // `from` as undefined; entering Contacted / Discovery, and any dial, must still gate.
    expect(isGuardedLeadTransition(undefined, CONTACTED_STATUS)).toBe(true)
    expect(isGuardedLeadTransition(undefined, DISCOVERY_STATUS)).toBe(true)
    expect(isGuardedLeadTransition(undefined, CONTACT_ATTEMPTED_STATUS)).toBe(true)

    const actions = stubActions()
    const routed = await resolveLeadStatusTransition(undefined, CONTACTED_STATUS, LEAD, {
      actions,
    })
    expect(actions.logReach).toHaveBeenCalledOnce()
    expect(routed.outcome).toBe(LEAD_TRANSITION_SAVED)
  })
})

describe('Log a dial owns exactly one dial/status mutation (ac-2)', () => {
  it('performs one successful dial-plus-status mutation and reports saved', async () => {
    const actions = stubActions()
    const routed = await resolveLeadStatusTransition(
      'Open',
      CONTACT_ATTEMPTED_STATUS,
      LEAD,
      { actions },
    )

    // Exactly one mutation, owned by Log a dial; the authority never issues a second status write.
    expect(actions.logADial).toHaveBeenCalledTimes(1)
    expect(routed.outcome).toBe(LEAD_TRANSITION_SAVED)
    expect(routed.result).toEqual({ status: CONTACT_ATTEMPTED_STATUS })
  })

  it('returns a non-persisting cancelled outcome with the prior status preserved', async () => {
    const actions = stubActions({ logADial: vi.fn().mockResolvedValue(null) })
    const routed = await resolveLeadStatusTransition(
      'Nurture',
      CONTACT_ATTEMPTED_STATUS,
      LEAD,
      { actions },
    )

    expect(routed.outcome).toBe(LEAD_TRANSITION_CANCELLED)
    expect(routed.status).toBe('Nurture')
    expect(routed.result).toBeNull()
  })

  it('returns a non-persisting failed outcome with the prior status preserved', async () => {
    const failure = new Error('log_a_dial failed')
    const actions = stubActions({ logADial: vi.fn().mockRejectedValue(failure) })
    const routed = await resolveLeadStatusTransition(
      'Nurture',
      CONTACT_ATTEMPTED_STATUS,
      LEAD,
      { actions },
    )

    expect(routed.outcome).toBe(LEAD_TRANSITION_FAILED)
    expect(routed.status).toBe('Nurture')
    expect(routed.error).toBe(failure)
  })
})

describe('unguarded and ordinary transitions keep their contracts (ac-3)', () => {
  it('leaves entering Contacted on Log a reach, not Log a dial', async () => {
    const actions = stubActions()
    const routed = await resolveLeadStatusTransition('Open', CONTACTED_STATUS, LEAD, { actions })
    expect(actions.logADial).not.toHaveBeenCalled()
    expect(routed.status).toBe(CONTACTED_STATUS)
  })

  it('does not re-gate a Lead already resting in a guarded status when moving onward', async () => {
    const actions = stubActions()
    const routed = await resolveLeadStatusTransition(
      CONTACT_ATTEMPTED_STATUS,
      'Not interested',
      LEAD,
      { actions },
    )
    expect(actions.logADial).not.toHaveBeenCalled()
    expect(routed.outcome).toBe(LEAD_TRANSITION_PLAIN)
    expect(routed.guarded).toBe(false)
  })

  it('keeps a Lost move on the plain (Lost-reason) path, never a guarded action', async () => {
    expect(isGuardedLeadTransition('Open', 'Lost')).toBe(false)
    const actions = stubActions()
    const routed = await resolveLeadStatusTransition('Open', 'Lost', LEAD, { actions })
    expect(actions.logReach).not.toHaveBeenCalled()
    expect(actions.logADial).not.toHaveBeenCalled()
    expect(actions.logDiscovery).not.toHaveBeenCalled()
    expect(routed.outcome).toBe(LEAD_TRANSITION_PLAIN)
  })

  it('keeps an ordinary move on the plain status save', async () => {
    const actions = stubActions()
    const routed = await resolveLeadStatusTransition('Contacted', 'Not interested', LEAD, {
      actions,
    })
    expect(routed.outcome).toBe(LEAD_TRANSITION_PLAIN)
    expect(routed.guarded).toBe(false)
    expect(routed.status).toBe('Not interested')
  })
})

describe('Follow-up and Nurture route through the shared authority (TXB-211)', () => {
  it('routes entering Follow-up through Schedule a follow-up, never a plain save', async () => {
    const actions = stubActions()
    const routed = await resolveLeadStatusTransition('Open', FOLLOW_UP_STATUS, LEAD, { actions })

    expect(actions.scheduleFollowUp).toHaveBeenCalledOnce()
    expect(actions.scheduleFollowUp).toHaveBeenCalledWith(LEAD, expect.anything())
    expect(actions.setNurture).not.toHaveBeenCalled()
    expect(actions.logReach).not.toHaveBeenCalled()
    expect(routed).toMatchObject({
      outcome: LEAD_TRANSITION_SAVED,
      guarded: true,
      status: FOLLOW_UP_STATUS,
    })
  })

  it('routes entering Nurture through Nurture the lead, never a plain save', async () => {
    const actions = stubActions()
    const routed = await resolveLeadStatusTransition('Contacted', NURTURE_STATUS, LEAD, { actions })

    expect(actions.setNurture).toHaveBeenCalledOnce()
    expect(actions.setNurture).toHaveBeenCalledWith(LEAD, expect.anything())
    expect(actions.scheduleFollowUp).not.toHaveBeenCalled()
    expect(routed).toMatchObject({
      outcome: LEAD_TRANSITION_SAVED,
      guarded: true,
      status: NURTURE_STATUS,
    })
  })

  it('gates a target-only (optimistic-from) Follow-up / Nurture caller into the same action', async () => {
    // The side-panel / Details controls overwrite the in-memory status first and pass `from`
    // as undefined; entering Follow-up or Nurture must still gate through its action.
    expect(isGuardedLeadTransition(undefined, FOLLOW_UP_STATUS)).toBe(true)
    expect(isGuardedLeadTransition(undefined, NURTURE_STATUS)).toBe(true)

    const followUp = stubActions()
    await resolveLeadStatusTransition(undefined, FOLLOW_UP_STATUS, LEAD, { actions: followUp })
    expect(followUp.scheduleFollowUp).toHaveBeenCalledOnce()

    const nurture = stubActions()
    await resolveLeadStatusTransition(undefined, NURTURE_STATUS, LEAD, { actions: nurture })
    expect(nurture.setNurture).toHaveBeenCalledOnce()
  })

  it('does not re-gate a Lead already resting in Follow-up or Nurture when moving onward', async () => {
    const actions = stubActions()
    const fromFollowUp = await resolveLeadStatusTransition(FOLLOW_UP_STATUS, 'Lost', LEAD, {
      actions,
    })
    const fromNurture = await resolveLeadStatusTransition(NURTURE_STATUS, 'Lost', LEAD, { actions })

    expect(actions.scheduleFollowUp).not.toHaveBeenCalled()
    expect(actions.setNurture).not.toHaveBeenCalled()
    expect(fromFollowUp.outcome).toBe(LEAD_TRANSITION_PLAIN)
    expect(fromNurture.outcome).toBe(LEAD_TRANSITION_PLAIN)
  })

  it('preserves the prior status when Follow-up is cancelled or fails', async () => {
    const cancelled = stubActions({ scheduleFollowUp: vi.fn().mockResolvedValue(null) })
    const onCancel = await resolveLeadStatusTransition('Open', FOLLOW_UP_STATUS, LEAD, {
      actions: cancelled,
    })
    expect(onCancel.outcome).toBe(LEAD_TRANSITION_CANCELLED)
    expect(onCancel.status).toBe('Open')

    const failure = new Error('schedule_follow_up failed')
    const failed = stubActions({ scheduleFollowUp: vi.fn().mockRejectedValue(failure) })
    const onFail = await resolveLeadStatusTransition('Open', FOLLOW_UP_STATUS, LEAD, {
      actions: failed,
    })
    expect(onFail.outcome).toBe(LEAD_TRANSITION_FAILED)
    expect(onFail.status).toBe('Open')
    expect(onFail.error).toBe(failure)
  })

  it('preserves the prior status when Nurture is cancelled or fails', async () => {
    const cancelled = stubActions({ setNurture: vi.fn().mockResolvedValue(null) })
    const onCancel = await resolveLeadStatusTransition('Contacted', NURTURE_STATUS, LEAD, {
      actions: cancelled,
    })
    expect(onCancel.outcome).toBe(LEAD_TRANSITION_CANCELLED)
    expect(onCancel.status).toBe('Contacted')

    const failure = new Error('set_nurture failed')
    const failed = stubActions({ setNurture: vi.fn().mockRejectedValue(failure) })
    const onFail = await resolveLeadStatusTransition('Contacted', NURTURE_STATUS, LEAD, {
      actions: failed,
    })
    expect(onFail.outcome).toBe(LEAD_TRANSITION_FAILED)
    expect(onFail.status).toBe('Contacted')
    expect(onFail.error).toBe(failure)
  })
})

describe('every existing-Lead status caller imports and invokes the authority (ac-1)', () => {
  const callers = [
    ['desktop Lead.vue', '../../src/pages/Lead.vue'],
    ['responsive MobileLead.vue', '../../src/pages/MobileLead.vue'],
    ['Lead Kanban board', '../../src/utils/kanbanTransitions.js'],
  ]

  it.each(callers)('%s imports resolveLeadStatusTransition from leadActions', (_label, path) => {
    const source = readSource(path)
    expect(source).toMatch(/from ['"]@\/utils\/leadActions['"]/)
    expect(source).toContain('resolveLeadStatusTransition')
  })

  it.each(callers)('%s invokes the authority before persisting a status', (_label, path) => {
    const source = readSource(path)
    expect(source).toMatch(/resolveLeadStatusTransition\s*\(/)
  })

  it.each(callers)('%s no longer re-implements the guarded routing decision', (_label, path) => {
    const source = readSource(path)
    // The old per-surface predicates (requiresReach / requiresDial / requiresDiscoverySchedule)
    // must not be called directly by a caller anymore; the authority owns that decision.
    expect(source).not.toMatch(/requiresReach\s*\(/)
    expect(source).not.toMatch(/requiresDial\s*\(/)
    expect(source).not.toMatch(/requiresDiscoverySchedule\s*\(/)
  })
})

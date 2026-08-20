import { describe, it, expect, vi } from 'vitest'
import {
  allowedTargets,
  candidateActions,
  canDropOn,
  prefillFor,
  resolveStatusChange,
  refreshStatusResolution,
  STATUS_CHANGE_ACTION,
  STATUS_CHANGE_BLOCKED,
  STATUS_CHANGE_UNOWNED,
} from '@/utils/dealTransitions'

const TRANSITIONS = {
  Workshop: {
    'Workshop set': {
      'Workshop ran': [{ name: 'run_workshop', label: 'Run Workshop' }],
      Lost: [
        { name: 'run_workshop', label: 'Run Workshop' },
        { name: 'cancel_workshop', label: 'Cancel Workshop' },
      ],
    },
    '*': {
      Lost: [{ name: 'cancel_workshop', label: 'Cancel Workshop' }],
    },
  },
}

const AVAILABLE = [
  { name: 'run_workshop', label: 'Run Workshop', fields: [] },
  { name: 'cancel_workshop', label: 'Cancel Workshop', fields: [] },
  { name: 'reschedule_workshop', label: 'Reschedule', fields: [] },
]

describe('allowedTargets', () => {
  it('lists the reachable statuses', () => {
    expect(
      allowedTargets(TRANSITIONS, 'Workshop', 'Workshop set').sort(),
    ).toEqual(['Lost', 'Workshop ran'])
  })

  it('returns nothing for an unknown pipeline', () => {
    expect(allowedTargets(TRANSITIONS, 'Nope', 'Workshop set')).toEqual([])
    expect(allowedTargets(undefined, 'Workshop', 'Workshop set')).toEqual([])
  })

  // An unknown STATUS is no longer asserted empty here: the fixture now carries a '*'
  // row (Task 15), so 'Workshop'/'Nope' legitimately falls back to it, the same as the
  // real off-list case covered below under "off-list statuses fall back to universal
  // actions". A status with no row of its own is exactly what triggers the fallback.
})

describe('candidateActions', () => {
  it('keeps only the actions the server currently offers', () => {
    const found = candidateActions(
      TRANSITIONS,
      'Workshop',
      'Workshop set',
      'Lost',
      AVAILABLE,
    )
    expect(found.map((a) => a.name)).toEqual([
      'run_workshop',
      'cancel_workshop',
    ])
  })

  it('drops an edge action the server has filtered out by role', () => {
    const found = candidateActions(
      TRANSITIONS,
      'Workshop',
      'Workshop set',
      'Lost',
      [AVAILABLE[1]],
    )
    expect(found.map((a) => a.name)).toEqual(['cancel_workshop'])
  })

  it('returns nothing for an edge that does not exist', () => {
    expect(
      candidateActions(
        TRANSITIONS,
        'Workshop',
        'Workshop set',
        'Sold',
        AVAILABLE,
      ),
    ).toEqual([])
  })
})

const terminalTransitions = {
  'Individual Session': {
    'Session Run': {
      Won: [{ name: 'session_won', label: 'Won' }],
    },
  },
  Workshop: {
    'Workshop ran': {
      Sold: [{ name: 'workshop_won', label: 'Sold' }],
    },
  },
}

describe('resolveStatusChange', () => {
  it('runs the owning action when the server offers it', () => {
    const resolution = resolveStatusChange(
      terminalTransitions,
      'Individual Session',
      'Session Run',
      'Won',
      [{ name: 'session_won', label: 'Won', fields: [] }],
    )

    expect(resolution.kind).toBe(STATUS_CHANGE_ACTION)
    expect(resolution.candidates.map((a) => a.name)).toEqual(['session_won'])
  })

  // The regression: an action-owned edge whose action is missing from the available list
  // (empty, stale, role-filtered or unmatched) must NOT collapse to the empty list a caller
  // reads as "write it bare". It fails closed as BLOCKED, keeping the Won/Sold modal
  // mandatory (TXB-175).
  it.each([
    ['an empty available list', []],
    ['an unmatched available list', [{ name: 'reschedule_bap', fields: [] }]],
  ])('blocks an owned edge when the action is absent from %s', (_label, available) => {
    const resolution = resolveStatusChange(
      terminalTransitions,
      'Workshop',
      'Workshop ran',
      'Sold',
      available,
    )

    expect(resolution.kind).toBe(STATUS_CHANGE_BLOCKED)
    expect(resolution.candidates).toBeUndefined()
  })

  it('reports an edge the graph does not describe as unowned', () => {
    const resolution = resolveStatusChange(
      terminalTransitions,
      'Individual Session',
      'Submitted',
      'Won',
      [{ name: 'session_won', label: 'Won', fields: [] }],
    )

    expect(resolution.kind).toBe(STATUS_CHANGE_UNOWNED)
  })
})

describe('refreshStatusResolution', () => {
  it('uses freshly loaded actions when the cached list predates Session Run', async () => {
    const loadAvailable = async () => ({
      actions: [{ name: 'session_won', label: 'Won', fields: [] }],
    })

    const resolution = await refreshStatusResolution({
      transitions: terminalTransitions,
      pipeline: 'Individual Session',
      from: 'Session Run',
      to: 'Won',
      loadAvailable,
    })

    expect(resolution.kind).toBe(STATUS_CHANGE_ACTION)
    expect(resolution.candidates.map((action) => action.name)).toEqual([
      'session_won',
    ])
  })

  it('loads the Workshop Sold action when the initial action cache is empty', async () => {
    const loadAvailable = async () => ({
      actions: [{ name: 'workshop_won', label: 'Sold', fields: [] }],
    })

    const resolution = await refreshStatusResolution({
      transitions: terminalTransitions,
      pipeline: 'Workshop',
      from: 'Workshop ran',
      to: 'Sold',
      loadAvailable,
    })

    expect(resolution.kind).toBe(STATUS_CHANGE_ACTION)
    expect(resolution.candidates.map((action) => action.name)).toEqual([
      'workshop_won',
    ])
  })

  it('rejects instead of treating a failed refresh as an actionless edge', async () => {
    const loadAvailable = async () => {
      throw new Error('network unavailable')
    }

    await expect(
      refreshStatusResolution({
        transitions: terminalTransitions,
        pipeline: 'Individual Session',
        from: 'Session Run',
        to: 'Won',
        loadAvailable,
      }),
    ).rejects.toThrow('network unavailable')
  })

  // A 200 whose body is not the expected shape is "we could not find out", not "this edge
  // has no action". If it resolved to a bare-write path the Won/Sold modal would be skipped
  // and an Admin would write the terminal status directly, so every malformed-but-resolved
  // payload must reject exactly like a refresh failure does.
  it.each([
    ['a null body', null],
    ['an empty object', {}],
    ['a null actions field', { actions: null }],
    ['a non-array actions field', { actions: 'oops' }],
  ])('rejects rather than bypassing the action on %s', async (_label, body) => {
    await expect(
      refreshStatusResolution({
        transitions: terminalTransitions,
        pipeline: 'Workshop',
        from: 'Workshop ran',
        to: 'Sold',
        loadAvailable: async () => body,
      }),
    ).rejects.toThrow('get_available_actions returned no actions array')
  })

  // The genuine empty offer — server reached, role filtered everything out — is an OWNED
  // edge with no available action: it fails closed as BLOCKED, it does not fall through to a
  // bare write.
  it('blocks when the server offers an empty actions array for an owned edge', async () => {
    const resolution = await refreshStatusResolution({
      transitions: terminalTransitions,
      pipeline: 'Workshop',
      from: 'Workshop ran',
      to: 'Sold',
      loadAvailable: async () => ({ actions: [] }),
    })

    expect(resolution.kind).toBe(STATUS_CHANGE_BLOCKED)
  })

  // An unowned edge is the Admin recovery hatch: there is no action to look up, so the
  // loader is never called and a malformed/empty response cannot even arise.
  it('resolves an unowned edge without loading available actions', async () => {
    const loadAvailable = vi.fn(async () => ({ actions: [] }))

    const resolution = await refreshStatusResolution({
      transitions: terminalTransitions,
      pipeline: 'Individual Session',
      from: 'Submitted',
      to: 'Won',
      loadAvailable,
    })

    expect(resolution.kind).toBe(STATUS_CHANGE_UNOWNED)
    expect(loadAvailable).not.toHaveBeenCalled()
  })
})

describe('prefillFor', () => {
  const runWorkshop = {
    to_state_map: {
      ws_outcome: {
        'Won - proceed to coaching': 'Workshop ran',
        'Follow-up needed': 'Workshop rescheduling in progress',
        Lost: 'Lost',
      },
    },
  }

  it('pre-selects the branch value that reaches the dropped column', () => {
    expect(prefillFor(runWorkshop, 'Workshop ran')).toEqual({
      ws_outcome: 'Won - proceed to coaching',
    })
  })

  it('pre-fills nothing when two values reach the same target', () => {
    const runBap = {
      to_state_map: {
        outcome: {
          'Won - proceed to coaching': 'Won',
          'Follow-up needed': 'Session Run',
          'Not interested': 'Session Run',
        },
      },
    }
    expect(prefillFor(runBap, 'Session Run')).toEqual({})
    expect(prefillFor(runBap, 'Won')).toEqual({
      outcome: 'Won - proceed to coaching',
    })
  })

  it('pre-fills nothing for an action with a fixed target', () => {
    expect(prefillFor({ to_state: 'Sold' }, 'Sold')).toEqual({})
    expect(prefillFor(null, 'Sold')).toEqual({})
  })
})

describe('canDropOn', () => {
  it('refuses every column when the user may not change the status', () => {
    expect(
      canDropOn(TRANSITIONS, 'Workshop', 'Workshop set', 'Workshop ran', false),
    ).toBe(false)
  })

  it('allows a reachable column', () => {
    expect(
      canDropOn(TRANSITIONS, 'Workshop', 'Workshop set', 'Workshop ran', true),
    ).toBe(true)
  })

  it('refuses an unreachable column', () => {
    expect(
      canDropOn(TRANSITIONS, 'Workshop', 'Workshop set', 'Sold', true),
    ).toBe(false)
  })

  it('allows the column the card came from', () => {
    expect(
      canDropOn(TRANSITIONS, 'Workshop', 'Workshop set', 'Workshop set', true),
    ).toBe(true)
  })
})

describe('canDropOn — the Admin hatch', () => {
  it('lets an Admin drop on any status in the pipeline', () => {
    const statuses = ['Workshop set', 'Sold', 'Lost']
    expect(
      canDropOn(
        TRANSITIONS,
        'Workshop',
        'Workshop set',
        'Sold',
        true,
        statuses,
      ),
    ).toBe(true)
  })

  it('still refuses a status outside the pipeline for an Admin', () => {
    expect(
      canDropOn(TRANSITIONS, 'Workshop', 'Workshop set', 'Active', true, [
        'Sold',
      ]),
    ).toBe(false)
  })

  it('still refuses everything when the user may not change status at all', () => {
    expect(
      canDropOn(TRANSITIONS, 'Workshop', 'Workshop set', 'Sold', false, [
        'Sold',
      ]),
    ).toBe(false)
  })

  it('falls back to the graph when no admin status list is given', () => {
    expect(
      canDropOn(TRANSITIONS, 'Workshop', 'Workshop set', 'Sold', true),
    ).toBe(false)
    expect(
      canDropOn(TRANSITIONS, 'Workshop', 'Workshop set', 'Workshop ran', true),
    ).toBe(true)
  })

  it('falls back to the graph when the status list has not loaded', () => {
    // allowedStatusesFor returns [] before its resource resolves; an empty array is
    // truthy, so a naive check would refuse every column for an Admin.
    expect(
      canDropOn(
        TRANSITIONS,
        'Workshop',
        'Workshop set',
        'Workshop ran',
        true,
        [],
      ),
    ).toBe(true)
    expect(
      canDropOn(TRANSITIONS, 'Workshop', 'Workshop set', 'Sold', true, []),
    ).toBe(false)
  })
})

describe('off-list statuses fall back to universal actions', () => {
  it('offers the universal targets from a status with no graph entry', () => {
    expect(allowedTargets(TRANSITIONS, 'Workshop', 'Active')).toEqual(['Lost'])
  })

  it('resolves the universal action as a candidate', () => {
    const found = candidateActions(
      TRANSITIONS,
      'Workshop',
      'Active',
      'Lost',
      AVAILABLE,
    )
    expect(found.map((a) => a.name)).toEqual(['cancel_workshop'])
  })

  it('prefers the status-specific entry when one exists', () => {
    // "Workshop set" has its own row; the universal row must not shadow or duplicate it.
    expect(
      allowedTargets(TRANSITIONS, 'Workshop', 'Workshop set').sort(),
    ).toEqual(['Lost', 'Workshop ran'])
    const found = candidateActions(
      TRANSITIONS,
      'Workshop',
      'Workshop set',
      'Lost',
      AVAILABLE,
    )
    expect(found.map((a) => a.name)).toEqual([
      'run_workshop',
      'cancel_workshop',
    ])
  })

  it('is silent when the pipeline has no universal actions', () => {
    expect(allowedTargets({ Workshop: {} }, 'Workshop', 'Active')).toEqual([])
  })
})

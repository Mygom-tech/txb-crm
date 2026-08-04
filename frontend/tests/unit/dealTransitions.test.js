import { describe, it, expect } from 'vitest'
import {
  allowedTargets,
  candidateActions,
  canDropOn,
  prefillFor,
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

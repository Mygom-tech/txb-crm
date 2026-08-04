import { describe, it, expect } from 'vitest'
import { allowedStatusesFor, statusLinkFilters } from '@/utils/pipelineStatuses'

const MAP = {
  'Individual Session': [
    'Submitted',
    'Session Set',
    'Session Run',
    'Won',
    'Lost',
  ],
  Workshop: [
    'Workshop submitted',
    'VCS call set',
    'Workshop set',
    'Sold',
    'Lost',
  ],
  'Delivering Coaching': [
    'Submitted',
    'Waiting on Review',
    'Active',
    'Inactive',
  ],
}

describe('allowedStatusesFor', () => {
  it('returns only the pipeline’s statuses', () => {
    expect(allowedStatusesFor('Workshop', 'Workshop set', MAP)).toEqual([
      'Workshop submitted',
      'VCS call set',
      'Workshop set',
      'Sold',
      'Lost',
    ])
  })

  it('does not leak statuses from other pipelines', () => {
    const allowed = allowedStatusesFor('Workshop', 'Workshop set', MAP)
    expect(allowed).not.toContain('Session Run')
    expect(allowed).not.toContain('Waiting on Review')
  })

  it('preserves the declared display order', () => {
    expect(allowedStatusesFor('Delivering Coaching', 'Active', MAP)).toEqual([
      'Submitted',
      'Waiting on Review',
      'Active',
      'Inactive',
    ])
  })

  it('keeps a status shared by two pipelines in both', () => {
    // "Lost" and "Submitted" are genuinely many-to-many.
    expect(allowedStatusesFor('Individual Session', 'Lost', MAP)).toContain(
      'Lost',
    )
    expect(allowedStatusesFor('Workshop', 'Lost', MAP)).toContain('Lost')
    expect(
      allowedStatusesFor('Delivering Coaching', 'Submitted', MAP),
    ).toContain('Submitted')
  })

  it('always includes the current status even when off-map', () => {
    // Real data: a Workshop deal sitting in "Active", a Delivering Coaching status.
    // Dropping it would leave that deal unable to display or change its status.
    const allowed = allowedStatusesFor('Workshop', 'Active', MAP)
    expect(allowed).toContain('Active')
    expect(allowed[allowed.length - 1]).toBe('Active')
  })

  it('does not duplicate a current status already in the list', () => {
    const allowed = allowedStatusesFor('Workshop', 'Sold', MAP)
    expect(allowed.filter((s) => s === 'Sold')).toHaveLength(1)
  })

  it('falls back to no filtering for an unknown pipeline', () => {
    expect(allowedStatusesFor('Nonexistent', 'Won', MAP)).toEqual([])
    expect(allowedStatusesFor(undefined, 'Won', MAP)).toEqual([])
    expect(allowedStatusesFor('Workshop', 'Sold', undefined)).toEqual([])
    expect(allowedStatusesFor('Workshop', 'Sold', {})).toEqual([])
  })

  it('handles a missing current status', () => {
    expect(allowedStatusesFor('Workshop', null, MAP)).toEqual(MAP.Workshop)
  })

  it('never mutates the source map', () => {
    const before = [...MAP.Workshop]
    allowedStatusesFor('Workshop', 'Active', MAP)
    expect(MAP.Workshop).toEqual(before)
  })
})

describe('statusLinkFilters', () => {
  it('builds an "in" filter for the link field', () => {
    expect(statusLinkFilters('Workshop', 'Sold', MAP)).toEqual({
      name: [
        'in',
        ['Workshop submitted', 'VCS call set', 'Workshop set', 'Sold', 'Lost'],
      ],
    })
  })

  it('includes an off-map current status so the field can render it', () => {
    expect(statusLinkFilters('Workshop', 'Active', MAP).name[1]).toContain(
      'Active',
    )
  })

  it('returns null when there is nothing to restrict', () => {
    expect(statusLinkFilters('Nonexistent', 'Won', MAP)).toBeNull()
    expect(statusLinkFilters('Workshop', 'Sold', {})).toBeNull()
  })
})

import { describe, expect, it } from 'vitest'
import {
  allZero,
  lastDays,
  memberOptions,
  monthRange,
  pageWindow,
  rangeError,
  recordRows,
  recordsParams,
  summaryParams,
  todayIn,
  weekRange,
} from '@/utils/teamActivity'

describe('weekRange / monthRange', () => {
  // Sunday 2026-10-25 22:30 UTC is already Monday 00:30 in Vilnius (UTC+2 after DST ends).
  const sundayNight = new Date('2026-10-25T22:30:00Z')

  it('runs Monday to Sunday in the site timezone', () => {
    expect(weekRange('UTC', 0, sundayNight)).toEqual({
      from: '2026-10-19',
      to: '2026-10-25',
    })
    expect(weekRange('Europe/Vilnius', 0, sundayNight)).toEqual({
      from: '2026-10-26',
      to: '2026-11-01',
    })
    expect(weekRange('UTC', 1, sundayNight)).toEqual({
      from: '2026-10-12',
      to: '2026-10-18',
    })
  })

  it('covers the whole calendar month, leap February included', () => {
    expect(monthRange('UTC', new Date('2028-02-10T12:00:00Z'))).toEqual({
      from: '2028-02-01',
      to: '2028-02-29',
    })
    expect(monthRange('UTC', new Date('2026-12-31T12:00:00Z'))).toEqual({
      from: '2026-12-01',
      to: '2026-12-31',
    })
  })
})

describe('todayIn / lastDays', () => {
  // 2026-10-07 22:30 UTC is already 2026-10-08 in Vilnius (UTC+3).
  const now = new Date('2026-10-07T22:30:00Z')

  it('uses the site timezone calendar date, not UTC', () => {
    expect(todayIn('Europe/Vilnius', now)).toBe('2026-10-08')
    expect(todayIn('UTC', now)).toBe('2026-10-07')
    expect(todayIn('America/New_York', now)).toBe('2026-10-07')
  })

  it('covers exactly the last 30 days inclusive of today', () => {
    expect(lastDays(30, 'Europe/Vilnius', now)).toEqual({
      from: '2026-09-09',
      to: '2026-10-08',
    })
    expect(lastDays(30, 'UTC', now)).toEqual({
      from: '2026-09-08',
      to: '2026-10-07',
    })
  })

  it('crosses month and year boundaries', () => {
    const jan = new Date('2027-01-05T12:00:00Z')
    expect(lastDays(7, 'UTC', jan)).toEqual({
      from: '2026-12-30',
      to: '2027-01-05',
    })
    expect(lastDays(1, 'UTC', jan)).toEqual({
      from: '2027-01-05',
      to: '2027-01-05',
    })
  })
})

describe('rangeError', () => {
  it('accepts an ordered or single-day range', () => {
    expect(rangeError('2026-09-01', '2026-09-30')).toBeNull()
    expect(rangeError('2026-09-01', '2026-09-01')).toBeNull()
  })

  it('blocks a from date after the to date', () => {
    expect(rangeError('2026-10-02', '2026-10-01')).toBe(
      'The start date must not be after the end date.',
    )
  })

  it('blocks missing or malformed dates', () => {
    expect(rangeError('', '2026-10-01')).toBeTruthy()
    expect(rangeError('2026-10-01', null)).toBeTruthy()
    expect(rangeError('01/10/2026', '2026-10-01')).toBeTruthy()
  })
})

describe('request params', () => {
  const team = { from: '2026-09-09', to: '2026-10-08', member: null }

  it('sends no member for the whole team', () => {
    expect(summaryParams(team)).toEqual({
      from_date: '2026-09-09',
      to_date: '2026-10-08',
    })
  })

  it('sends the chosen member', () => {
    expect(summaryParams({ ...team, member: 'a@x.co' })).toEqual({
      from_date: '2026-09-09',
      to_date: '2026-10-08',
      member: 'a@x.co',
    })
  })

  it('records reuse the summary period and member, plus metric and page', () => {
    expect(
      recordsParams('any_metric', { ...team, member: 'a@x.co' }, 2),
    ).toEqual({
      metric: 'any_metric',
      from_date: '2026-09-09',
      to_date: '2026-10-08',
      member: 'a@x.co',
      page: 2,
    })
    expect(recordsParams('any_metric', team)).toMatchObject({ page: 1 })
    expect(recordsParams('any_metric', team)).not.toHaveProperty('member')
  })
})

describe('recordRows', () => {
  it('gives every row a key unique across pages', () => {
    const rows = recordRows([{ a: 1 }, { a: 2 }], 2)
    expect(rows.map((r) => r._key)).toEqual(['2:0', '2:1'])
    expect(rows[0].a).toBe(1)
  })

  it('links a row to the record it references', () => {
    const [deal, meeting] = recordRows([
      { reference_doctype: 'CRM Deal', reference_name: 'D-1' },
      { reference_doctype: 'Event', reference_name: 'E-1' },
    ])
    expect(deal._reference.route).toEqual({
      name: 'Deal',
      params: { dealId: 'D-1' },
    })
    expect(meeting._reference.route).toBe(null)
  })

  it('handles no rows', () => {
    expect(recordRows(undefined)).toEqual([])
  })
})

describe('pageWindow', () => {
  it('pages 20 rows at a time', () => {
    expect(pageWindow(1, 20, 45)).toEqual({ from: 1, to: 20, hasNext: true })
    expect(pageWindow(2, 20, 45)).toEqual({ from: 21, to: 40, hasNext: true })
    expect(pageWindow(3, 20, 45)).toEqual({ from: 41, to: 45, hasNext: false })
    expect(pageWindow(1, 20, 20)).toEqual({ from: 1, to: 20, hasNext: false })
  })

  it('is empty for no records', () => {
    expect(pageWindow(1, 20, 0)).toEqual({ from: 0, to: 0, hasNext: false })
  })
})

describe('memberOptions', () => {
  it('maps the contract list shape', () => {
    expect(
      memberOptions([{ name: 'a@x.co', full_name: 'Ana', user_image: null }]),
    ).toEqual([{ label: 'Ana', value: 'a@x.co' }])
  })

  it('maps the {members: [{user}]} shape', () => {
    expect(
      memberOptions({
        members: [{ user: 'b@x.co', full_name: 'Bo', values: {} }],
      }),
    ).toEqual([{ label: 'Bo', value: 'b@x.co' }])
  })

  it('falls back to the user id and handles no data', () => {
    expect(memberOptions([{ name: 'c@x.co' }])).toEqual([
      { label: 'c@x.co', value: 'c@x.co' },
    ])
    expect(memberOptions(null)).toEqual([])
  })
})

describe('allZero', () => {
  it('is true only when there are metrics and all are 0', () => {
    expect(allZero([{ value: 0 }, { value: 0 }])).toBe(true)
    expect(allZero([{ value: 0 }, { value: 1 }])).toBe(false)
    expect(allZero([])).toBe(false)
  })
})

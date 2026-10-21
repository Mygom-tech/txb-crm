import { describe, expect, it } from 'vitest'
import { flatRows, isSearchShortcut, moveIndex } from '@/utils/globalSearch'

describe('isSearchShortcut', () => {
  it('opens on Ctrl+K and ⌘K, not on plain K or Ctrl+Alt+K', () => {
    expect(isSearchShortcut({ ctrlKey: true, key: 'k' })).toBe(true)
    expect(isSearchShortcut({ metaKey: true, key: 'K' })).toBe(true)
    expect(isSearchShortcut({ key: 'k' })).toBe(false)
    expect(isSearchShortcut({ ctrlKey: true, altKey: true, key: 'k' })).toBe(
      false,
    )
  })
})

describe('flatRows / moveIndex', () => {
  const groups = [
    {
      doctype: 'CRM Lead',
      label: 'Leads',
      total: 2,
      rows: [{ name: 'L1' }, { name: 'L2' }],
    },
    { doctype: 'CRM Deal', label: 'Deals', total: 7, rows: [{ name: 'D1' }] },
  ]

  it('keeps display order and tags each row with its doctype', () => {
    expect(flatRows(groups).map((r) => `${r.doctype}:${r.name}`)).toEqual([
      'CRM Lead:L1',
      'CRM Lead:L2',
      'CRM Deal:D1',
    ])
    expect(flatRows(groups).map((r) => r.heading?.label ?? null)).toEqual([
      'Leads',
      null,
      'Deals',
    ])
    expect(flatRows(undefined)).toEqual([])
  })

  it('wraps the highlight at both ends', () => {
    expect(moveIndex(2, 1, 3)).toBe(0)
    expect(moveIndex(0, -1, 3)).toBe(2)
    expect(moveIndex(-1, 1, 3)).toBe(0)
    expect(moveIndex(0, 1, 0)).toBe(-1)
  })
})

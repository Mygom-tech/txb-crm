import { describe, it, expect } from 'vitest'
import { revertCardMove } from '@/utils/kanbanRevert'

function makeColumns() {
  return [
    {
      column: { name: 'Open' },
      data: [{ name: 'CRM-LEAD-01' }, { name: 'CRM-LEAD-02' }],
    },
    {
      column: { name: 'Contacted' },
      data: [{ name: 'CRM-LEAD-03' }, { name: 'CRM-LEAD-04' }],
    },
  ]
}

describe('revertCardMove', () => {
  it('moves the card back to the source column at oldIndex', () => {
    // simulate: CRM-LEAD-02 was dragged from Open (index 1) to Contacted
    const columns = makeColumns()
    columns[1].data.push(columns[0].data.splice(1, 1)[0])

    const ok = revertCardMove({
      columns,
      itemName: 'CRM-LEAD-02',
      from: 'Open',
      to: 'Contacted',
      oldIndex: 1,
    })

    expect(ok).toBe(true)
    expect(columns[0].data.map((r) => r.name)).toEqual([
      'CRM-LEAD-01',
      'CRM-LEAD-02',
    ])
    expect(columns[1].data.map((r) => r.name)).toEqual([
      'CRM-LEAD-03',
      'CRM-LEAD-04',
    ])
  })

  it('clamps oldIndex when the source column shrank', () => {
    const columns = makeColumns()
    columns[1].data.push(columns[0].data.splice(1, 1)[0])
    columns[0].data.length = 0 // source emptied while modal was open

    const ok = revertCardMove({
      columns,
      itemName: 'CRM-LEAD-02',
      from: 'Open',
      to: 'Contacted',
      oldIndex: 5,
    })

    expect(ok).toBe(true)
    expect(columns[0].data.map((r) => r.name)).toEqual(['CRM-LEAD-02'])
  })

  it('defaults to end of source column when oldIndex is undefined', () => {
    const columns = makeColumns()
    columns[1].data.push(columns[0].data.splice(1, 1)[0])

    const ok = revertCardMove({
      columns,
      itemName: 'CRM-LEAD-02',
      from: 'Open',
      to: 'Contacted',
      oldIndex: undefined,
    })

    expect(ok).toBe(true)
    expect(columns[0].data.map((r) => r.name)).toEqual([
      'CRM-LEAD-01',
      'CRM-LEAD-02',
    ])
  })

  it('returns false when the card is not in the target column', () => {
    const columns = makeColumns()
    const ok = revertCardMove({
      columns,
      itemName: 'CRM-LEAD-99',
      from: 'Open',
      to: 'Contacted',
      oldIndex: 0,
    })
    expect(ok).toBe(false)
  })

  it('returns false when the source column is gone', () => {
    const columns = makeColumns()
    columns[1].data.push(columns[0].data.splice(1, 1)[0])
    columns.splice(0, 1)

    const ok = revertCardMove({
      columns,
      itemName: 'CRM-LEAD-02',
      from: 'Open',
      to: 'Contacted',
      oldIndex: 1,
    })
    expect(ok).toBe(false)
  })

  it('returns false when the target column is gone', () => {
    const ok = revertCardMove({
      columns: [{ column: { name: 'Open' }, data: [] }],
      itemName: 'CRM-LEAD-02',
      from: 'Open',
      to: 'Contacted',
      oldIndex: 0,
    })
    expect(ok).toBe(false)
  })

  it('returns false for empty/absent columns array', () => {
    expect(
      revertCardMove({
        columns: [],
        itemName: 'X',
        from: 'A',
        to: 'B',
        oldIndex: 0,
      }),
    ).toBe(false)
  })
})

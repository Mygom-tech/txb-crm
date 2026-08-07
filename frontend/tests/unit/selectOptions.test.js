import { describe, it, expect } from 'vitest'
import { selectFieldOptions } from '@/utils/selectOptions'

describe('selectFieldOptions', () => {
  it('parses a newline-delimited string, as raw doctype meta stores it', () => {
    expect(
      selectFieldOptions({ options: 'Workshop\nSelling Training' }),
    ).toEqual([
      { label: 'Workshop', value: 'Workshop' },
      { label: 'Selling Training', value: 'Selling Training' },
    ])
  })

  it('passes through the array shape getFields() rewrites the shared meta into', () => {
    const already = [
      { label: 'Workshop', value: 'Workshop' },
      { label: 'Selling Training', value: 'Selling Training' },
    ]
    expect(selectFieldOptions({ options: already })).toEqual(already)
  })

  it('drops the blank entry getFields() prepends to non-required selects', () => {
    expect(
      selectFieldOptions({
        options: [
          { label: '', value: '' },
          { label: 'Workshop', value: 'Workshop' },
        ],
      }),
    ).toEqual([{ label: 'Workshop', value: 'Workshop' }])
  })

  it('drops blank lines from the string shape', () => {
    expect(selectFieldOptions({ options: '\nWorkshop\n' })).toEqual([
      { label: 'Workshop', value: 'Workshop' },
    ])
  })

  it('returns an empty array when meta has not loaded yet', () => {
    expect(selectFieldOptions(undefined)).toEqual([])
    expect(selectFieldOptions({})).toEqual([])
    expect(selectFieldOptions({ options: null })).toEqual([])
  })
})

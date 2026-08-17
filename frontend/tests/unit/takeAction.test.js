import { describe, it, expect, vi } from 'vitest'
import {
  actionOptions,
  actionFields,
  requiredFieldnames,
} from '@/utils/takeAction'

const LOG_CALL = {
  name: 'log_coaching_call',
  label: 'Log Coaching Call',
  to_state: null,
  fields: [
    {
      fieldname: 'call_status',
      label: 'Call Status',
      fieldtype: 'Select',
      reqd: 1,
    },
    {
      fieldname: 'delivery_date',
      label: 'Delivery Date',
      fieldtype: 'Date',
      reqd: 1,
      default: 'Today',
    },
    // Server-owned read-only count, injected immediately above Topic from the deal's
    // canonical total_completed_calls (0 when unset).
    {
      fieldname: 'completed_calls',
      label: 'Completed Calls',
      fieldtype: 'Int',
      read_only: 1,
      default: 0,
    },
    { fieldname: 'topic', label: 'Topic', fieldtype: 'Data', reqd: 1 },
  ],
}

describe('actionOptions', () => {
  it('maps actions to dropdown entries in order', () => {
    const options = actionOptions(
      [{ label: 'Put on Hold' }, { label: 'Mark Inactive' }],
      () => {},
    )
    expect(options.map((o) => o.label)).toEqual([
      'Put on Hold',
      'Mark Inactive',
    ])
  })

  it('invokes onSelect with the action that was clicked', () => {
    const onSelect = vi.fn()
    const hold = { label: 'Put on Hold' }
    actionOptions([hold], onSelect)[0].onClick()
    expect(onSelect).toHaveBeenCalledWith(hold)
  })

  it('handles an empty or missing list', () => {
    expect(actionOptions([], () => {})).toEqual([])
    expect(actionOptions(undefined, () => {})).toEqual([])
  })
})

describe('actionFields', () => {
  it("resolves Frappe's Today default to a real date", () => {
    const fields = actionFields(LOG_CALL, '2026-08-04')
    expect(fields.find((f) => f.fieldname === 'delivery_date').default).toBe(
      '2026-08-04',
    )
  })

  it('leaves other defaults untouched', () => {
    const fields = actionFields(
      { fields: [{ fieldname: 'x', default: 'Fixed' }] },
      '2026-08-04',
    )
    expect(fields[0].default).toBe('Fixed')
  })

  it('never mutates the source action', () => {
    const before = JSON.stringify(LOG_CALL)
    actionFields(LOG_CALL, '2026-08-04')
    expect(JSON.stringify(LOG_CALL)).toBe(before)
  })

  it('handles an action with no fields', () => {
    expect(actionFields({}, '2026-08-04')).toEqual([])
    expect(actionFields(undefined, '2026-08-04')).toEqual([])
  })

  it('keeps the read-only Completed Calls count immediately above Topic', () => {
    const fields = actionFields(LOG_CALL, '2026-08-04')
    const names = fields.map((f) => f.fieldname)
    expect(names.indexOf('completed_calls') + 1).toBe(names.indexOf('topic'))
  })

  it('preserves the server-supplied Completed Calls default and read-only flag', () => {
    const completed = actionFields(LOG_CALL, '2026-08-04').find(
      (f) => f.fieldname === 'completed_calls',
    )
    expect(completed.default).toBe(0)
    expect(completed.read_only).toBe(1)
  })
})

describe('requiredFieldnames', () => {
  it('returns only the fields the server will insist on', () => {
    expect(requiredFieldnames(LOG_CALL)).toEqual([
      'call_status',
      'delivery_date',
      'topic',
    ])
  })

  it('returns nothing when no field is required', () => {
    expect(requiredFieldnames({ fields: [{ fieldname: 'notes' }] })).toEqual([])
    expect(requiredFieldnames(undefined)).toEqual([])
  })
})

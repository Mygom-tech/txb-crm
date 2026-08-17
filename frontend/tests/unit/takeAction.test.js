import { describe, it, expect, vi } from 'vitest'
import {
  actionOptions,
  actionFields,
  requiredFieldnames,
  actionDefaults,
} from '@/utils/takeAction'
import { findMissingMandatory } from '@/utils/fieldTransforms'
import { evaluateDependsOnValue } from '@/utils/expressions'

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
      label: 'Total Completed Calls',
      fieldtype: 'Int',
      read_only: 1,
      default: 0,
    },
    { fieldname: 'topic', label: 'Topic', fieldtype: 'Data', reqd: 1 },
    {
      fieldname: 'call_notes',
      label: 'Coaching Call Notes',
      fieldtype: 'Small Text',
      reqd: 1,
    },
    {
      fieldname: 'is_last_call',
      label: 'This is the last coaching call',
      fieldtype: 'Check',
    },
    // Visible + mandatory only while it is not the last call; hidden + optional once
    // the coach ticks "last call". Both flags travel from the server schema.
    {
      fieldname: 'next_call_date',
      label: 'Next Coaching Call Date',
      fieldtype: 'Datetime',
      depends_on: 'eval:!doc.is_last_call',
      mandatory_depends_on: 'eval:!doc.is_last_call',
    },
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
  it('returns only the unconditionally required fields', () => {
    // next_call_date is conditionally required (mandatory_depends_on), so it is not a
    // static required fieldname — its requiredness is decided per checkbox state.
    expect(requiredFieldnames(LOG_CALL)).toEqual([
      'call_status',
      'delivery_date',
      'topic',
      'call_notes',
    ])
  })

  it('returns nothing when no field is required', () => {
    expect(requiredFieldnames({ fields: [{ fieldname: 'notes' }] })).toEqual([])
    expect(requiredFieldnames(undefined)).toEqual([])
  })
})

describe('actionDefaults', () => {
  it('seeds the reactive document with the read-only Total Completed Calls value', () => {
    // The dialog builds its reactive document from these defaults, so the read-only count
    // renders instead of showing an empty box.
    const seeded = actionDefaults(LOG_CALL, '2026-08-04')
    expect(seeded.completed_calls).toBe(0)
  })

  it('carries a non-zero canonical count through unchanged', () => {
    const withCount = {
      ...LOG_CALL,
      fields: LOG_CALL.fields.map((f) =>
        f.fieldname === 'completed_calls' ? { ...f, default: 7 } : f,
      ),
    }
    expect(actionDefaults(withCount, '2026-08-04').completed_calls).toBe(7)
  })

  it("resolves Frappe's Today default like actionFields does", () => {
    expect(actionDefaults(LOG_CALL, '2026-08-04').delivery_date).toBe(
      '2026-08-04',
    )
  })

  it('lets caller-supplied defaults (e.g. a kanban branch) win', () => {
    const seeded = actionDefaults(LOG_CALL, '2026-08-04', { completed_calls: 3 })
    expect(seeded.completed_calls).toBe(3)
  })

  it('omits fields that carry no default', () => {
    const seeded = actionDefaults(LOG_CALL, '2026-08-04')
    expect('topic' in seeded).toBe(false)
    expect('next_call_date' in seeded).toBe(false)
  })

  it('never mutates the source action', () => {
    const before = JSON.stringify(LOG_CALL)
    actionDefaults(LOG_CALL, '2026-08-04', { x: 1 })
    expect(JSON.stringify(LOG_CALL)).toBe(before)
  })
})

describe('Next Coaching Call Date conditional behavior', () => {
  const nextCallField = LOG_CALL.fields.find(
    (f) => f.fieldname === 'next_call_date',
  )

  it('is visible when this is not the last call', () => {
    expect(
      evaluateDependsOnValue(nextCallField.depends_on, { is_last_call: 0 }),
    ).toBe(true)
  })

  it('is hidden when this is the last call', () => {
    expect(
      evaluateDependsOnValue(nextCallField.depends_on, { is_last_call: 1 }),
    ).toBe(false)
  })

  it('is required and missing when unticked and left blank', () => {
    const missing = findMissingMandatory(LOG_CALL.fields, {
      call_status: 'Completed',
      delivery_date: '2026-08-04',
      completed_calls: 0,
      topic: 'Goals',
      call_notes: 'Great call',
      is_last_call: 0,
    })
    expect(missing).toContain('Next Coaching Call Date')
  })

  it('is satisfied when unticked and a date is supplied', () => {
    const missing = findMissingMandatory(LOG_CALL.fields, {
      call_status: 'Completed',
      delivery_date: '2026-08-04',
      completed_calls: 0,
      topic: 'Goals',
      call_notes: 'Great call',
      is_last_call: 0,
      next_call_date: '2026-08-11 10:00:00',
    })
    expect(missing).not.toContain('Next Coaching Call Date')
  })

  it('is optional when ticked as the last call, even with no date', () => {
    const missing = findMissingMandatory(LOG_CALL.fields, {
      call_status: 'Completed',
      delivery_date: '2026-08-04',
      completed_calls: 0,
      topic: 'Goals',
      call_notes: 'Great call',
      is_last_call: 1,
    })
    expect(missing).not.toContain('Next Coaching Call Date')
  })

  it('still requires Topic in both checkbox states', () => {
    const base = {
      call_status: 'Completed',
      delivery_date: '2026-08-04',
      completed_calls: 0,
      call_notes: 'Great call',
      next_call_date: '2026-08-11 10:00:00',
    }
    expect(
      findMissingMandatory(LOG_CALL.fields, { ...base, is_last_call: 0 }),
    ).toContain('Topic')
    expect(
      findMissingMandatory(LOG_CALL.fields, { ...base, is_last_call: 1 }),
    ).toContain('Topic')
  })
})

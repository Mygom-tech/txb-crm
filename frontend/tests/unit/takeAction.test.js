import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  actionOptions,
  actionFields,
  requiredFieldnames,
  actionDefaults,
} from '@/utils/takeAction'
import { findMissingMandatory } from '@/utils/fieldTransforms'
import { evaluateDependsOnValue } from '@/utils/expressions'
import {
  splitDatetime,
  combineDatetime,
  generateTimeOptions,
  DEFAULT_TIME_OPTIONS_END,
} from '@/utils/timePicker'
import { discoveryScheduleFields } from '@/utils/leadActions'
import { createApp, h, reactive } from 'vue'

// TXB-239: the coaching Datetime fields render through the shared DateTimeWithOptions control.
// Mock the two frappe-ui pickers it composes with faithful render-function stubs — DatePicker
// `@change` with a date string; TimePicker `@change` with a committed "HH:mm" from either an
// option click or a typed value — so the mounted tests below drive the REAL control (opening
// the option list, clicking, typing an early exception) rather than helper arrays alone.
// `vueRef` bridges Vue's `h` into the hoisted mock factory, since top-level imports are not yet
// initialised when the factory is registered.
const vueRef = vi.hoisted(() => ({ h: null }))
vi.mock('frappe-ui', () => ({
  DatePicker: {
    name: 'DatePicker',
    props: ['value', 'format', 'placeholder', 'inputClass'],
    emits: ['change'],
    setup: (props, { emit }) => () =>
      vueRef.h('input', {
        'data-testid': 'date-input',
        value: props.value,
        onChange: (e) => emit('change', e.target.value),
      }),
  },
  TimePicker: {
    name: 'TimePicker',
    props: ['modelValue', 'options', 'interval', 'placeholder', 'inputClass'],
    emits: ['change', 'update:modelValue'],
    // The installed frappe-ui TimePicker emits `update:modelValue`; DateTimeWithOptions bridges
    // it back out as `@change`, while Field.vue's standalone Time branch (TXB-241) binds
    // `@update:model-value` directly. Emit both from an option click and a typed value so either
    // consumer's contract can be driven from the same faithful stub.
    setup: (props, { emit }) => () =>
      vueRef.h('div', [
        vueRef.h('input', {
          'data-testid': 'time-input',
          value: props.modelValue,
          onChange: (e) => {
            emit('change', e.target.value)
            emit('update:modelValue', e.target.value)
          },
        }),
        vueRef.h(
          'ul',
          { 'data-testid': 'time-list' },
          (props.options || []).map((o) =>
            vueRef.h('li', { key: o.value }, [
              vueRef.h(
                'button',
                {
                  type: 'button',
                  'data-testid': 'time-option',
                  'data-value': o.value,
                  onClick: () => {
                    emit('change', o.value)
                    emit('update:modelValue', o.value)
                  },
                },
                o.label,
              ),
            ]),
          ),
        ),
      ]),
  },
}))
import DateTimeWithOptions from '@/components/Controls/DateTimeWithOptions.vue'
vueRef.h = h

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

// TXB-239: the coaching call-date Datetime fields (and, from leadActions, Log a Dial's
// Follow-up Date and the Follow-up transition date-time) declare `time_options_start` and
// render through the shared DateTimeWithOptions control. These mount the REAL control and
// exercise the rendered pickers — opening the 07:00 option list, clicking a slot, and typing
// an earlier exception — pinning the combined-selector experience (AC-1), the preserved early
// manual entry (AC-2), and clean degradation, rather than asserting the helper arrays alone.
describe('coaching Datetime option and value contract (TXB-239)', () => {
  let hosts = []

  // Mount the adapter with a reactive `value` that echoes each emitted change back, as the
  // FieldLayout parent would, so `state.last` is the exact datetime the field would persist.
  function mountControl({ value = '', optionsStart = '07:00' } = {}) {
    const state = reactive({ value, last: undefined })
    const container = document.createElement('div')
    document.body.appendChild(container)
    const app = createApp({
      render: () =>
        h(DateTimeWithOptions, {
          value: state.value,
          optionsStart,
          onChange: (v) => {
            state.last = v
            state.value = v
          },
        }),
    })
    app.config.globalProperties.__ = globalThis.__
    app.mount(container)
    hosts.push({ app, container })
    return { container, state }
  }

  const optionValues = (container) =>
    Array.from(container.querySelectorAll('[data-testid="time-option"]')).map((b) =>
      b.getAttribute('data-value'),
    )
  const clickOption = (container, value) =>
    container
      .querySelector(`[data-testid="time-option"][data-value="${value}"]`)
      .dispatchEvent(new Event('click', { bubbles: true }))
  const changeInput = (container, testid, text) => {
    const input = container.querySelector(`[data-testid="${testid}"]`)
    input.value = text
    input.dispatchEvent(new Event('change', { bubbles: true }))
  }

  afterEach(() => {
    hosts.forEach(({ app, container }) => {
      app.unmount()
      container.remove()
    })
    hosts = []
  })

  it('renders one combined control with a date picker and a real time picker, not a bare input (AC-1/AC-2)', () => {
    const { container } = mountControl({ value: '2026-09-15 07:00:00' })
    expect(container.querySelector('.crm-datetime-picker')).not.toBeNull()
    expect(container.querySelector('[data-testid="date-input"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="time-input"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="time-list"]')).not.toBeNull()
  })

  it('opens a 15-minute time list from 07:00 through 23:45 with nothing earlier (AC-1)', () => {
    const { container } = mountControl({ value: '2026-09-15 07:00:00' })
    const options = optionValues(container)
    expect(options[0]).toBe('07:00')
    expect(options[1]).toBe('07:15')
    expect(options.at(-1)).toBe(DEFAULT_TIME_OPTIONS_END)
    expect(DEFAULT_TIME_OPTIONS_END).toBe('23:45')
    // 07:00 … 23:45 inclusive = ((23*60+45) - (7*60)) / 15 + 1 = 68 slots.
    expect(options).toHaveLength(68)
    expect(new Set(options).size).toBe(options.length)
    expect(options.every((t) => t >= '07:00')).toBe(true)
    expect(options).not.toContain('06:45')
    expect(options).not.toContain('00:00')
  })

  it('honours a different declared start', () => {
    const { container } = mountControl({ value: '2026-09-15 09:30:00', optionsStart: '09:30' })
    const options = optionValues(container)
    expect(options[0]).toBe('09:30')
    expect(options.every((t) => t >= '09:30')).toBe(true)
  })

  it('commits the canonical datetime when a dropdown time is picked (AC-1)', () => {
    const { container, state } = mountControl({ value: '2026-09-15 07:00:00' })
    clickOption(container, '09:00')
    expect(state.last).toBe('2026-09-15 09:00:00')
  })

  it('preserves an earlier time typed by hand, exactly, so it round-trips on reload (AC-2)', () => {
    const { container, state } = mountControl({ value: '2026-09-15 07:00:00' })
    // 06:30 is earlier than the 07:00 dropdown start, but a typed value is kept verbatim.
    changeInput(container, 'time-input', '06:30')
    expect(state.last).toBe('2026-09-15 06:30:00')
  })

  it('combines a newly picked date with the already-selected time', () => {
    const { container, state } = mountControl({ value: '2026-09-15 08:15:00' })
    changeInput(container, 'date-input', '2026-10-01')
    expect(state.last).toBe('2026-10-01 08:15:00')
  })

  it('degrades cleanly with no date yet — a lone time never fabricates a datetime', () => {
    const { container, state } = mountControl({ value: '' })
    // The option list still renders, but with no date there is nothing to store.
    expect(optionValues(container)[0]).toBe('07:00')
    clickOption(container, '09:00')
    expect(state.last).toBe('')
  })

  // The pure split/combine helpers underpin the control above; keep a direct check that the
  // canonical value contract the FieldLayout persists is exact.
  it('splits and recombines the stored datetime without drift', () => {
    expect(splitDatetime('')).toEqual({ date: '', time: '' })
    expect(splitDatetime(null)).toEqual({ date: '', time: '' })
    expect(combineDatetime('2026-09-15', '07:00')).toBe('2026-09-15 07:00:00')
    expect(combineDatetime('2026-09-15', '')).toBe('2026-09-15')
    const stored = '2026-09-15 06:30:00'
    const { date, time } = splitDatetime(stored)
    expect(combineDatetime(date, time)).toBe(stored)
  })
})

// TXB-241: Schedule Discovery Meeting renders a *standalone* Time field for `meeting_time`, so
// the TXB-239 combined DateTimeWithOptions path above never applied. The field now declares
// `time_options_start: '07:00'`, and Field.vue's Time branch feeds the real frappe-ui TimePicker
// the generated 07:00–23:45 `{ value, label }` list only for such fields, committing through the
// canonical `update:modelValue` route (TXB-236). These mount the real TimePicker exactly as that
// branch binds it — proving the rendered dropdown starts at 07:00 (AC-1), a picked time and an
// earlier typed exception both commit (AC-2/AC-3), and a metadata-free Time field keeps the
// picker default list (AC-3). Field.vue's `timeFieldOptions` wiring is pinned at source level in
// leadActions.test.js; the transformation below is the same one, kept in lockstep by those asserts.
describe('Discovery standalone Time option and commit contract (TXB-241)', () => {
  let hosts = []

  const timeFieldOptions = (field) => {
    if (!field.time_options_start) return null
    return generateTimeOptions(field.time_options_start).map((time) => ({
      value: time,
      label: time,
    }))
  }

  // Mount the real TimePicker bound as Field.vue's Time branch does: `:model-value`,
  // `:options="timeFieldOptions(field)"`, commit on `@update:model-value`. `state.value` echoes
  // each committed value back, as the reactive FieldLayout `data` snapshotted on submit would.
  function mountTime(field, initial = '') {
    const state = reactive({ value: initial })
    const container = document.createElement('div')
    document.body.appendChild(container)
    const app = createApp({
      render: () =>
        h(TimePicker, {
          modelValue: state.value,
          options: timeFieldOptions(field),
          'onUpdate:modelValue': (v) => {
            state.value = v
          },
        }),
    })
    app.config.globalProperties.__ = globalThis.__
    app.mount(container)
    hosts.push({ app, container })
    return { container, state }
  }

  const optionValues = (container) =>
    Array.from(container.querySelectorAll('[data-testid="time-option"]')).map((b) =>
      b.getAttribute('data-value'),
    )
  const clickOption = (container, value) =>
    container
      .querySelector(`[data-testid="time-option"][data-value="${value}"]`)
      .dispatchEvent(new Event('click', { bubbles: true }))
  const typeTime = (container, text) => {
    const input = container.querySelector('[data-testid="time-input"]')
    input.value = text
    input.dispatchEvent(new Event('change', { bubbles: true }))
  }

  const meetingTimeField = () =>
    discoveryScheduleFields().find((f) => f.fieldname === 'meeting_time')

  afterEach(() => {
    hosts.forEach(({ app, container }) => {
      app.unmount()
      container.remove()
    })
    hosts = []
  })

  it('opens a 15-minute list from 07:00 through 23:45 with nothing earlier (AC-1)', () => {
    const { container } = mountTime(meetingTimeField(), '09:00:00')
    const options = optionValues(container)
    expect(options[0]).toBe('07:00')
    expect(options[1]).toBe('07:15')
    expect(options.at(-1)).toBe(DEFAULT_TIME_OPTIONS_END)
    expect(DEFAULT_TIME_OPTIONS_END).toBe('23:45')
    // 07:00 … 23:45 inclusive = ((23*60+45) - (7*60)) / 15 + 1 = 68 slots.
    expect(options).toHaveLength(68)
    expect(new Set(options).size).toBe(options.length)
    expect(options.every((t) => t >= '07:00')).toBe(true)
    expect(options).not.toContain('06:45')
    expect(options).not.toContain('00:00')
  })

  it('commits a dropdown-selected time through update:modelValue (AC-2)', () => {
    const { container, state } = mountTime(meetingTimeField(), '')
    clickOption(container, '09:00')
    expect(state.value).toBe('09:00')
  })

  it('commits an earlier time typed by hand, verbatim, so it survives to the snapshot (AC-2)', () => {
    const { container, state } = mountTime(meetingTimeField(), '')
    // 06:30 is earlier than the 07:00 dropdown start, but a typed exception is kept as-is — the
    // option start is a suggestion, not a validation minimum.
    typeTime(container, '06:30')
    expect(state.value).toBe('06:30')
  })

  it('leaves a metadata-free Time field on the picker default list (AC-3)', () => {
    // A plain Time field passes null options, so frappe-ui keeps its own default list and every
    // other Time field is untouched.
    const plainTime = { fieldname: 'other_time', fieldtype: 'Time' }
    const { container } = mountTime(plainTime, '')
    expect(timeFieldOptions(plainTime)).toBeNull()
    expect(optionValues(container)).toHaveLength(0)
  })
})

import { describe, it, expect, vi } from 'vitest'
import { nextTick, reactive } from 'vue'

import {
  INTERVAL_FIELD,
  MAX_INTERVAL_MINUTES,
  intervalLabel,
  parseInterval,
  statusRows,
  useIntervalDraft,
} from '@/components/ContactInactivity/contactInactivity'
import { taskReference } from '@/components/ContactInactivity/taskReference'

describe('parseInterval', () => {
  it.each([
    ['0', 0],
    ['1', 1],
    ['90', 90],
    [' 90 ', 90],
    [String(MAX_INTERVAL_MINUTES), MAX_INTERVAL_MINUTES],
    [90, 90],
  ])('accepts %j as %d whole minutes', (input, value) => {
    expect(parseInterval(input)).toEqual({ value })
  })

  it.each([[''], ['   '], [null], [undefined]])('maps blank %j to 0', (input) => {
    expect(parseInterval(input)).toEqual({ value: 0 })
  })

  it.each([
    ['-1'],
    [-5],
    ['1.5'],
    [1.5],
    [String(MAX_INTERVAL_MINUTES + 1)],
    [MAX_INTERVAL_MINUTES + 1],
    ['abc'],
    ['1e3'],
    ['10 minutes'],
  ])('rejects %j', (input) => {
    const result = parseInterval(input)
    expect(result.value).toBeUndefined()
    expect(result.error).toBeTruthy()
  })
})

describe('useIntervalDraft', () => {
  function settingsWith(minutes) {
    return reactive({
      doc: { [INTERVAL_FIELD]: minutes },
      save: { submit: vi.fn() },
    })
  }

  it('starts from the saved value, blank for 0', () => {
    expect(useIntervalDraft(settingsWith(0)).draft.value).toBe('')
    expect(useIntervalDraft(settingsWith(120)).draft.value).toBe('120')
  })

  it('keeps edits as a draft until Save', async () => {
    const settings = settingsWith(0)
    const { draft, dirty } = useIntervalDraft(settings)

    draft.value = '90'
    await nextTick()

    expect(dirty.value).toBe(true)
    expect(settings.doc[INTERVAL_FIELD]).toBe(0)
    expect(settings.save.submit).not.toHaveBeenCalled()
  })

  it('writes the parsed value and saves when Save is clicked', () => {
    const settings = settingsWith(0)
    const { draft, save } = useIntervalDraft(settings)
    const options = { onSuccess: vi.fn() }

    draft.value = '90'
    expect(save(options)).toBe(true)

    expect(settings.doc[INTERVAL_FIELD]).toBe(90)
    expect(settings.save.submit).toHaveBeenCalledTimes(1)
    expect(settings.save.submit).toHaveBeenCalledWith(null, options)
  })

  it('saves a cleared draft as 0', () => {
    const settings = settingsWith(120)
    const { draft, save } = useIntervalDraft(settings)

    draft.value = ''
    save()

    expect(settings.doc[INTERVAL_FIELD]).toBe(0)
    expect(settings.save.submit).toHaveBeenCalledTimes(1)
  })

  it('refuses to save an invalid draft', () => {
    const settings = settingsWith(30)
    const { draft, error, dirty, save } = useIntervalDraft(settings)

    draft.value = '1.5'

    expect(error.value).toBeTruthy()
    expect(dirty.value).toBe(false)
    expect(save()).toBe(false)
    expect(settings.doc[INTERVAL_FIELD]).toBe(30)
    expect(settings.save.submit).not.toHaveBeenCalled()
  })
})

describe('taskReference', () => {
  it('opens a Contact reference on the Contact page', () => {
    expect(taskReference('Contact', 'CONTACT-1')).toEqual({
      label: 'Contact',
      route: { name: 'Contact', params: { contactId: 'CONTACT-1' } },
    })
  })

  it('opens Deals and Leads on their own pages', () => {
    expect(taskReference('CRM Deal', 'CRM-DEAL-1')).toEqual({
      label: 'Deal',
      route: { name: 'Deal', params: { dealId: 'CRM-DEAL-1' } },
    })
    expect(taskReference('CRM Lead', 'CRM-LEAD-1')).toEqual({
      label: 'Lead',
      route: { name: 'Lead', params: { leadId: 'CRM-LEAD-1' } },
    })
  })

  it('gives an unknown doctype or a missing name no route', () => {
    expect(taskReference('CRM Organization', 'ORG-1').route).toBeNull()
    expect(taskReference('', 'X').route).toBeNull()
    expect(taskReference('Contact', '').route).toBeNull()
  })
})

describe('status helper', () => {
  it('labels a 0 or blank interval as six calendar months', () => {
    expect(intervalLabel(0)).toBe('6 calendar months')
    expect(intervalLabel(null)).toBe('6 calendar months')
    expect(intervalLabel('')).toBe('6 calendar months')
  })

  it('labels a positive interval in minutes', () => {
    expect(intervalLabel(90)).toBe('90 minutes')
    expect(intervalLabel(1)).toBe('1 minute')
  })

  const inactivity = {
    anchor_at: '2026-04-01 09:00:00',
    anchor_source: 'History',
    due_on: '2026-10-01 09:00:00',
    status: 'Due',
    reminder_task: null,
    exception: null,
    interval_minutes: 0,
  }

  it('shows the status, interval, anchor and due date', () => {
    const rows = statusRows(inactivity)
    expect(rows.map((row) => row.label)).toEqual([
      'Status',
      'Interval',
      'Last contact',
      'Due',
    ])
    expect(rows.find((row) => row.label === 'Interval').value).toBe(
      '6 calendar months',
    )
    expect(rows.find((row) => row.label === 'Due')).toMatchObject({
      value: '2026-10-01 09:00:00',
      date: true,
    })
  })

  it('omits the due date when there is none and shows an exception', () => {
    const rows = statusRows({
      ...inactivity,
      due_on: null,
      exception: 'Unowned',
      interval_minutes: 45,
    })
    expect(rows.find((row) => row.label === 'Due')).toBeUndefined()
    expect(rows.find((row) => row.label === 'Interval').value).toBe('45 minutes')
    expect(rows.find((row) => row.label === 'Exception').value).toBe('Unowned')
  })

  it('shows nothing for a Contact without a cycle', () => {
    expect(statusRows(null)).toEqual([])
    expect(statusRows({ status: null, due_on: null })).toEqual([])
  })
})

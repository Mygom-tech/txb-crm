import { computed, ref, watch } from 'vue'

// FCRM Settings.custom_contact_inactivity_minutes (TXB-278): whole minutes in
// 0..525600, 0 or blank meaning six calendar months in the site's time zone.
export const INTERVAL_FIELD = 'custom_contact_inactivity_minutes'
export const MAX_INTERVAL_MINUTES = 525600

export const API = 'crm.txb.api.contact_inactivity'

// The interval as `{ value }` whole minutes (blank = 0), or `{ error }`.
export function parseInterval(input) {
  const text = input == null ? '' : String(input).trim()
  if (!text) return { value: 0 }
  if (!/^\d+$/.test(text)) {
    return {
      error: __('Enter a whole number of minutes between 0 and {0}.', [
        MAX_INTERVAL_MINUTES,
      ]),
    }
  }
  const value = Number(text)
  if (value > MAX_INTERVAL_MINUTES) {
    return {
      error: __('The interval cannot be more than {0} minutes.', [
        MAX_INTERVAL_MINUTES,
      ]),
    }
  }
  return { value }
}

export function intervalLabel(minutes) {
  const value = Number(minutes) || 0
  if (!value) return __('6 calendar months')
  return value === 1 ? __('1 minute') : __('{0} minutes', [value])
}

// The rows ContactInactivityStatus shows; date rows carry `date: true` for formatting.
export function statusRows(inactivity) {
  if (!inactivity?.status) return []
  const rows = [
    { label: __('Status'), value: inactivity.status },
    { label: __('Interval'), value: intervalLabel(inactivity.interval_minutes) },
    {
      label: __('Last contact'),
      value: inactivity.anchor_at,
      date: true,
      hint: inactivity.anchor_source,
    },
  ]
  if (inactivity.due_on) {
    rows.push({ label: __('Due'), value: inactivity.due_on, date: true })
  }
  if (inactivity.exception) {
    rows.push({ label: __('Exception'), value: inactivity.exception })
  }
  return rows
}

// A draft of the interval that reaches `settings.doc` and the server only on `save()`.
export function useIntervalDraft(settings) {
  const draft = ref('')
  watch(
    () => settings.doc?.[INTERVAL_FIELD],
    (saved) => (draft.value = saved ? String(saved) : ''),
    { immediate: true },
  )
  const parsed = computed(() => parseInterval(draft.value))
  const error = computed(() => parsed.value.error || '')
  const dirty = computed(
    () =>
      !error.value &&
      parsed.value.value !== (Number(settings.doc?.[INTERVAL_FIELD]) || 0),
  )

  function save(options) {
    if (error.value || !settings.doc) return false
    settings.doc[INTERVAL_FIELD] = parsed.value.value
    settings.save.submit(null, options)
    return true
  }

  return { draft, error, dirty, save }
}

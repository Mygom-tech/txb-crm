import { computed } from 'vue'
import { getSettings } from '@/stores/settings'
import { formatDate, timeAgo } from '@/utils'

export function useTimelinePreferences() {
  const { _settings } = getSettings()

  const timestampFormat = computed(
    () => _settings.doc?.crm_timeline_timestamp_format || 'Relative',
  )

  const showExactTimestamp = computed(() => timestampFormat.value === 'Exact')

  // Timeline chronology is a fixed CRM-wide invariant (newest-first) and is no longer a user
  // preference, so crm_timeline_sort_order is intentionally not read here. Any saved value stays
  // stored but inert. Only the timestamp-format preference is surfaced.
  return {
    timestampFormat,
    showExactTimestamp,
  }
}

// Builds a { label, timeAgo } cell for list/table views, honoring the global
// FCRM Settings timestamp format. `timeAgo` is the displayed value and `label`
// is the tooltip; when "Exact" is set the two are swapped so the date shows.
// Kept here (not in utils/index.js) so the settings store is not pulled into
// the eager import graph and fetched before frappe-ui is configured.
export function timestampCell(date) {
  if (!date) return { label: '', timeAgo: '' }
  const exact = formatDate(date)
  const relative = __(timeAgo(date))
  const showExact =
    getSettings()._settings.doc?.crm_timeline_timestamp_format === 'Exact'
  return {
    label: showExact ? relative : exact,
    timeAgo: showExact ? exact : relative,
  }
}

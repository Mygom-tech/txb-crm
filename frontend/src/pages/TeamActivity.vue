<template>
  <div class="flex h-full flex-col overflow-hidden">
    <LayoutHeader>
      <template #left-header>
        <Breadcrumbs
          :items="[
            { label: __('Team activity'), route: { name: 'Team Activity' } },
          ]"
        />
      </template>
      <template #right-header>
        <Button
          variant="outline"
          :label="__('Refresh')"
          :iconLeft="LucideRefreshCcw"
          :loading="summary.loading"
          @click="load"
        />
      </template>
    </LayoutHeader>

    <div class="flex flex-col gap-3 overflow-y-auto p-5">
      <FilterRow
        :filters="filters"
        :members="members"
        :timezone="timezone"
        @update="update"
      />

      <ErrorMessage v-if="rangeProblem" :message="rangeProblem" />
      <template v-else-if="filters.mode === 'person' && !filters.member">
        <span class="text-sm text-ink-gray-6">
          {{ __('Person · choose a team member') }}
        </span>
        <div
          class="flex flex-col items-center gap-3 rounded-md border border-dashed border-outline-gray-2 py-12 text-center"
          data-testid="no-member-panel"
        >
          <span class="text-base font-medium text-ink-gray-9">
            {{ __('Choose a team member') }}
          </span>
          <span class="text-sm text-ink-gray-6">
            {{ __('Pick someone in the member field to see their activity.') }}
          </span>
          <Button
            variant="subtle"
            :label="__('Back to whole team')"
            @click="update({ mode: 'team' })"
          />
        </div>
      </template>
      <template v-else>
        <span class="text-sm text-ink-gray-6">{{ scopeLine }}</span>
        <div
          v-if="summary.error && !summary.loading"
          class="flex flex-col items-start gap-2 rounded-md border border-outline-gray-2 p-5"
          data-testid="error-panel"
        >
          <span class="text-base font-medium text-ink-gray-9">
            {{ __("Couldn't load team activity") }}
          </span>
          <ErrorMessage :message="summary.error" />
          <Button variant="outline" :label="__('Retry')" @click="load" />
        </div>
        <template v-else>
          <MetricCards
            :metrics="metrics"
            :loading="summary.loading"
            @view="openRecords"
          />
          <span
            v-if="!summary.loading && allZero(metrics)"
            class="text-sm text-ink-gray-5"
          >
            {{ __('No activity in this period') }}
          </span>
        </template>
      </template>
    </div>

    <RecordsDialog
      v-model="showRecords"
      :metric="recordsMetric"
      :filters="filters"
      :scope="`${scopeLine} · ${period} · ${timezone}`"
      :period="period"
    />
  </div>
</template>

<script setup>
import LucideRefreshCcw from '~icons/lucide/refresh-ccw'
import LayoutHeader from '@/components/LayoutHeader.vue'
import FilterRow from '@/components/TeamActivity/FilterRow.vue'
import MetricCards from '@/components/TeamActivity/MetricCards.vue'
import RecordsDialog from '@/components/TeamActivity/RecordsDialog.vue'
import { usersStore } from '@/stores/users'
import {
  allZero,
  lastDays,
  memberOptions,
  rangeError,
  summaryParams,
} from '@/utils/teamActivity'
import {
  Breadcrumbs,
  Button,
  createResource,
  dayjs,
  ErrorMessage,
  usePageMeta,
} from 'frappe-ui'
import { computed, reactive, ref } from 'vue'

const { crmUsers, getUser } = usersStore()

const siteTimezone = window.timezone?.system

const filters = reactive({
  mode: 'team',
  preset: 'Last 30 Days',
  ...lastDays(30, siteTimezone),
  member: null,
})

const rangeProblem = ref(null)
const showRecords = ref(false)
const recordsMetric = ref(null)

const summary = createResource({
  url: 'crm.txb.api.team_activity.summary',
})

const membersResource = createResource({
  url: 'crm.txb.api.team_activity.members',
})

const metrics = computed(() => summary.data?.metrics || [])
const members = computed(() => memberOptions(membersResource.data))
const timezone = computed(() => summary.data?.timezone || siteTimezone || '')

const period = computed(() =>
  [filters.from, filters.to]
    .map((date) => dayjs(date).format('D MMM YYYY'))
    .join(' – '),
)

const scopeLine = computed(() => {
  if (filters.member) {
    return __(
      '{0} · activity credited to this user as the recorder, not the current owner',
      [getUser(filters.member).full_name],
    )
  }
  const count = (crmUsers.value || []).filter((u) => u.enabled).length
  return __(
    'Whole team · {0} enabled CRM users · activity credited to the user who recorded it',
    [count],
  )
})

function update(patch) {
  if (patch.days) {
    Object.assign(filters, lastDays(patch.days, siteTimezone))
    delete patch.days
  }
  if (patch.mode === 'team') patch.member = null
  Object.assign(filters, patch)
  load()
}

// Every filter change closes the dialog and refetches, unless the period is invalid (checked
// here, before any request) or Person mode has no member yet.
function load() {
  showRecords.value = false
  rangeProblem.value = rangeError(filters.from, filters.to)
  if (rangeProblem.value) return
  if (filters.mode !== 'person') return summary.submit(summaryParams(filters))
  if (!membersResource.data && !membersResource.loading) {
    membersResource.submit({ from_date: filters.from, to_date: filters.to })
  }
  if (filters.member) summary.submit(summaryParams(filters))
}

function openRecords(metric) {
  recordsMetric.value = metric
  showRecords.value = true
}

load()

usePageMeta(() => ({ title: __('Team activity') }))
</script>

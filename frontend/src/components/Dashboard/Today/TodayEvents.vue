<template>
  <WidgetCard
    :title="__(`Today's events`)"
    :icon="CalendarIcon"
    :badge="upcoming ? __('{0} upcoming', [upcoming]) : ''"
    :height="height"
    :loading="events.loading && !events.data"
    @expand="show = true"
  >
    <p v-if="!rows.length" class="p-4 text-base text-ink-gray-5">
      {{ __('No events today.') }}
    </p>
    <div class="py-1.5">
      <template v-for="(event, i) in rows" :key="event.name">
        <div
          v-if="i == nowIndex"
          class="flex items-center gap-2 px-3.5 py-1"
          aria-hidden="true"
        >
          <span class="text-xs font-semibold text-amber-700">
            {{ __('Now') }} {{ nowLabel }}
          </span>
          <span class="h-px flex-1 bg-amber-300" />
        </div>
        <button
          type="button"
          class="flex w-full items-start gap-2.5 px-3.5 py-1.5 text-left hover:bg-surface-gray-1"
          :class="{ 'opacity-60': event.past }"
          @click="openEvent(event)"
        >
          <span class="w-11 shrink-0 pt-px text-sm font-medium text-ink-gray-7">
            {{ event.time }}
          </span>
          <div class="min-w-0 flex-1">
            <div class="truncate text-base text-ink-gray-9">
              {{ event.subject }}
            </div>
            <div class="truncate text-sm text-ink-gray-5">{{ event.meta }}</div>
          </div>
          <UserAvatar :user="event.owner" size="sm" class="shrink-0" />
        </button>
      </template>
      <div
        v-if="rows.length && nowIndex == rows.length"
        class="flex items-center gap-2 px-3.5 py-1"
        aria-hidden="true"
      >
        <span class="text-xs font-semibold text-amber-700">
          {{ __('Now') }} {{ nowLabel }}
        </span>
        <span class="h-px flex-1 bg-amber-300" />
      </div>
    </div>
  </WidgetCard>

  <Dialog v-model:open="show" size="5xl">
    <template #body>
      <div class="px-5 pb-5 pt-4">
        <div class="mb-3 flex items-center gap-2">
          <h3 class="text-xl font-semibold text-ink-gray-9">
            {{ __(`Today's events`) }}
          </h3>
          <span class="text-base text-ink-gray-5">
            {{ todayLabel }} · {{ __('{0} events', [rows.length]) }}
          </span>
          <Button
            class="ml-auto"
            variant="ghost"
            icon="lucide-x"
            :aria-label="__('Close')"
            @click="show = false"
          />
        </div>
        <p v-if="!rows.length" class="py-6 text-center text-base text-ink-gray-5">
          {{ __('No events today.') }}
        </p>
        <div v-else class="max-h-[65vh] overflow-auto">
          <div class="flex gap-2 pl-12">
            <div
              v-for="column in columns"
              :key="column.user"
              class="flex min-w-36 flex-1 items-center gap-1.5 pb-2 text-base font-medium text-ink-gray-8"
            >
              <UserAvatar :user="column.user" size="sm" />
              <span class="truncate">{{ getUser(column.user).full_name }}</span>
            </div>
          </div>
          <div class="relative flex gap-2 pl-12" :style="{ height: `${gridHeight}px` }">
            <div
              v-for="hour in hours"
              :key="hour.label"
              class="pointer-events-none absolute inset-x-0 flex items-center gap-2"
              :style="{ top: `${hour.top - 6}px` }"
              aria-hidden="true"
            >
              <span class="w-10 text-right text-xs text-ink-gray-5">{{ hour.label }}</span>
              <span class="h-px flex-1 bg-outline-gray-1" />
            </div>
            <div
              v-if="nowTop !== null"
              class="pointer-events-none absolute left-12 right-0 z-10 h-0.5 bg-amber-500"
              :style="{ top: `${nowTop}px` }"
              aria-hidden="true"
            />
            <div v-for="column in columns" :key="column.user" class="relative min-w-36 flex-1">
              <button
                v-for="event in column.events"
                :key="event.name"
                type="button"
                class="absolute inset-x-0 overflow-hidden rounded bg-surface-blue-2 px-2 py-1 text-left text-ink-gray-8 hover:brightness-95"
                :class="{ 'opacity-60': event.past }"
                :style="{ top: `${event.top}px`, height: `${event.height}px` }"
                @click="openEvent(event)"
              >
                <div class="truncate text-sm font-semibold">{{ event.subject }}</div>
                <div class="truncate text-xs">{{ event.time }} · {{ event.meta }}</div>
              </button>
            </div>
          </div>
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import WidgetCard from './WidgetCard.vue'
import CalendarIcon from '@/components/Icons/CalendarIcon.vue'
import UserAvatar from '@/components/UserAvatar.vue'
import { taskReference } from '@/components/ContactInactivity/taskReference'
import { usersStore } from '@/stores/users'
import { formatDate } from '@/utils'
import { createResource, Dialog } from 'frappe-ui'
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  user: { type: String, default: null },
  height: { type: String, default: 'auto' },
})

const HOUR_PX = 44
const { getUser, isAdmin, isManager } = usersStore()
const router = useRouter()
const show = ref(false)

const events = createResource({
  url: 'crm.api.dashboard.get_today_events',
  makeParams: () => ({ user: props.user }),
  auto: true,
})
watch(() => props.user, () => events.reload())

const today = formatDate(new Date(), 'YYYY-MM-DD')
const todayLabel = formatDate(new Date(), 'ddd, MMM D')
// Event times are in the site time zone, so "now" is read in it too, not the browser's.
const nowLabel = formatDate(new Date(), 'HH:mm')
const nowHours = Number(nowLabel.slice(0, 2)) + Number(nowLabel.slice(3, 5)) / 60

// Hours since today's midnight, clamped to today (an event may start yesterday or end tomorrow).
function hoursOf(datetime, fallback) {
  if (!datetime) return fallback
  if (datetime.slice(0, 10) < today) return 0
  if (datetime.slice(0, 10) > today) return 24
  return Number(datetime.slice(11, 13)) + Number(datetime.slice(14, 16)) / 60
}

const rows = computed(() =>
  (events.data || []).map((event) => {
    const start = event.all_day ? 0 : hoursOf(event.starts_on, 0)
    const end = event.all_day ? 24 : hoursOf(event.ends_on, start + 0.5)
    const minutes = Math.round((end - start) * 60)
    const { label } = taskReference(event.reference_doctype, event.reference_docname)
    return {
      ...event,
      start,
      end,
      past: end <= nowHours,
      time: event.all_day ? __('All day') : start == 0 ? __('Ongoing') : formatDate(event.starts_on, 'HH:mm'),
      meta: [
        label && `${label} · ${event.reference_title || event.reference_docname}`,
        !event.all_day && minutes > 0 && __('{0} min', [minutes]),
      ]
        .filter(Boolean)
        .join(' · '),
    }
  }),
)

const upcoming = computed(() => rows.value.filter((e) => !e.past).length)
const nowIndex = computed(() => {
  const i = rows.value.findIndex((e) => e.start > nowHours)
  return i == -1 ? rows.value.length : i
})

const firstHour = computed(() =>
  Math.min(8, ...rows.value.map((e) => Math.floor(e.start))),
)
const lastHour = computed(() =>
  Math.max(18, ...rows.value.map((e) => Math.ceil(e.end))),
)
const gridHeight = computed(() => (lastHour.value - firstHour.value) * HOUR_PX + 20)
const hours = computed(() =>
  Array.from({ length: lastHour.value - firstHour.value + 1 }, (_, i) => ({
    label: `${String(firstHour.value + i).padStart(2, '0')}:00`,
    top: 10 + i * HOUR_PX,
  })),
)
const nowTop = computed(() =>
  nowHours >= firstHour.value && nowHours <= lastHour.value
    ? 10 + (nowHours - firstHour.value) * HOUR_PX
    : null,
)

// A manager gets one column per event owner; a Sales User one column of their own.
const columns = computed(() => {
  const me = getUser().name
  const byUser = new Map()
  for (const event of rows.value) {
    const user = isAdmin() || isManager() ? event.owner : me
    if (!byUser.has(user)) byUser.set(user, [])
    byUser.get(user).push({
      ...event,
      top: 10 + (event.start - firstHour.value) * HOUR_PX,
      height: Math.max(34, (event.end - event.start) * HOUR_PX),
    })
  }
  return [...byUser].map(([user, events]) => ({ user, events }))
})

function openEvent(event) {
  const { route } = taskReference(event.reference_doctype, event.reference_docname)
  if (!route) return
  show.value = false
  router.push(route)
}
</script>

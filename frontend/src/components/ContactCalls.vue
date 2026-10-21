<template>
  <!-- Contact Calls: the `calls` category of the same Contact aggregate stream the Notes tab
       and Activity timeline read, as a searchable, source-linked table (desktop) / list
       (mobile). The host page offers Log a Call (desktop header, mobile tab); a row opens the
       call's detail. -->
  <div class="flex flex-1 flex-col overflow-hidden">
    <div class="flex items-center gap-2 px-3 pt-4 sm:px-10">
      <TextInput
        v-model="search"
        class="w-full sm:w-80"
        type="text"
        :placeholder="__('Search calls')"
      >
        <template #prefix>
          <FeatherIcon name="search" class="h-4 w-4 text-ink-gray-4" />
        </template>
      </TextInput>
    </div>

    <div
      v-if="activities.loading"
      class="flex flex-1 flex-col items-center justify-center gap-3 text-2xl-medium text-ink-gray-4"
    >
      <LoadingIndicator class="h-6 w-6" />
      <span>{{ __('Loading...') }}</span>
    </div>

    <div
      v-else-if="!filteredCalls.length"
      class="flex flex-1 flex-col items-center justify-center gap-3 text-xl-medium text-ink-gray-4"
    >
      <PhoneIcon class="h-10 w-10" />
      <span>{{ search ? __('No matching calls') : __('No calls found') }}</span>
    </div>

    <FadedScrollableDiv v-else class="flex flex-1 flex-col overflow-y-auto">
      <!-- Desktop table -->
      <div class="hidden px-10 py-4 sm:block">
        <div
          :class="[COLUMNS, 'grid gap-3 border-b pb-2 text-sm text-ink-gray-5']"
        >
          <div>{{ __('Call') }}</div>
          <div>{{ __('Status') }}</div>
          <div>{{ __('Source') }}</div>
          <div>{{ __('By') }}</div>
          <div>{{ __('Duration') }}</div>
          <div>{{ __('When') }}</div>
        </div>
        <div
          v-for="call in filteredCalls"
          :key="call.name"
          :class="[
            COLUMNS,
            'grid cursor-pointer items-center gap-3 border-b py-3 text-base hover:bg-surface-gray-1',
          ]"
          @click="openCall(call)"
        >
          <div class="truncate font-medium text-ink-gray-8">
            {{ callTypeLabel(call) }}
          </div>
          <div>
            <Badge
              v-if="call.status"
              :label="statusLabelMap[call.status] || call.status"
              :theme="statusColorMap[call.status] || 'gray'"
              variant="subtle"
              size="sm"
            />
          </div>
          <div class="flex min-w-0 items-center" @click.stop>
            <ContactSourceLink :activity="call" :leadEmails="leadEmails" />
          </div>
          <div class="truncate text-ink-gray-7" :title="call._caller?.label">
            {{ call._caller?.label }}
          </div>
          <div class="text-ink-gray-6">{{ call._duration }}</div>
          <TimelineTimestamp
            :date="call.creation"
            class-name="text-ink-gray-6"
          />
        </div>
      </div>

      <!-- Mobile list -->
      <div class="flex flex-col gap-3 px-3 py-4 sm:hidden">
        <div
          v-for="call in filteredCalls"
          :key="call.name"
          class="flex cursor-pointer flex-col gap-2 rounded-md bg-surface-gray-1 px-4 py-3 hover:bg-surface-gray-2"
          @click="openCall(call)"
        >
          <div class="flex items-center justify-between gap-2">
            <span class="truncate text-lg-medium text-ink-gray-8">
              {{ callTypeLabel(call) }}
            </span>
            <Badge
              v-if="call.status"
              :label="statusLabelMap[call.status] || call.status"
              :theme="statusColorMap[call.status] || 'gray'"
              variant="subtle"
              size="sm"
            />
          </div>
          <div class="flex flex-wrap items-center gap-1.5" @click.stop>
            <ContactSourceLink :activity="call" :leadEmails="leadEmails" />
          </div>
          <div
            class="flex items-center justify-between gap-2 text-sm text-ink-gray-6"
          >
            <span class="truncate">{{ call._caller?.label }}</span>
            <TimelineTimestamp :date="call.creation" class-name="text-sm" />
          </div>
        </div>
      </div>
    </FadedScrollableDiv>

    <CallLogDetailModal v-model="showCallDetail" v-model:callLog="callLog" />
  </div>
</template>

<script setup>
import ContactSourceLink from '@/components/Activities/ContactSourceLink.vue'
import TimelineTimestamp from '@/components/Activities/TimelineTimestamp.vue'
import CallLogDetailModal from '@/components/Modals/CallLogDetailModal.vue'
import PhoneIcon from '@/components/Icons/PhoneIcon.vue'
import LoadingIndicator from '@/components/Icons/LoadingIndicator.vue'
import FadedScrollableDiv from '@/components/FadedScrollableDiv.vue'
import { useContactActivityFeed } from '@/composables/contactActivityFeed'
import { statusLabelMap, statusColorMap, callTypeLabel } from '@/utils/callLog'
import { Badge, FeatherIcon, TextInput, createResource } from 'frappe-ui'
import { computed, ref } from 'vue'

const props = defineProps({
  docname: { type: String, required: true },
})

const {
  activities,
  items: calls,
  leadEmails,
  sourceLabel,
} = useContactActivityFeed(props.docname, 'calls', 'creation')

// Call · Status · Source · By · Duration · When
const COLUMNS =
  'grid-cols-[minmax(0,1.5fr)_minmax(0,1.2fr)_minmax(0,2fr)_minmax(0,1.5fr)_minmax(0,1fr)_minmax(0,1.3fr)]'

const search = ref('')

const filteredCalls = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return calls.value
  return calls.value.filter((call) =>
    [
      callTypeLabel(call),
      statusLabelMap[call.status] || call.status || '',
      sourceLabel(call),
      call._caller?.label || '',
    ]
      .join(' ')
      .toLowerCase()
      .includes(q),
  )
})

const showCallDetail = ref(false)
const callLog = ref({})

function openCall(call) {
  callLog.value = createResource({
    url: 'crm.fcrm.doctype.crm_call_log.crm_call_log.get_call_log',
    params: { name: call.name },
    cache: ['call_log', call.name],
    auto: true,
  })
  showCallDetail.value = true
}
</script>

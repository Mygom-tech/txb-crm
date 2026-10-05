<template>
  <div
    v-if="rows.length"
    class="flex flex-col gap-1.5 border-b px-5 py-3 text-base"
    data-test="contact-inactivity"
  >
    <div class="text-sm font-medium text-ink-gray-5">
      {{ __('Inactivity') }}
    </div>
    <div
      v-for="row in rows"
      :key="row.label"
      class="flex items-center justify-between gap-2"
    >
      <span class="text-ink-gray-6">{{ row.label }}</span>
      <span class="truncate text-ink-gray-8">
        {{ row.date ? formatDate(row.value) : row.value }}
        <span v-if="row.hint" class="text-ink-gray-5">· {{ row.hint }}</span>
      </span>
    </div>
    <div
      v-if="inactivity.data.reminder_task"
      class="flex items-center justify-between gap-2"
    >
      <span class="text-ink-gray-6">{{ __('Reminder') }}</span>
      <router-link
        class="text-ink-gray-8 underline"
        :to="{ name: 'Tasks', query: { open: inactivity.data.reminder_task } }"
      >
        {{ __('Task {0}', [inactivity.data.reminder_task]) }}
      </router-link>
    </div>
  </div>
</template>

<script setup>
import { API, statusRows } from './contactInactivity'
import { formatDate } from '@/utils'
import { createResource } from 'frappe-ui'
import { computed } from 'vue'

const props = defineProps({
  contact: { type: String, required: true },
})

const inactivity = createResource({
  url: `${API}.get_contact_inactivity`,
  cache: ['contact_inactivity', props.contact],
  params: { contact: props.contact },
  auto: true,
})

const rows = computed(() => statusRows(inactivity.data))
</script>

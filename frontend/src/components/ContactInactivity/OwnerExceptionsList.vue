<template>
  <div class="flex flex-col gap-2 py-3 px-2" data-test="owner-exceptions">
    <div class="flex flex-col">
      <div class="text-p-base-medium text-ink-gray-7 truncate">
        {{ __('Contacts without an owner to remind') }}
      </div>
      <div class="text-p-sm text-ink-gray-5">
        {{
          __(
            'These Contacts are due for an inactivity reminder, but have no owner or a disabled owner. Assign an enabled owner to send the reminder.',
          )
        }}
      </div>
    </div>
    <div v-if="!exceptions.data?.length" class="text-p-sm text-ink-gray-5">
      {{ __('No owner exceptions') }}
    </div>
    <router-link
      v-for="row in exceptions.data || []"
      :key="row.cycle"
      :to="{ name: 'Contact', params: { contactId: row.contact } }"
      class="flex items-center justify-between gap-2 rounded px-2 py-1.5 text-base hover:bg-surface-gray-2"
    >
      <span class="truncate text-ink-gray-8">
        {{ row.contact_name || row.contact }}
      </span>
      <span class="shrink-0 text-ink-gray-6">
        {{ row.exception }}<template v-if="row.owner"> · {{ row.owner }}</template>
        · {{ formatDate(row.due_on) }}
      </span>
    </router-link>
  </div>
</template>

<script setup>
import { API } from './contactInactivity'
import { formatDate } from '@/utils'
import { createResource } from 'frappe-ui'

const exceptions = createResource({
  url: `${API}.list_owner_exceptions`,
  cache: 'contact_inactivity_owner_exceptions',
  auto: true,
})
</script>

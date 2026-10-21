<template>
  <!-- The archived Leads this person came from, so their Lead-only data stays one click away. -->
  <div
    v-if="leads.data?.length"
    class="flex flex-col gap-1.5 border-b px-5 py-3 text-base"
  >
    <div class="text-sm font-medium text-ink-gray-5">
      {{ __('Source Lead') }}
    </div>
    <router-link
      v-for="lead in leads.data"
      :key="lead.name"
      class="truncate text-ink-blue-3 hover:underline"
      :to="{ name: 'Lead', params: { leadId: lead.name } }"
    >
      {{ lead.lead_name || lead.name }}
    </router-link>
  </div>
</template>

<script setup>
import { createResource } from 'frappe-ui'

const props = defineProps({
  contact: { type: String, required: true },
})

const leads = createResource({
  url: 'crm.api.contact.get_source_leads',
  cache: ['sourceLeads', props.contact],
  params: { contact: props.contact },
  auto: true,
})
</script>

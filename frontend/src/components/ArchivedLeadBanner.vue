<template>
  <!--
    A converted Lead is archived: read-only provenance, not an editable record (TXB-132).
    Surfaces the archived state and links to the conversion result -- the Contact it created
    and the initial Opportunity it opened -- so the historical trail stays reachable while the
    page's mutation and reconversion controls are hidden by the host.
  -->
  <div
    class="flex flex-col gap-1.5 border-b bg-surface-gray-2 px-5 py-2.5 text-sm sm:flex-row sm:items-center sm:gap-4"
  >
    <div class="flex items-center gap-2 font-medium text-ink-gray-7">
      <FeatherIcon name="lock" class="h-4 w-4 text-ink-gray-6" />
      <span>{{ __('Archived — converted Lead, read-only') }}</span>
    </div>
    <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-ink-gray-6">
      <router-link
        v-if="lead.converted_contact"
        class="text-ink-blue-3 hover:underline"
        :to="{
          name: contactRouteName,
          params: { contactId: lead.converted_contact },
        }"
      >
        {{ __('View converted Contact') }}
      </router-link>
      <router-link
        v-if="lead.converted_deal"
        class="text-ink-blue-3 hover:underline"
        :to="{ name: dealRouteName, params: { dealId: lead.converted_deal } }"
      >
        {{ __('View initial Opportunity') }}
      </router-link>
      <span v-if="lead.converted_at" class="text-ink-gray-5">
        {{ __('Converted on {0}', [formatDate(lead.converted_at, '', true)]) }}
      </span>
    </div>
  </div>
</template>

<script setup>
import { formatDate } from '@/utils'
import { FeatherIcon } from 'frappe-ui'

defineProps({
  lead: { type: Object, required: true },
  // Route names differ between desktop and mobile shells, so the host passes the pair it uses.
  contactRouteName: { type: String, default: 'Contact' },
  dealRouteName: { type: String, default: 'Deal' },
})
</script>

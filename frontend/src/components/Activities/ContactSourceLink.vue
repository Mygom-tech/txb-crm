<template>
  <span v-if="source" class="inline-flex min-w-0 items-center gap-1.5">
    <component
      :is="source.kind === 'lead' ? LeadsIcon : DealsIcon"
      class="h-3.5 w-3.5 shrink-0 text-ink-gray-5"
    />
    <router-link
      :to="source.route"
      class="max-w-[16rem] truncate font-medium text-ink-gray-8 hover:text-ink-gray-9 hover:underline"
      :title="source.label"
    >
      {{ source.label }}
    </router-link>
  </span>
</template>

<script setup>
import LeadsIcon from '@/components/Icons/LeadsIcon.vue'
import DealsIcon from '@/components/Icons/DealsIcon.vue'
import { activitySource } from '@/utils/contactActivity'
import { computed } from 'vue'

const props = defineProps({
  activity: { type: Object, default: () => ({}) },
  leadEmails: { type: Object, default: () => ({}) },
})

// Single reusable source-link presentation contract: a source-doctype icon plus a link that
// ALWAYS navigates by the immutable Lead/Deal docname to the direct record route (never the
// filtered Leads list), while showing the email-preferred, docname-fallback label. Shared by
// the Contact activity-log badge and the forthcoming Contact Notes table so every Contact
// aggregate presentation reaches the same archived Lead / linked Opportunity. Renders nothing
// for untagged records (activitySource returns null).
const source = computed(() => activitySource(props.activity, props.leadEmails))
</script>

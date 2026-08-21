<template>
  <div v-if="source" class="flex flex-wrap items-center gap-1.5 text-sm">
    <!-- Reuse the shared source-link contract so the activity log and the forthcoming Contact
         Notes table link to the archived Lead / linked Opportunity identically. -->
    <ContactSourceLink :activity="activity" :leadEmails="leadEmails" />
    <Badge
      v-if="source.phaseLabel"
      :label="__(source.phaseLabel)"
      variant="subtle"
      size="sm"
      :theme="source.phase === PHASE_PRE_CONVERSION ? 'gray' : 'green'"
    />
  </div>
</template>

<script setup>
import ContactSourceLink from '@/components/Activities/ContactSourceLink.vue'
import {
  activitySource,
  PHASE_PRE_CONVERSION,
} from '@/utils/contactActivity'
import { Badge } from 'frappe-ui'
import { computed } from 'vue'

const props = defineProps({
  activity: { type: Object, default: () => ({}) },
  leadEmails: { type: Object, default: () => ({}) },
})

// Presentation contract lives in the pure helper; this component only renders it. The clickable
// source is delegated to ContactSourceLink; the badge adds the pre/post-conversion phase.
const source = computed(() => activitySource(props.activity, props.leadEmails))
</script>

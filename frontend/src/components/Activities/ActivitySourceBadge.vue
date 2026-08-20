<template>
  <div v-if="source" class="flex flex-wrap items-center gap-1.5 text-sm">
    <component
      :is="LeadsIcon"
      v-if="source.kind === 'lead'"
      class="h-3.5 w-3.5 text-ink-gray-5"
    />
    <component :is="DealsIcon" v-else class="h-3.5 w-3.5 text-ink-gray-5" />
    <router-link
      :to="source.route"
      class="max-w-[16rem] truncate font-medium text-ink-gray-8 hover:text-ink-gray-9 hover:underline"
      :title="source.label"
    >
      {{ source.label }}
    </router-link>
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
import LeadsIcon from '@/components/Icons/LeadsIcon.vue'
import DealsIcon from '@/components/Icons/DealsIcon.vue'
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

// Presentation contract lives in the pure helper; this component only renders it.
const source = computed(() => activitySource(props.activity, props.leadEmails))
</script>

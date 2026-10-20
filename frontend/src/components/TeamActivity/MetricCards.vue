<template>
  <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
    <div
      v-for="(metric, index) in loading ? skeletons : metrics"
      :key="metric?.key || index"
      class="flex flex-col gap-1 rounded-md px-6 py-5"
      :class="
        metric?.ignores_period
          ? 'border border-dashed border-outline-gray-3 bg-surface-gray-1'
          : 'bg-surface-base shadow'
      "
      :data-metric="metric?.key"
    >
      <template v-if="loading">
        <span v-if="metric" class="text-base text-ink-gray-6">
          {{ metric.label }}
        </span>
        <Skeleton v-else class="h-4 w-28 rounded" />
        <Skeleton class="mt-1 h-7 w-16 rounded-md" />
        <Skeleton class="mt-2 h-3 w-48 rounded" />
        <Skeleton class="h-3 w-20 rounded" />
      </template>
      <template v-else>
        <span class="flex items-center gap-2 text-base text-ink-gray-6">
          {{ metric.label }}
          <Badge
            v-if="metric.ignores_period"
            theme="gray"
            variant="subtle"
            size="sm"
            :label="__('All upcoming')"
          />
        </span>
        <span class="text-3xl font-semibold text-ink-gray-9">
          {{ metric.value }}
        </span>
        <span class="text-sm text-ink-gray-5">{{ metric.description }}</span>
        <span v-if="metric.groups?.length" class="text-sm text-ink-gray-7">
          {{ metric.groups.map((g) => `${g.label} ${g.value}`).join(' · ') }}
        </span>
        <div>
          <Button
            class="-ml-2"
            variant="ghost"
            :label="__('View records')"
            :iconRight="LucideArrowRight"
            @click="emit('view', metric)"
          />
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import LucideArrowRight from '~icons/lucide/arrow-right'
import { Badge, Button, Skeleton } from 'frappe-ui'
import { computed } from 'vue'

const props = defineProps({
  metrics: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['view'])

// A refresh keeps the known card titles; the first load has none yet, so show four blanks.
const skeletons = computed(() =>
  props.metrics.length ? props.metrics : [null, null, null, null],
)
</script>

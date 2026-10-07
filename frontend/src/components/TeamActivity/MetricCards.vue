<template>
  <div class="grid grid-cols-1 gap-4 md:grid-cols-3">
    <div
      v-for="(metric, index) in loading ? skeletons : metrics"
      :key="metric?.key || index"
      class="flex flex-col gap-1 rounded-md bg-surface-base px-6 py-5 shadow"
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
        <span class="text-base text-ink-gray-6">{{ metric.label }}</span>
        <span class="text-3xl font-semibold text-ink-gray-9">
          {{ metric.value }}
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
import { Button, Skeleton } from 'frappe-ui'
import { computed } from 'vue'

const props = defineProps({
  metrics: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['view'])

// A refresh keeps the known card titles; the first load has none yet, so show three blanks.
const skeletons = computed(() =>
  props.metrics.length ? props.metrics : [null, null, null],
)
</script>

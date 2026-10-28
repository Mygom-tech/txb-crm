<template>
  <section
    class="flex flex-col overflow-hidden rounded-lg border border-outline-gray-2 bg-surface-white"
    :style="{ height }"
  >
    <header
      class="flex items-center gap-2 border-b border-outline-gray-1 py-2 pl-3.5 pr-2"
    >
      <component :is="icon" class="size-4 text-ink-gray-6" aria-hidden="true" />
      <h2 class="text-base font-semibold text-ink-gray-9">{{ title }}</h2>
      <span
        v-if="badge"
        class="rounded-full bg-surface-gray-2 px-2 text-sm text-ink-gray-6"
      >
        {{ badge }}
      </span>
      <slot name="header-extra" />
      <Button
        class="ml-auto"
        variant="ghost"
        :aria-label="__('Expand {0}', [title])"
        @click="emit('expand')"
      >
        <template #icon>
          <MaximizeIcon class="size-4 text-ink-gray-6" />
        </template>
      </Button>
    </header>
    <div class="min-h-0 flex-1 overflow-y-auto">
      <div v-if="loading" class="p-4 text-base text-ink-gray-5">
        {{ __('Loading...') }}
      </div>
      <slot v-else />
    </div>
  </section>
</template>

<script setup>
import MaximizeIcon from '@/components/Icons/MaximizeIcon.vue'

defineProps({
  title: { type: String, required: true },
  icon: { type: [Object, Function], required: true },
  badge: { type: String, default: '' },
  height: { type: String, default: 'auto' },
  loading: { type: Boolean, default: false },
})
const emit = defineEmits(['expand'])
</script>

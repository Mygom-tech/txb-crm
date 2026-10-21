<template>
  <div class="flex flex-wrap items-center gap-4">
    <TabButtons
      :buttons="[
        { label: __('Whole team'), value: 'team' },
        { label: __('Person'), value: 'person' },
      ]"
      :modelValue="filters.mode"
      @update:modelValue="(mode) => emit('update', { mode })"
    />
    <Dropdown
      :options="presets"
      :button="{
        label: __(filters.preset),
        class: 'w-48 justify-start [&>span]:mr-auto [&>svg]:text-ink-gray-5',
        variant: 'outline',
        iconRight: 'chevron-down',
        iconLeft: 'calendar',
      }"
    />
    <template v-if="filters.preset === 'Custom Range'">
      <DatePicker
        class="w-36"
        variant="outline"
        :value="filters.from"
        :placeholder="__('From')"
        :clearable="false"
        @change="(from) => emit('update', { from })"
      />
      <DatePicker
        class="w-36"
        variant="outline"
        :value="filters.to"
        :placeholder="__('To')"
        :clearable="false"
        @change="(to) => emit('update', { to })"
      />
    </template>
    <span v-else class="text-base text-ink-gray-8">
      {{ formatDay(filters.from) }} – {{ formatDay(filters.to) }}
    </span>
    <Select
      class="w-56"
      variant="outline"
      :options="members"
      :modelValue="filters.member || undefined"
      :disabled="filters.mode !== 'person'"
      :placeholder="
        filters.mode === 'person'
          ? __('Team member')
          : __('Team member · Person only')
      "
      @update:modelValue="(member) => emit('update', { member })"
    >
      <template #prefix>
        <UserAvatar
          v-if="filters.member"
          class="mr-2"
          :user="filters.member"
          size="sm"
        />
        <LucideUser v-else class="mr-2 size-4 text-ink-gray-5" />
      </template>
    </Select>
    <span class="ml-auto flex items-center gap-1.5 text-sm text-ink-gray-5">
      <LucideGlobe class="size-4" />
      {{ __('Site timezone: {0}', [timezone]) }}
    </span>
  </div>
</template>

<script setup>
import LucideGlobe from '~icons/lucide/globe'
import LucideUser from '~icons/lucide/user'
import UserAvatar from '@/components/UserAvatar.vue'
import { PERIOD_PRESETS } from '@/utils/teamActivity'
import { dayjs, DatePicker, Dropdown, Select, TabButtons } from 'frappe-ui'

defineProps({
  filters: { type: Object, required: true },
  members: { type: Array, default: () => [] },
  timezone: { type: String, default: '' },
})

const emit = defineEmits(['update'])

// The label stays the English key, like Dashboard's `preset`; the page resolves its range.
const presets = [
  {
    group: 'Presets',
    hideLabel: true,
    items: Object.keys(PERIOD_PRESETS).map((preset) => ({
      label: __(preset),
      onClick: () => emit('update', { preset }),
    })),
  },
  {
    label: __('Custom Range'),
    onClick: () => emit('update', { preset: 'Custom Range' }),
  },
]

function formatDay(date) {
  return date ? dayjs(date).format('D MMM YYYY') : ''
}
</script>

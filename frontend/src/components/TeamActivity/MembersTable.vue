<template>
  <section class="flex flex-col gap-2">
    <div class="flex items-baseline justify-between gap-3">
      <span class="text-base font-medium text-ink-gray-9">
        {{ __('By team member') }}
      </span>
      <span class="text-sm text-ink-gray-5">
        {{ __('Select a member to see their activity only') }}
      </span>
    </div>
    <div
      class="overflow-x-auto rounded-md border border-outline-gray-2"
      data-testid="members-table"
    >
      <table class="w-full min-w-[640px] text-base">
        <thead>
          <tr class="bg-surface-gray-1 text-right text-sm text-ink-gray-6">
            <th scope="col" class="px-4 py-2 text-left font-medium">
              {{ __('Member') }}
            </th>
            <th
              v-for="metric in metrics"
              :key="metric.key"
              scope="col"
              class="px-4 py-2 font-medium"
            >
              {{ metric.label }}
            </th>
          </tr>
        </thead>
        <tbody>
          <template v-if="loading && !members.length">
            <tr v-for="i in 3" :key="i" class="border-t border-outline-gray-1">
              <td :colspan="metrics.length + 1" class="px-4 py-2">
                <Skeleton class="h-5 w-full rounded" />
              </td>
            </tr>
          </template>
          <tr
            v-for="member in members"
            :key="member.user"
            class="border-t border-outline-gray-1 text-right tabular-nums"
          >
            <td class="px-4 py-2 text-left">
              <button
                type="button"
                class="flex items-center gap-2 text-ink-gray-9 hover:underline"
                @click="emit('select', member.user)"
              >
                <UserAvatar :user="member.user" size="sm" />
                {{ member.full_name || member.user }}
              </button>
            </td>
            <td
              v-for="metric in metrics"
              :key="metric.key"
              class="px-4 py-2 text-ink-gray-8"
            >
              {{ member.values[metric.key] }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup>
import UserAvatar from '@/components/UserAvatar.vue'
import { Skeleton } from 'frappe-ui'
import { computed } from 'vue'

const props = defineProps({
  // members() response: `{ metrics, members: [{ user, full_name, values }] }`.
  data: { type: Object, default: null },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['select'])

const metrics = computed(() => props.data?.metrics || [])
// Members with no activity at all add rows of zeros, so they are left out.
const members = computed(() =>
  (props.data?.members || []).filter((member) =>
    Object.values(member.values).some(Boolean),
  ),
)
</script>

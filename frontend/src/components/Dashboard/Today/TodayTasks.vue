<template>
  <WidgetCard
    :title="__(`Today's tasks`)"
    :icon="TaskIcon"
    :badge="openCount ? __('{0} open', [openCount]) : ''"
    :height="height"
    :loading="tasks.loading && !tasks.data"
    @expand="show = true"
  >
    <p v-if="!groups.length" class="p-4 text-base text-ink-gray-5">
      {{ __('Nothing due today.') }}
    </p>
    <div v-for="group in groups" :key="group.key" class="pb-1">
      <div
        class="px-3.5 pb-0.5 pt-2.5 text-xs font-semibold uppercase tracking-wide"
        :class="GROUP_CLASS[group.key]"
      >
        {{ __(group.label) }} · {{ group.count }}
      </div>
      <button
        v-for="task in group.items"
        :key="task.name"
        type="button"
        class="flex w-full items-center gap-2.5 px-3.5 py-1.5 text-left hover:bg-surface-gray-1"
        @click="openTask(task)"
      >
        <TaskStatusIcon :status="task.status" class="shrink-0" />
        <div class="min-w-0 flex-1">
          <div
            class="truncate text-base"
            :class="
              task.group == 'done'
                ? 'text-ink-gray-5 line-through'
                : 'text-ink-gray-9'
            "
          >
            {{ task.title }}
          </div>
          <div class="truncate text-sm text-ink-gray-5">
            {{ recordLabel(task) }}
          </div>
        </div>
        <span
          class="shrink-0 text-sm"
          :class="task.group == 'overdue' ? 'text-red-700' : 'text-ink-gray-5'"
        >
          {{ dueLabel(task) }}
        </span>
        <UserAvatar
          v-if="task.assigned_to"
          :user="task.assigned_to"
          size="sm"
          class="shrink-0"
        />
      </button>
    </div>
  </WidgetCard>

  <Dialog v-model:open="show" size="4xl">
    <template #body>
      <div class="px-5 pb-5 pt-4">
        <div class="mb-3 flex items-center gap-2">
          <h3 class="text-xl font-semibold text-ink-gray-9">
            {{ __(`Today's tasks`) }}
          </h3>
          <span class="text-base text-ink-gray-5">{{ todayLabel }}</span>
          <Button class="ml-auto" variant="ghost" icon="lucide-x" :aria-label="__('Close')" @click="show = false" />
        </div>
        <div class="mb-2 flex flex-wrap gap-1 border-b border-outline-gray-1 pb-2">
          <Button
            v-for="tab in tabs"
            :key="tab.key"
            :variant="filter == tab.key ? 'subtle' : 'ghost'"
            @click="filter = tab.key"
          >
            {{ __(tab.label) }}
            <span class="ml-1 text-ink-gray-5">{{ tab.count }}</span>
          </Button>
        </div>
        <div class="max-h-[60vh] overflow-auto">
          <table class="w-full text-base">
            <thead>
              <tr class="text-left text-ink-gray-6">
                <th class="w-8 py-2 font-medium">
                  <span class="sr-only">{{ __('Status') }}</span>
                </th>
                <th class="py-2 pr-3 font-medium">{{ __('Task') }}</th>
                <th class="py-2 pr-3 font-medium">{{ __('Record') }}</th>
                <th class="py-2 pr-3 font-medium">{{ __('Assigned to') }}</th>
                <th class="py-2 pr-3 font-medium">{{ __('Priority') }}</th>
                <th class="py-2 font-medium">{{ __('Due') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="task in filtered"
                :key="task.name"
                class="cursor-pointer border-t border-outline-gray-1 hover:bg-surface-gray-1"
                @click="openTask(task)"
              >
                <td class="py-2"><TaskStatusIcon :status="task.status" /></td>
                <td
                  class="py-2 pr-3"
                  :class="
                    task.group == 'done'
                      ? 'text-ink-gray-5 line-through'
                      : 'text-ink-gray-9'
                  "
                >
                  {{ task.title }}
                </td>
                <td class="py-2 pr-3">
                  <router-link
                    v-if="reference(task).route"
                    :to="reference(task).route"
                    class="text-ink-gray-7 hover:underline"
                    @click.stop="show = false"
                  >
                    {{ recordLabel(task) }}
                  </router-link>
                </td>
                <td class="py-2 pr-3">
                  <div v-if="task.assigned_to" class="flex items-center gap-1.5">
                    <UserAvatar :user="task.assigned_to" size="xs" />
                    <span class="text-ink-gray-7">
                      {{ getUser(task.assigned_to).full_name }}
                    </span>
                  </div>
                </td>
                <td class="py-2 pr-3">
                  <div class="flex items-center gap-1.5 text-ink-gray-7">
                    <TaskPriorityIcon :priority="task.priority" />
                    {{ task.priority }}
                  </div>
                </td>
                <td
                  class="whitespace-nowrap py-2"
                  :class="
                    task.group == 'overdue' ? 'text-red-700' : 'text-ink-gray-7'
                  "
                >
                  {{ dueLabel(task, true) }}
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="!filtered.length" class="py-6 text-center text-base text-ink-gray-5">
            {{ __('No tasks here.') }}
          </p>
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import WidgetCard from './WidgetCard.vue'
import TaskIcon from '@/components/Icons/TaskIcon.vue'
import TaskStatusIcon from '@/components/Icons/TaskStatusIcon.vue'
import TaskPriorityIcon from '@/components/Icons/TaskPriorityIcon.vue'
import UserAvatar from '@/components/UserAvatar.vue'
import { taskReference } from '@/components/ContactInactivity/taskReference'
import { useDoctypeModal } from '@/composables/doctypeModal'
import { usersStore } from '@/stores/users'
import { groupTasks, TASK_GROUPS } from '@/utils/todayWidgets'
import { formatDate } from '@/utils'
import { createResource, Dialog } from 'frappe-ui'
import { computed, ref, watch } from 'vue'

const props = defineProps({
  user: { type: String, default: null },
  height: { type: String, default: 'auto' },
})

const GROUP_CLASS = {
  overdue: 'text-red-700',
  today: 'text-ink-gray-6',
  done: 'text-green-700',
}

const { getUser } = usersStore()
const { showModal } = useDoctypeModal()

const show = ref(false)
const filter = ref('all')

const tasks = createResource({
  url: 'crm.api.dashboard.get_today_tasks',
  makeParams: () => ({ user: props.user }),
  auto: true,
})
watch(() => props.user, () => tasks.reload())

const rows = computed(() => tasks.data || [])
const groups = computed(() => groupTasks(rows.value))
const openCount = computed(() => rows.value.filter((t) => t.group != 'done').length)
const tabs = computed(() => [
  { key: 'all', label: 'All', count: rows.value.length },
  ...TASK_GROUPS.map((g) => ({
    ...g,
    label: g.key == 'done' ? 'Completed' : g.label,
    count: rows.value.filter((t) => t.group == g.key).length,
  })),
])
const filtered = computed(() =>
  filter.value == 'all' ? rows.value : rows.value.filter((t) => t.group == filter.value),
)

const today = formatDate(new Date(), 'YYYY-MM-DD')
const todayLabel = formatDate(new Date(), 'ddd, MMM D')

function reference(task) {
  return taskReference(task.reference_doctype, task.reference_docname)
}

function recordLabel(task) {
  const { label } = reference(task)
  if (!label) return ''
  return `${label} · ${task.reference_title || task.reference_docname}`
}

function dueLabel(task, long = false) {
  if (task.group == 'done') return __('Done')
  if (!task.due_date) return long ? '' : __('Today')
  if (task.due_date.slice(0, 10) == today) return formatDate(task.due_date, 'HH:mm')
  return formatDate(task.due_date, long ? 'ddd, MMM D HH:mm' : 'MMM D')
}

function openTask(task) {
  show.value = false
  showModal({
    name: task.name,
    doctype: 'CRM Task',
    title: 'Task',
    callbacks: { afterUpdate: () => tasks.reload() },
  })
}
</script>

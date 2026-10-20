<template>
  <Dialog v-model:open="show" :title="metric?.label" size="4xl">
    <div class="flex flex-col gap-3">
      <div class="flex flex-col gap-0.5 text-sm text-ink-gray-5">
        <span>{{ scope }}</span>
        <span v-if="records.data" class="text-ink-gray-8">
          {{ __('{0} records', [records.data.total]) }}
        </span>
      </div>

      <div v-if="metric?.groups?.length" class="flex flex-col gap-2">
        <TabButtons
          :buttons="groupTabs"
          :modelValue="group"
          @update:modelValue="selectGroup"
        />
        <span class="text-sm text-ink-gray-6">{{ explainer }}</span>
      </div>
      <span v-else-if="metric?.description" class="text-sm text-ink-gray-6">
        {{ metric.description }}
      </span>

      <div v-if="records.error" class="flex flex-col items-start gap-2 py-6">
        <span class="text-base font-medium text-ink-gray-9">
          {{ __("Couldn't load the records") }}
        </span>
        <span class="text-sm text-ink-gray-6">
          {{ __('The total on the card is unchanged. Try again.') }}
        </span>
        <ErrorMessage :message="records.error" />
        <Button
          variant="outline"
          :label="__('Retry')"
          :loading="records.loading"
          @click="records.reload()"
        />
      </div>
      <div
        v-else-if="records.data && !records.data.total"
        class="flex flex-col items-center gap-1 py-10 text-center"
      >
        <span class="text-base font-medium text-ink-gray-9">
          {{ __('No records in this period') }}
        </span>
        <span class="text-sm text-ink-gray-6">
          {{ __('Nothing matched for {0}, so the card shows 0.', [period]) }}
        </span>
      </div>
      <div v-else-if="!records.data" class="flex flex-col gap-2 py-4">
        <Skeleton v-for="i in 5" :key="i" class="h-6 w-full rounded" />
      </div>
      <div v-else class="max-h-[60vh] overflow-y-auto">
        <ListView
          :columns="columns"
          :rows="rows"
          row-key="_key"
          :options="{
            selectable: false,
            showTooltip: false,
            resizeColumn: false,
          }"
        >
          <template #cell="{ item, column, row }">
            <router-link
              v-if="column.type === 'person' && row._reference.route"
              :to="row._reference.route"
              class="flex flex-col truncate hover:underline"
              @click.stop
            >
              <span class="truncate text-base text-ink-gray-9">{{ item }}</span>
              <span class="text-xs text-ink-gray-5">
                {{ row._reference.label }}
              </span>
            </router-link>
            <Badge
              v-else-if="column.type === 'badge' && item"
              theme="gray"
              variant="subtle"
              :label="item"
            />
            <span
              v-else-if="column.type === 'link' && item && isCrmUser(item)"
              class="flex items-center gap-2 truncate text-base"
            >
              <UserAvatar :user="item" size="sm" />
              {{ getUser(item).full_name }}
            </span>
            <span
              v-else
              class="truncate text-base"
              :title="cellText(column, item)"
            >
              {{ cellText(column, item) }}
            </span>
          </template>
        </ListView>
      </div>

      <div
        v-if="records.data?.total"
        class="flex items-center justify-between text-sm text-ink-gray-6"
      >
        <span>
          {{
            __('{0}–{1} of {2}', [
              pageInfo.from,
              pageInfo.to,
              records.data.total,
            ])
          }}
        </span>
        <div class="flex gap-2">
          <Button
            variant="outline"
            :label="__('Previous')"
            :disabled="page === 1 || records.loading"
            @click="load(page - 1)"
          />
          <Button
            variant="outline"
            :label="__('Next')"
            :disabled="!pageInfo.hasNext || records.loading"
            @click="load(page + 1)"
          />
        </div>
      </div>
    </div>
  </Dialog>
</template>

<script setup>
import UserAvatar from '@/components/UserAvatar.vue'
import { usersStore } from '@/stores/users'
import { formatDate } from '@/utils'
import { pageWindow, recordRows, recordsParams } from '@/utils/teamActivity'
import {
  Badge,
  Button,
  createResource,
  Dialog,
  ErrorMessage,
  ListView,
  Skeleton,
  TabButtons,
} from 'frappe-ui'
import { computed, ref, watch } from 'vue'

const props = defineProps({
  metric: { type: Object, default: null },
  filters: { type: Object, required: true },
  scope: { type: String, default: '' },
  period: { type: String, default: '' },
})

const show = defineModel({ type: Boolean, default: false })

const { getUser, isCrmUser } = usersStore()

const page = ref(1)
const group = ref('')

// "All" plus one tab per group, each with its summary count.
const groupTabs = computed(() => [
  { label: __('All · {0}', [props.metric?.value ?? 0]), value: '' },
  ...(props.metric?.groups || []).map((g) => ({
    label: `${g.label} · ${g.value}`,
    value: g.key,
  })),
])
const explainer = computed(
  () =>
    props.metric?.groups?.find((g) => g.key === group.value)?.description ||
    props.metric?.description,
)

const records = createResource({
  url: 'crm.txb.api.team_activity.records',
})

const columns = computed(() => props.metric?.record_columns || [])
const rows = computed(() =>
  recordRows(records.data?.rows, records.data?.page || page.value),
)
const pageInfo = computed(() =>
  pageWindow(
    records.data?.page || page.value,
    records.data?.page_length || 20,
    records.data?.total || 0,
  ),
)

function load(next) {
  page.value = next
  records.submit(
    recordsParams(props.metric.key, props.filters, next, group.value),
  )
}

function selectGroup(value) {
  group.value = value
  load(1)
}

watch(show, (open) => {
  if (!open) return
  group.value = ''
  records.reset()
  load(1)
})

function cellText(column, value) {
  if (value === null || value === undefined) return ''
  if (column.type === 'datetime') return formatDate(value)
  return String(value)
}
</script>

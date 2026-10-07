<template>
  <Dialog v-model:open="show" :title="metric?.label" size="4xl">
    <div class="flex flex-col gap-3">
      <div class="flex flex-col gap-0.5 text-sm text-ink-gray-5">
        <span>{{ scope }}</span>
        <span v-if="records.data" class="text-ink-gray-8">
          {{ __('{0} records', [records.data.total]) }}
        </span>
      </div>

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
            <template v-if="column.type === 'repeat'">
              <Badge
                v-if="repeatCount(row)"
                theme="gray"
                variant="subtle"
                size="sm"
                :label="__('{0} activities', [repeatCount(row)])"
              />
            </template>
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
import {
  pageWindow,
  recordRows,
  recordsColumns,
  recordsParams,
  repeatCount,
} from '@/utils/teamActivity'
import {
  Badge,
  Button,
  createResource,
  Dialog,
  ErrorMessage,
  ListView,
  Skeleton,
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

const records = createResource({
  url: 'crm.txb.api.team_activity.records',
})

const columns = computed(() => recordsColumns(props.metric?.record_columns))
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
  records.submit(recordsParams(props.metric.key, props.filters, next))
}

watch(show, (open) => {
  if (!open) return
  records.reset()
  load(1)
})

function cellText(column, value) {
  if (value === null || value === undefined) return ''
  if (column.type === 'datetime') return formatDate(value)
  return String(value)
}
</script>

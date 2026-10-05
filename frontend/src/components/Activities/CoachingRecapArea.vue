<template>
  <div class="flex flex-col gap-2 py-1.5">
    <div class="flex items-center justify-stretch gap-2 text-base">
      <div class="inline-flex items-center flex-wrap gap-1.5 text-ink-gray-5">
        <span class="font-medium text-ink-gray-8">{{ actorName }}</span>
        <span v-if="optedOut" data-test="opt-out">
          {{ __('opted out of the coaching call recap') }}
        </span>
        <template v-else>
          <span>{{ __('coaching call recap to') }}</span>
          <span class="font-medium text-ink-gray-8" data-test="recipient">
            {{ activity.recipient_email }}
          </span>
        </template>
        <Badge
          data-test="status"
          :label="__(status.label)"
          :theme="status.theme"
          variant="subtle"
          size="sm"
        />
        <span
          v-if="activity.revision_of"
          class="text-sm text-ink-gray-5"
          data-test="revision-of"
        >
          {{ __('Revised copy of {0}', [activity.revision_of]) }}
        </span>
      </div>
      <div class="ml-auto whitespace-nowrap">
        <TimelineTimestamp :date="timestamp" />
      </div>
    </div>
    <div
      v-if="activity.status == 'failed' && activity.last_error"
      class="text-sm text-ink-red-4"
    >
      {{ activity.last_error }}
    </div>
    <div v-if="!optedOut && (activity.can_retry || activity.can_send_revised)">
      <Button
        v-if="activity.can_retry"
        data-test="retry"
        :label="__('Retry')"
        :loading="pending"
        :disabled="pending"
        @click="retry"
      />
      <Button
        v-if="activity.can_send_revised"
        data-test="send-revised"
        :label="__('Send revised copy')"
        :disabled="pending"
        @click="showConfirm = true"
      />
    </div>
    <Dialog
      v-model:open="showConfirm"
      :title="__('Send revised copy')"
      @close="showConfirm = false"
    >
      <template #default>
        <p class="text-p-base text-ink-gray-7">
          {{
            __('Send the client another copy of this recap to {0}?', [
              activity.recipient_email,
            ])
          }}
        </p>
      </template>
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button
            data-test="cancel-revised"
            :label="__('Cancel')"
            @click="showConfirm = false"
          />
          <Button
            data-test="confirm-revised"
            variant="solid"
            :label="__('Confirm')"
            :loading="pending"
            :disabled="pending"
            @click="sendRevised"
          />
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import TimelineTimestamp from '@/components/Activities/TimelineTimestamp.vue'
import { Badge, Button, Dialog, call, toast } from 'frappe-ui'
import { computed, ref } from 'vue'

const props = defineProps({
  activity: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['reload'])

// TXB-276: one Activity Log row per Coaching Call recap ledger entry; a revised copy is its own
// row pointing at the original through `revision_of`, so the original keeps its own status.
const STATUSES = {
  queued: { label: 'Queued', theme: 'orange' },
  sent: { label: 'Sent', theme: 'green' },
  failed: { label: 'Failed', theme: 'red' },
  opted_out: { label: 'Opted out', theme: 'gray' },
}

const API = 'crm.txb.api.coaching_call_recap'

const status = computed(
  () =>
    STATUSES[props.activity.status] || {
      label: props.activity.status,
      theme: 'gray',
    },
)
const optedOut = computed(() => props.activity.status == 'opted_out')
const actorName = computed(
  () =>
    props.activity.owner_name || props.activity.actor || props.activity.owner,
)
const timestamp = computed(
  () =>
    props.activity.timestamp ||
    props.activity.sent_at ||
    props.activity.occurred_at ||
    props.activity.creation,
)

const pending = ref(false)
const showConfirm = ref(false)

async function run(method, params) {
  if (pending.value) return
  pending.value = true
  try {
    await call(`${API}.${method}`, params)
    showConfirm.value = false
    emit('reload')
  } catch (err) {
    toast.error(err?.messages?.[0] || __('Could not update the recap'))
  } finally {
    pending.value = false
  }
}

const retry = () => run('retry_recap', { recap: props.activity.recap })
const sendRevised = () =>
  run('send_revised_copy', { recap: props.activity.recap, confirm: 1 })
</script>

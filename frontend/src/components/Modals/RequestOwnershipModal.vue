<template>
  <Dialog v-model="show" :options="{ title: __('Request Ownership') }">
    <template #body-content>
      <div class="flex flex-col gap-4">
        <p class="text-p-base text-ink-gray-6">
          {{
            __(
              'The owner will not change now. An Admin reviews your request and decides.',
            )
          }}
        </p>

        <div class="flex flex-col gap-1.5">
          <div class="text-sm text-ink-gray-5">{{ __('Current owner') }}</div>
          <div class="text-base text-ink-gray-9">
            {{ currentOwnerLabel }}
          </div>
        </div>

        <div class="flex flex-col gap-1.5">
          <div class="text-sm text-ink-gray-5">{{ __('Requested owner') }}</div>
          <Link
            class="form-control"
            size="md"
            :value="requestedOwner"
            doctype="User"
            @change="(value) => (requestedOwner = value)"
          />
        </div>

        <div class="flex flex-col gap-1.5">
          <div class="text-sm text-ink-gray-5">
            {{ __('Why are you claiming this?') }}
            <span class="text-ink-red-2">*</span>
          </div>
          <FormControl
            v-model="reason"
            type="textarea"
            :rows="3"
            :placeholder="
              __('e.g. I ran the discovery call and own the relationship')
            "
          />
        </div>

        <ErrorMessage :message="error" />
      </div>
    </template>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button :label="__('Cancel')" @click="show = false" />
        <Button
          variant="solid"
          :label="__('Send request')"
          :loading="submitting"
          @click="submit"
        />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import Link from '@/components/Controls/Link.vue'
import { usersStore } from '@/stores/users'
import { sessionStore } from '@/stores/session'
import { Dialog, FormControl, ErrorMessage, call, toast } from 'frappe-ui'
import { ref, computed, watch } from 'vue'

const props = defineProps({
  doctype: { type: String, required: true },
  docname: { type: String, required: true },
  currentOwner: { type: String, default: '' },
})

const show = defineModel({ type: Boolean })

const { getUser } = usersStore()
const { user } = sessionStore()

const requestedOwner = ref(user)
const reason = ref('')
const error = ref('')
const submitting = ref(false)

const currentOwnerLabel = computed(() =>
  props.currentOwner
    ? getUser(props.currentOwner).full_name || props.currentOwner
    : __('Unassigned'),
)

// Reopening the modal after a send must not show the previous request.
watch(show, (open) => {
  if (!open) return
  requestedOwner.value = user
  reason.value = ''
  error.value = ''
})

async function submit() {
  error.value = ''

  if (!reason.value.trim()) {
    error.value = __('Say why you are claiming this record.')
    return
  }

  submitting.value = true
  try {
    const result = await call('crm.txb.api.ownership.request_claim', {
      doctype: props.doctype,
      name: props.docname,
      requested_owner: requestedOwner.value,
      reason: reason.value,
    })
    toast.success(result.message)
    show.value = false
  } catch (err) {
    error.value = err.messages?.[0] || __('Could not send the request.')
  } finally {
    submitting.value = false
  }
}
</script>

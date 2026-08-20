<template>
  <Dialog v-model="show" :options="{ title: __('Create Opportunity') }">
    <template #body-content>
      <div class="flex flex-col gap-4">
        <div class="flex flex-col gap-1.5">
          <div class="text-sm text-ink-gray-5">{{ __('Contact') }}</div>
          <div class="text-base text-ink-gray-9">
            {{ contact.full_name || contact.name }}
          </div>
        </div>

        <div class="flex flex-col gap-1.5">
          <div class="text-sm text-ink-gray-5">{{ __('Organization') }}</div>
          <Link
            class="form-control"
            size="md"
            :value="organization"
            doctype="CRM Organization"
            @change="(value) => (organization = value)"
          />
        </div>

        <!--
          Optional provenance (TXB-132): this later Opportunity may retain one archived Lead
          associated with this Contact, carried through the existing CRM Deal.lead link. The
          filter enumerates only converted Leads whose recorded conversion Contact is this
          Contact, and the server re-validates the choice. Leaving it blank is fully supported.
        -->
        <div class="flex flex-col gap-1.5">
          <div class="text-sm text-ink-gray-5">{{ __('Source Lead') }}</div>
          <Link
            class="form-control"
            size="md"
            :value="sourceLead"
            doctype="CRM Lead"
            :filters="{ converted: 1, converted_contact: contact.name }"
            :placeholder="__('Optional archived Lead...')"
            @change="(value) => (sourceLead = value)"
          />
        </div>

        <div class="flex flex-col gap-1.5">
          <div class="text-sm text-ink-gray-5">
            {{ __('Pipeline Type') }}
            <span class="text-ink-red-2">*</span>
          </div>
          <Select
            v-model="pipelineType"
            class="form-control"
            :options="pipelineTypeOptions"
            :placeholder="__('Select Pipeline Type...')"
          />
        </div>

        <div v-if="pipelineType" class="flex flex-col gap-1.5">
          <div class="text-sm text-ink-gray-5">
            {{ __('Status') }}
            <span class="text-ink-red-2">*</span>
          </div>
          <Select
            v-model="status"
            class="form-control"
            :options="statusOptions"
            :placeholder="__('Select Status...')"
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
          :label="__('Create')"
          :loading="creating"
          @click="create"
        />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import Link from '@/components/Controls/Link.vue'
import { statusesStore } from '@/stores/statuses'
import { getMeta } from '@/stores/meta'
import { allowedStatusesFor } from '@/utils/pipelineStatuses'
import { selectFieldOptions } from '@/utils/selectOptions'
import { Dialog, Select, ErrorMessage, call, toast } from 'frappe-ui'
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  contact: { type: Object, required: true },
})

const show = defineModel({ type: Boolean })

const router = useRouter()
const { pipelineStatuses } = statusesStore()
const { doctypeMeta: dealMeta } = getMeta('CRM Deal')

const organization = ref(props.contact.custom_organization_link || '')
const sourceLead = ref('')
const pipelineType = ref('')
const status = ref('')
const error = ref('')
const creating = ref(false)

const pipelineTypeOptions = computed(() => {
  const field = dealMeta.value?.fields?.find(
    (f) => f.fieldname === 'pipeline_type',
  )
  return selectFieldOptions(field).map(({ value }) => ({
    label: __(value),
    value,
  }))
})

// Same server-owned map the deal page and the convert modal read, rather than the
// private copy the form script carried.
const statusOptions = computed(() =>
  allowedStatusesFor(pipelineType.value, null, pipelineStatuses.data).map(
    (value) => ({ label: __(value), value }),
  ),
)

// The pipeline's first status is the one this pipeline starts in, and pre-selecting it is
// what the script approximated with a hardcoded map.
watch(pipelineType, () => {
  status.value = statusOptions.value[0]?.value || ''
})

async function create() {
  error.value = ''

  if (!pipelineType.value) {
    error.value = __('Please select a pipeline type')
    return
  }

  if (!status.value) {
    error.value = __('Please select a status')
    return
  }

  creating.value = true
  try {
    // One insert, carrying the final status. The script this replaces inserted first and
    // then PUT the corrected status, which TXB-110's transition guard now refuses for
    // non-Admins -- inserts are exempt, later status writes are not.
    const dealName = await call(
      'crm.fcrm.doctype.crm_deal.crm_deal.create_deal',
      {
        doc: {
          contact: props.contact.name,
          organization: organization.value || undefined,
          pipeline_type: pipelineType.value,
          status: status.value,
          // Optional archived-Lead provenance, persisted via CRM Deal.lead and re-validated
          // server-side; omitted when unset so a plain later Opportunity is created.
          lead: sourceLead.value || undefined,
        },
      },
    )
    toast.success(__('Opportunity created'))
    show.value = false
    router.push({ name: 'Deal', params: { dealId: dealName } })
  } catch (err) {
    error.value = err.messages?.[0] || __('Could not create the opportunity.')
  } finally {
    creating.value = false
  }
}
</script>

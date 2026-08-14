<template>
  <Dialog v-model:open="show" :size="'xl'">
    <template #body-header>
      <div class="mb-6 flex items-center justify-between">
        <div>
          <h3 class="text-3xl-semibold leading-6 text-ink-gray-9">
            {{ __('Convert to Deal') }}
          </h3>
        </div>
        <div class="flex items-center gap-1">
          <Button
            v-if="isManager() && !isMobileView"
            variant="ghost"
            :tooltip="__('Edit deal\'s mandatory fields layout')"
            :icon="EditIcon"
            @click="openQuickEntryModal"
          />
          <Button icon="lucide-x" variant="ghost" @click="show = false" />
        </div>
      </div>
    </template>
    <template #default>
      <div class="mb-4 flex items-center gap-2 text-ink-gray-5">
        <OrganizationsIcon class="h-4 w-4" />
        <label class="block text-base">{{ __('Organization') }}</label>
      </div>
      <div class="ml-6 text-ink-gray-9">
        <div
          v-if="leadOrganization && !changeOrganization"
          class="flex items-center justify-between gap-2 text-base"
        >
          <div class="truncate">{{ leadOrganization }}</div>
          <Button
            variant="ghost"
            :label="__('Change')"
            @click="changeOrganization = true"
          />
        </div>
        <template v-else>
          <Link
            class="form-control"
            size="md"
            :value="existingOrganization"
            doctype="CRM Organization"
            :onCreate="openOrganizationModal"
            @change="(data) => (existingOrganization = data)"
          />
          <div class="mt-2 text-sm text-ink-gray-5">
            {{
              __(
                'Every opportunity needs an organization. Choose an existing one or create a new one.',
              )
            }}
          </div>
        </template>
      </div>

      <div class="h-px w-full border-t my-6" />

      <div class="mb-4 flex items-center gap-2 text-ink-gray-5">
        <label class="block text-base">{{ __('Pipeline') }}</label>
      </div>
      <div class="ml-6 flex flex-col gap-3.5">
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
            v-model="pipelineStatus"
            class="form-control"
            :options="pipelineStatusOptions"
            :placeholder="__('Select Status...')"
          />
        </div>
      </div>

      <div v-if="dealTabs.data?.length" class="h-px w-full border-t my-6" />

      <FieldLayout
        v-if="dealTabs.data?.length"
        :tabs="dealTabs.data"
        :data="deal.doc"
        doctype="CRM Deal"
      />
      <ErrorMessage class="mt-4" :message="error" />
    </template>
    <template #actions>
      <div class="flex justify-end">
        <Button :label="__('Convert')" variant="solid" @click="convertToDeal" />
      </div>
    </template>
  </Dialog>

  <OrganizationModal
    v-if="showOrganizationModal"
    v-model="showOrganizationModal"
    :data="newOrganization"
    :options="{
      redirect: false,
      afterInsert: (doc) => (existingOrganization = doc.name),
    }"
  />
</template>
<script setup>
import OrganizationsIcon from '@/components/Icons/OrganizationsIcon.vue'
import EditIcon from '@/components/Icons/EditIcon.vue'
import FieldLayout from '@/components/FieldLayout/FieldLayout.vue'
import OrganizationModal from '@/components/Modals/OrganizationModal.vue'
import Link from '@/components/Controls/Link.vue'
import { useDocument } from '@/data/document'
import { usersStore } from '@/stores/users'
import { sessionStore } from '@/stores/session'
import { viewsStore } from '@/stores/views'
import {
  conversionPipelineTypes,
  conversionInitialStatus,
} from '@/utils/pipelineStatuses'
import {
  excludeSelfRenderedFields,
  SELF_RENDERED_FIELDS,
} from '@/utils/convertLayout'
import { getMeta } from '@/stores/meta'
import { showQuickEntryModal, quickEntryProps } from '@/composables/modals'
import { isMobileView } from '@/composables/settings'
import { useOnboarding, useTelemetry } from 'frappe-ui/frappe'
import { Dialog, Select, createResource, call } from 'frappe-ui'
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  lead: { type: Object, required: true },
})

const show = defineModel({ type: Boolean })

const router = useRouter()

const { views } = viewsStore()
const { isManager } = usersStore()
const { user } = sessionStore()
const { updateOnboardingStep } = useOnboarding('frappecrm')
const { doctypeMeta: leadMeta } = getMeta('CRM Lead')

// The lead's own organization is used as-is unless the user explicitly overrides it; the
// backend resolves it by name, which is what keeps a duplicate from being created.
const leadOrganization = computed(() => props.lead.organization || '')
const changeOrganization = ref(false)

const existingOrganization = ref('')
const showOrganizationModal = ref(false)
const newOrganization = ref({})
const error = ref('')
const { capture } = useTelemetry()

const { triggerConvertToDeal } = useDocument('CRM Lead', props.lead.name)
const { document: deal } = useDocument('CRM Deal')

async function convertToDeal() {
  error.value = ''

  // A deal without an organization renders untitled, since organization is the title field.
  if (!leadOrganization.value && !existingOrganization.value) {
    error.value = __('Please select or create an organization')
    return
  }

  if (!pipelineType.value) {
    error.value = __('Please select a pipeline type')
    return
  }

  if (!pipelineStatus.value) {
    error.value = __('Please select a status')
    return
  }

  // Carried in the existing payload, which convert_to_deal applies to the new deal.
  deal.doc.pipeline_type = pipelineType.value
  deal.doc.status = pipelineStatus.value

  await triggerConvertToDeal?.(props.lead, deal.doc, () => (show.value = false))

  let _deal = await call('crm.fcrm.doctype.crm_lead.crm_lead.convert_to_deal', {
    lead: props.lead.name,
    deal: deal.doc,
    existing_organization: existingOrganization.value,
  }).catch((err) => {
    if (err.exc_type == 'MandatoryError') {
      const errorMessage = err.messages
        .map((msg) => {
          let arr = msg.split(': ')
          return arr[arr.length - 1].trim()
        })
        .join(', ')

      if (errorMessage.toLowerCase().includes('required')) {
        error.value = __(errorMessage)
      } else {
        error.value = __('{0} is required', [errorMessage])
      }
      return
    }
    error.value = __('Error converting to deal: {0}', [err.messages?.[0]])
  })
  if (_deal) {
    show.value = false
    changeOrganization.value = false
    existingOrganization.value = ''
    error.value = ''
    updateOnboardingStep('convert_lead_to_deal', true, false, () => {
      localStorage.setItem('firstDeal' + user, _deal)
    })
    capture('convert_lead_to_deal')
    router.push(destinationAfterConvert(_deal))
  }
}

/**
 * Where to land after converting.
 *
 * The pipelines each have their own kanban board, and landing on the board the new deal
 * belongs to is the behaviour users have. The form script did this by mapping pipeline
 * names to hardcoded view ids (16/17/18/28) and assigning window.location; the boards are
 * ordinary views whose labels match the pipeline names, so look one up instead.
 *
 * Falls back to the deal itself if no matching board exists.
 */
function destinationAfterConvert(dealName) {
  const board = (views.data || []).find(
    (view) =>
      view.dt === 'CRM Deal' &&
      view.type === 'kanban' &&
      view.label === pipelineType.value,
  )

  if (!board) return { name: 'Deal', params: { dealId: dealName } }

  return {
    name: 'Deals',
    params: { viewType: 'kanban' },
    query: { view: board.name },
  }
}

// Pipeline Type and Status. The deal does not exist yet, so these are held locally and
// written into deal.doc, which convert_to_deal already applies to the new deal.
//
// Replaces a form script that injected these two selects as raw HTML and monkey-patched
// window.fetch to splice them into the request on its way out.
const pipelineType = ref('')
const pipelineStatus = ref('')

// TXB-125: conversion is limited to the approved pipelines only. The full pipeline_type
// Select on CRM Deal still exists for deals themselves; here we deliberately offer only the
// convertible subset so a lead cannot be dropped onto a pipeline conversion is not meant to
// reach. The backend re-validates this, so a tampered payload gains nothing.
const pipelineTypeOptions = computed(() =>
  conversionPipelineTypes().map((value) => ({ label: __(value), value })),
)

// TXB-125: the initial deal state is fixed per pipeline and derived, not chosen. The Status
// control therefore shows exactly the required entry state and offers no alternative, so it
// cannot be used to start the deal anywhere else. The server derives the same value and
// ignores whatever arrives, making this display-only.
const pipelineStatusOptions = computed(() => {
  const status = conversionInitialStatus(pipelineType.value)
  return status ? [{ label: __(status), value: status }] : []
})

// Selecting a pipeline pins its required initial state; there is nothing else to pick.
watch(pipelineType, () => {
  pipelineStatus.value = conversionInitialStatus(pipelineType.value)
})

const dealTabs = createResource({
  url: 'crm.fcrm.doctype.crm_fields_layout.crm_fields_layout.get_fields_layout',
  cache: ['RequiredFields', 'CRM Deal'],
  params: { doctype: 'CRM Deal', type: 'Required Fields' },
  auto: true,
  transform: (_tabs) => excludeSelfRenderedFields(_tabs, SELF_RENDERED_FIELDS),
})

// Empty, mirroring LEAD_DEAL_FIELD_MAP on the server. deal_owner used to be prefilled
// from the lead; TXB-106 gives the deal to the converting user instead, and a prefilled
// value would read as an Admin's deliberate nomination and be honoured.
const leadDealFieldMap = {}
const skipPrefillFields = ['organization', 'status', 'deal_owner']
const leadFields = computed(() => leadMeta.value?.fields || [])

watch(
  () => dealTabs.data,
  (tabs) => resetDealDoc(tabs),
  { immediate: true },
)

watch(leadFields, () => prefillFields(dealTabs.data))

function resetDealDoc(tabs) {
  deal.doc = { __newDocument: true, doctype: 'CRM Deal' }
  prefillFields(tabs)
}

function prefillFields(tabs) {
  tabs?.forEach((tab) =>
    tab.sections?.forEach((section) =>
      section.columns?.forEach((column) =>
        column.fields?.forEach((field) => prefillField(field)),
      ),
    ),
  )
}

function prefillField(field) {
  if (field.fieldtype === 'Table') {
    deal.doc[field.fieldname] = []
    return
  }
  prefillFromLead(field)
}

function prefillFromLead(field) {
  if (skipPrefillFields.includes(field.fieldname)) return
  if (hasValue(deal.doc[field.fieldname])) return

  const leadFieldname = getLeadFieldname(field)
  if (!leadFieldname) return

  const value = props.lead[leadFieldname]
  if (value != null && value !== '') {
    deal.doc[field.fieldname] = value
  }
}

function getLeadFieldname(field) {
  const mappedFieldname = leadDealFieldMap[field.fieldname]
  if (mappedFieldname) return mappedFieldname
  if (Object.hasOwn(props.lead, field.fieldname)) return field.fieldname

  return getMatchingCustomLeadField(field)?.fieldname
}

function getMatchingCustomLeadField(field) {
  if (!isCustomField(field)) return

  const matches = leadFields.value.filter((leadField) =>
    isMatchingCustomField(leadField, field),
  )
  return matches.length === 1 ? matches[0] : null
}

function isMatchingCustomField(leadField, dealField) {
  return (
    isCustomField(leadField) &&
    leadField.label === dealField.label &&
    leadField.fieldtype === dealField.fieldtype
  )
}

function isCustomField(field) {
  return Boolean(
    field?.is_custom_field ||
      field?.custom ||
      field?.fieldname?.startsWith('custom_') ||
      field?.name === `${field?.parent}-${field?.fieldname}`,
  )
}

function hasValue(value) {
  return value != null && value !== ''
}

function openOrganizationModal(value, close) {
  newOrganization.value = { organization_name: value }
  showOrganizationModal.value = true
  close()
}

function openQuickEntryModal() {
  showQuickEntryModal.value = true
  quickEntryProps.value = {
    doctype: 'CRM Deal',
    onlyRequired: true,
  }
  show.value = false
}
</script>

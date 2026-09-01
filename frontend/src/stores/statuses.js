import IndicatorIcon from '@/components/Icons/IndicatorIcon.vue'
import { parseColor, isTranslatable } from '@/utils'
import { isRetiredLeadStatus } from '@/utils/leadActions'
import { defineStore } from 'pinia'
import { useTelemetry } from 'frappe-ui/frappe'
import { createListResource, createResource } from 'frappe-ui'
import { reactive, h } from 'vue'

// createListResource defaults to 20 rows. There are more deal statuses than that, so the
// store silently truncated the list and some statuses could never be selected.
const ALL_STATUSES_PAGE_LENGTH = 500

export const statusesStore = defineStore('crm-statuses', () => {
  let leadStatusesByName = reactive({})
  let dealStatusesByName = reactive({})
  let communicationStatusesByName = reactive({})

  const { capture } = useTelemetry()

  const leadStatuses = createListResource({
    doctype: 'CRM Lead Status',
    fields: ['name', 'color', 'position', 'type'],
    orderBy: 'position asc',
    pageLength: ALL_STATUSES_PAGE_LENGTH,
    cache: 'lead-statuses',
    initialData: [],
    auto: true,
    transform(statuses) {
      for (let status of statuses) {
        status.color = parseColor(status.color)
        leadStatusesByName[status.name] = status
      }
      return statuses
    },
  })

  const dealStatuses = createListResource({
    doctype: 'CRM Deal Status',
    fields: ['name', 'color', 'position', 'type'],
    orderBy: 'position asc',
    pageLength: ALL_STATUSES_PAGE_LENGTH,
    cache: 'deal-statuses',
    initialData: [],
    auto: true,
    transform(statuses) {
      for (let status of statuses) {
        status.color = parseColor(status.color)
        dealStatusesByName[status.name] = status
      }
      return statuses
    },
  })

  const communicationStatuses = createListResource({
    doctype: 'CRM Communication Status',
    fields: ['name'],
    cache: 'communication-statuses',
    initialData: [],
    auto: true,
    transform(statuses) {
      for (let status of statuses) {
        communicationStatusesByName[status.name] = status
      }
      return statuses
    },
  })

  // Which statuses each pipeline may use. Single source of truth, served by the backend.
  const pipelineStatuses = createResource({
    url: 'crm.txb.api.pipelines.get_pipeline_statuses',
    cache: 'pipeline-statuses',
    initialData: {},
    auto: true,
  })

  function getLeadStatus(name) {
    if (!name) {
      name = leadStatuses.data[0].name
    }
    return leadStatusesByName[name]
  }

  function getDealStatus(name) {
    if (!name) {
      name = dealStatuses.data[0].name
    }
    return dealStatusesByName[name]
  }

  function getCommunicationStatus(name) {
    if (!name) {
      name = communicationStatuses.data[0].name
    }
    return communicationStatuses[name]
  }

  function statusOptions(
    doctype,
    statuses = [],
    triggerStatusChange = null,
    currentValue = null,
  ) {
    let statusesByName =
      doctype == 'deal' ? dealStatusesByName : leadStatusesByName

    if (statuses?.length) {
      statusesByName = statuses.reduce((acc, status) => {
        acc[status] = statusesByName[status]
        return acc
      }, {})
    }

    let translatable = isTranslatable(
      doctype == 'deal' ? 'CRM Deal Status' : 'CRM Lead Status',
    )

    let options = []
    for (const status in statusesByName) {
      // TXB-211: the retired Lead statuses (Qualified, legacy "No Answer") are never selectable
      // on any surface. A record still resting on one during a mixed-version cutover keeps it
      // readable — only the record's own current value is retained, so no other retired status
      // can be picked for a new or existing Lead.
      if (
        doctype != 'deal' &&
        isRetiredLeadStatus(status) &&
        status !== currentValue
      ) {
        continue
      }
      options.push({
        label: translatable
          ? __(statusesByName[status]?.name)
          : statusesByName[status]?.name,
        value: statusesByName[status]?.name,
        icon: () => h(IndicatorIcon, { class: statusesByName[status]?.color }),
        onClick: async () => {
          await triggerStatusChange?.(statusesByName[status]?.name)
          capture('status_changed', { doctype, status })
        },
      })
    }
    return options
  }

  return {
    leadStatuses,
    dealStatuses,
    communicationStatuses,
    pipelineStatuses,
    getLeadStatus,
    getDealStatus,
    getCommunicationStatus,
    statusOptions,
  }
})

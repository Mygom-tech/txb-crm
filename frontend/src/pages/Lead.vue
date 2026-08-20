<template>
  <LayoutHeader>
    <template #left-header>
      <Breadcrumbs :items="breadcrumbs">
        <template #prefix="{ item }">
          <Icon v-if="item.icon" :icon="item.icon" class="mr-2 h-4" />
        </template>
      </Breadcrumbs>
    </template>
    <template v-if="!errorTitle" #right-header>
      <!-- A converted Lead is archived: read-only, no mutation or reconversion controls
           (TXB-132). The backend guard refuses the writes regardless; hiding the controls
           keeps the page honestly read-only. -->
      <Badge
        v-if="isArchived"
        variant="subtle"
        theme="gray"
        :label="__('Archived')"
      />
      <template v-else>
        <CustomActions
          v-if="document._actions?.length"
          :actions="document._actions"
        />
        <CustomActions
          v-if="document.actions?.length"
          :actions="document.actions"
        />
        <EnrichFromWebsite
          doctype="CRM Lead"
          :docname="leadId"
          :website="doc.website"
          @done="onEnriched"
        />
        <Button
          v-if="!userIsAdmin()"
          :label="__('Request Ownership')"
          @click="showRequestOwnership = true"
        />
        <AssignTo
          v-model="assignees.data"
          doctype="CRM Lead"
          :docname="leadId"
        />
        <Dropdown
          v-if="doc && document.statuses"
          :options="statuses"
          placement="right"
        >
          <template #default="{ open }">
            <Button
              v-if="doc.status"
              :label="statusLabel(doc.status)"
              :iconRight="open ? 'chevron-up' : 'chevron-down'"
            >
              <template #prefix>
                <IndicatorIcon :class="getLeadStatus(doc.status).color" />
              </template>
            </Button>
          </template>
        </Dropdown>
        <Button
          v-if="requiresDiscovery(doc.status)"
          :label="__('Run Discovery Meeting')"
          variant="solid"
          @click="runDiscovery"
        />
        <Button
          :label="__('Convert to Deal')"
          variant="solid"
          @click="showConvertToDealModal = true"
        />
      </template>
    </template>
  </LayoutHeader>
  <ArchivedLeadBanner v-if="isArchived && doc.name" :lead="doc" />
  <div v-if="doc.name" class="flex h-full overflow-hidden">
    <Tabs
      v-model="tabIndex"
      :tabs="tabs"
      class="flex flex-1 overflow-hidden flex-col [&_[role='tab']]:px-0 [&_[role='tab']]:shrink-0 [&_[role='tablist']]:px-5 [&_[role='tablist']::-webkit-scrollbar]:h-0 [&_[role='tablist']]:min-h-[45px] [&_[role='tablist']]:gap-7.5 [&_[role='tabpanel']:not([hidden])]:flex [&_[role='tabpanel']:not([hidden])]:grow"
    >
      <template #tab-panel>
        <Activities
          ref="activities"
          v-model:reload="reload"
          v-model:tabIndex="tabIndex"
          doctype="CRM Lead"
          :docname="leadId"
          :tabs="tabs"
          @beforeSave="beforeStatusChange"
          @afterSave="reloadResources"
        />
      </template>
    </Tabs>
    <Resizer
      side="right"
      class="flex flex-col justify-between border-l"
      :defaultWidth="sidebarDefaultWidth"
      :minWidth="SIDEBAR_MIN_WIDTH"
      :maxWidth="SIDEBAR_MAX_WIDTH"
      :persistWidth="saveLeadSidebarWidth"
    >
      <div
        class="flex h-[45px] cursor-copy items-center border-b px-5 py-2.5 text-lg-medium text-ink-gray-9"
        @click="copyToClipboard(leadId)"
      >
        {{ __(leadId) }}
      </div>
      <FileUploader
        :validateFile="validateIsImageFile"
        @success="(file) => updateField('image', file.file_url)"
      >
        <template #default="{ openFileSelector }">
          <div class="flex items-center justify-start gap-5 border-b p-5">
            <div class="group relative size-12">
              <Avatar
                size="3xl"
                class="size-12"
                :label="title"
                :image="doc.image || doc.organization_logo"
              />
              <component
                :is="doc.image ? Dropdown : 'div'"
                v-bind="
                  doc.image
                    ? {
                        options: [
                          {
                            icon: 'upload',
                            label: doc.image
                              ? __('Change Image')
                              : __('Upload Image'),
                            onClick: openFileSelector,
                          },
                          {
                            icon: 'trash-2',
                            label: __('Remove Image'),
                            onClick: () => updateField('image', ''),
                          },
                        ],
                      }
                    : { onClick: openFileSelector }
                "
                class="!absolute bottom-0 left-0 right-0"
              >
                <div
                  class="z-1 absolute bottom-0.5 left-0 right-0.5 flex h-9 cursor-pointer items-center justify-center rounded-b-full bg-black bg-opacity-40 pt-3 opacity-0 duration-300 ease-in-out group-hover:opacity-100"
                  style="
                    -webkit-clip-path: inset(12px 0 0 0);
                    clip-path: inset(12px 0 0 0);
                  "
                >
                  <CameraIcon class="size-4 cursor-pointer text-white" />
                </div>
              </component>
            </div>
            <div class="flex flex-col gap-2.5 truncate">
              <Tooltip :text="doc.lead_name || __('Set First Name')">
                <div class="truncate text-3xl-medium text-ink-gray-9">
                  {{ title }}
                </div>
              </Tooltip>
              <div class="flex gap-1.5">
                <Button
                  v-if="callEnabled"
                  :tooltip="__('Make a Call')"
                  :icon="PhoneIcon"
                  @click="
                    () =>
                      doc.mobile_no
                        ? makeCall(doc.mobile_no)
                        : toast.error(
                            __('Please set a mobile number to make calls'),
                          )
                  "
                />

                <Button
                  :tooltip="__('Send an Email')"
                  :icon="Email2Icon"
                  @click="
                    doc.email
                      ? openEmailBox()
                      : toast.error(
                          __('Please set an email address to send emails'),
                        )
                  "
                />
                <Button
                  :tooltip="__('Go to Website')"
                  :icon="LinkIcon"
                  @click="
                    doc.website
                      ? openWebsite(doc.website)
                      : toast.error(__('Please set a website to visit'))
                  "
                />

                <Button
                  :tooltip="__('Attach a File')"
                  :icon="AttachmentIcon"
                  @click="showFilesUploader = true"
                />

                <Button
                  v-if="canDelete"
                  :tooltip="__('Delete')"
                  variant="subtle"
                  theme="red"
                  icon="lucide-trash-2"
                  @click="deleteLead"
                />
              </div>
              <ErrorMessage :message="__(error)" />
            </div>
          </div>
        </template>
      </FileUploader>
      <SLASection
        v-if="doc.sla_status"
        v-model="doc"
        @updateField="updateField"
      />
      <div
        v-if="sections.data"
        class="flex flex-1 flex-col justify-between overflow-hidden"
      >
        <SidePanelLayout
          :sections="sections.data"
          doctype="CRM Lead"
          :docname="leadId"
          @reload="sections.reload"
          @beforeFieldChange="beforeStatusChange"
          @afterFieldChange="reloadResources"
        />
      </div>
    </Resizer>
  </div>
  <ErrorPage
    v-else-if="errorTitle"
    :errorTitle="errorTitle"
    :errorMessage="errorMessage"
  />
  <ConvertToDealModal
    v-if="showConvertToDealModal"
    v-model="showConvertToDealModal"
    :lead="doc"
  />
  <FilesUploader
    v-model="showFilesUploader"
    doctype="CRM Lead"
    :docname="leadId"
    @after="
      () => {
        activities?.all_activities?.reload()
        changeTabTo('attachments')
      }
    "
  />
  <DeleteLinkedDocModal
    v-if="showDeleteLinkedDocModal"
    v-model="showDeleteLinkedDocModal"
    :doctype="'CRM Lead'"
    :docname="leadId"
    name="Leads"
  />
  <LostReasonModal
    v-if="showLostReasonModal"
    v-model="showLostReasonModal"
    doctype="CRM Lead"
    :document="document"
    skippable
    :skip-reason="PENDING_REVIEW"
  />
  <RequestOwnershipModal
    v-if="showRequestOwnership"
    v-model="showRequestOwnership"
    doctype="CRM Lead"
    :docname="leadId"
    :current-owner="doc?.lead_owner"
  />
</template>
<script setup>
import DeleteLinkedDocModal from '@/components/DeleteLinkedDocModal.vue'
import ErrorPage from '@/components/ErrorPage.vue'
import Icon from '@/components/Icon.vue'
import Resizer from '@/components/Resizer.vue'
import {
  entitySidebarWidth,
  SIDEBAR_ENTITIES,
  SIDEBAR_MIN_WIDTH,
  SIDEBAR_MAX_WIDTH,
} from '@/utils/resizerState'
import ActivityIcon from '@/components/Icons/ActivityIcon.vue'
import EmailIcon from '@/components/Icons/EmailIcon.vue'
import Email2Icon from '@/components/Icons/Email2Icon.vue'
import CommentIcon from '@/components/Icons/CommentIcon.vue'
import DetailsIcon from '@/components/Icons/DetailsIcon.vue'
import EventIcon from '@/components/Icons/EventIcon.vue'
import PhoneIcon from '@/components/Icons/PhoneIcon.vue'
import TaskIcon from '@/components/Icons/TaskIcon.vue'
import NoteIcon from '@/components/Icons/NoteIcon.vue'
import WhatsAppIcon from '@/components/Icons/WhatsAppIcon.vue'
import IndicatorIcon from '@/components/Icons/IndicatorIcon.vue'
import CameraIcon from '@/components/Icons/CameraIcon.vue'
import LinkIcon from '@/components/Icons/LinkIcon.vue'
import AttachmentIcon from '@/components/Icons/AttachmentIcon.vue'
import LostReasonModal from '@/components/Modals/LostReasonModal.vue'
import LayoutHeader from '@/components/LayoutHeader.vue'
import Activities from '@/components/Activities/Activities.vue'
import AssignTo from '@/components/AssignTo.vue'
import FilesUploader from '@/components/FilesUploader/FilesUploader.vue'
import SidePanelLayout from '@/components/SidePanelLayout.vue'
import SLASection from '@/components/SLASection.vue'
import CustomActions from '@/components/CustomActions.vue'
import ArchivedLeadBanner from '@/components/ArchivedLeadBanner.vue'
import ConvertToDealModal from '@/components/Modals/ConvertToDealModal.vue'
import RequestOwnershipModal from '@/components/Modals/RequestOwnershipModal.vue'
import EnrichFromWebsite from '@/components/EnrichFromWebsite.vue'
import {
  openWebsite,
  setupCustomizations,
  copyToClipboard,
  validateIsImageFile,
  isTranslatable,
} from '@/utils'
import { getView } from '@/utils/view'
import {
  isDisqualifiedReasonUnresolved,
  PENDING_REVIEW,
} from '@/utils/leadReasonPrompt'
import {
  requiresDiscovery,
  runDiscoveryMeeting,
  resolveLeadStatusTransition,
  isGuardedLeadTransition,
  LEAD_TRANSITION_SAVED,
  LEAD_TRANSITION_FAILED,
} from '@/utils/leadActions'
import { sessionStore } from '@/stores/session'
import { getSettings } from '@/stores/settings'
import { globalStore } from '@/stores/global'
import { statusesStore } from '@/stores/statuses'
import { getMeta } from '@/stores/meta'
import { transitionsStore } from '@/stores/transitions'
import { useDocument } from '@/data/document'
import { whatsappEnabled } from '@/composables/whatsapp'
import { callEnabled } from '@/composables/telephony'
import {
  createResource,
  FileUploader,
  Dropdown,
  Tooltip,
  Avatar,
  Badge,
  Tabs,
  Breadcrumbs,
  call,
  usePageMeta,
  toast,
} from 'frappe-ui'
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useActiveTabManager } from '@/composables/useActiveTabManager'

const { brand } = getSettings()
const { $dialog, $socket, makeCall } = globalStore()
const { statusOptions, getLeadStatus } = statusesStore()
const { user: sessionUser } = sessionStore()
const { isAdmin: userIsAdmin } = transitionsStore()
const { doctypeMeta } = getMeta('CRM Lead')

const route = useRoute()
const router = useRouter()

const props = defineProps({
  leadId: { type: String, required: true },
})

// Restore the persisted Lead sidebar width from its own namespace, validated
// and clamped to the resizer's limits and the current viewport. Shares the
// responsive default, validation and clamping with Deal/Organization while
// keeping an independent saved width.
const { load: loadLeadSidebarWidth, save: saveLeadSidebarWidth } =
  entitySidebarWidth(SIDEBAR_ENTITIES.lead)
const sidebarDefaultWidth = loadLeadSidebarWidth({
  minWidth: SIDEBAR_MIN_WIDTH,
  maxWidth: SIDEBAR_MAX_WIDTH,
  viewportWidth: typeof window !== 'undefined' ? window.innerWidth : undefined,
})

const reload = ref(false)
const activities = ref(null)
const errorTitle = ref('')
const errorMessage = ref('')
const showDeleteLinkedDocModal = ref(false)
const showConvertToDealModal = ref(false)
const showFilesUploader = ref(false)
const showRequestOwnership = ref(false)

const {
  triggerOnChange,
  triggerOnRender,
  assignees,
  permissions,
  document,
  scripts,
  error,
} = useDocument('CRM Lead', props.leadId)

const canDelete = computed(() => permissions.data?.permissions?.delete || false)

const doc = computed(() => document.doc || {})

// A converted Lead is archived: the page becomes a read-only historical record whose
// conversion result is surfaced instead of mutation/reconversion controls (TXB-132).
const isArchived = computed(() => Boolean(doc.value.converted))

onMounted(async () => {
  if (document.doc) await triggerOnRender()
})

watch(error, (err) => {
  if (err) {
    errorTitle.value = __(
      err.exc_type == 'DoesNotExistError'
        ? 'Document not found'
        : 'Error occurred',
    )
    errorMessage.value = __(err.messages?.[0] || 'An error occurred')
  } else {
    errorTitle.value = ''
    errorMessage.value = ''
  }
})

watch(
  () => document.doc,
  async (_doc) => {
    if (scripts.data?.length) {
      let s = await setupCustomizations(scripts.data, {
        doc: _doc,
        $dialog,
        $socket,
        router,
        toast,
        updateField,
        createToast: toast.create,
        deleteDoc: deleteLead,
        call,
      })
      document._actions = s.actions || []
      document._statuses = s.statuses || []
    }
  },
  { once: true },
)

const breadcrumbs = computed(() => {
  let items = [{ label: __('Leads'), route: { name: 'Leads' } }]

  if (route.query.view || route.query.viewType) {
    let view = getView(route.query.view, route.query.viewType, 'CRM Lead')
    if (view) {
      items.push({
        label: __(view.label),
        icon: view.icon,
        route: {
          name: 'Leads',
          params: { viewType: route.query.viewType },
          query: { view: route.query.view },
        },
      })
    }
  }

  items.push({
    label: title.value,
    route: {
      name: 'Lead',
      params: { leadId: props.leadId },
      query: route.query,
    },
  })
  return items
})

const title = computed(() => {
  let t = doctypeMeta.value?.title_field || 'name'
  return doc.value?.[t] || props.leadId
})

const statuses = computed(() => {
  let customStatuses = document.statuses?.length
    ? document.statuses
    : document._statuses || []
  return statusOptions('lead', customStatuses, triggerStatusChange)
})

usePageMeta(() => {
  return { title: title.value, icon: brand.favicon }
})

const tabs = computed(() => {
  let tabOptions = [
    {
      name: 'Activity',
      label: __('Activity'),
      icon: ActivityIcon,
    },
    {
      name: 'Emails',
      label: __('Emails'),
      icon: EmailIcon,
    },
    {
      name: 'Comments',
      label: __('Comments'),
      icon: CommentIcon,
    },
    {
      name: 'Data',
      label: __('Data'),
      icon: DetailsIcon,
    },
    {
      name: 'Events',
      label: __('Events'),
      icon: EventIcon,
    },
    {
      name: 'Calls',
      label: __('Calls'),
      icon: PhoneIcon,
    },
    {
      name: 'Tasks',
      label: __('Tasks'),
      icon: TaskIcon,
    },
    {
      name: 'Notes',
      label: __('Notes'),
      icon: NoteIcon,
    },
    {
      name: 'Attachments',
      label: __('Attachments'),
      icon: AttachmentIcon,
    },
    {
      name: 'WhatsApp',
      label: __('WhatsApp'),
      icon: WhatsAppIcon,
      condition: () => whatsappEnabled.value,
    },
  ]
  return tabOptions.filter((tab) => (tab.condition ? tab.condition() : true))
})

const { tabIndex, changeTabTo } = useActiveTabManager(tabs, 'lastLeadTab')

const sections = createResource({
  url: 'crm.fcrm.doctype.crm_fields_layout.crm_fields_layout.get_sidepanel_sections',
  cache: ['sidePanelSections', 'CRM Lead'],
  params: { doctype: 'CRM Lead' },
  auto: true,
})

async function triggerStatusChange(value) {
  // Every guarded existing-Lead transition -- entering Contacted (Log a reach), Contact
  // attempted (Log a dial) or Discovery meeting set (Schedule Discovery meeting) -- is routed
  // through the one shared authority instead of a bare status write the backend guard rejects.
  // The in-memory status is not touched first, so a cancelled or failed action leaves the lead
  // exactly where it was.
  const routed = await resolveLeadStatusTransition(doc.value?.status, value, props.leadId, {
    actor: sessionUser,
  })
  if (routed.guarded) {
    applyGuardedTransition(routed)
    return
  }
  await triggerOnChange('status', value)
  setLostReason()
}

// Map the shared authority's outcome onto the desktop Lead surfaces' refresh. A cancelled or
// failed transition posted nothing, so reloading the document discards any optimistic status
// change and restores the prior status; a saved one atomically applied the new status server-
// side, so it additionally refreshes the side-panel sections and the activity feed (TXB-164:
// the reach/dial land as native FCRM Notes). A failure is surfaced as a toast.
function applyGuardedTransition(routed) {
  if (routed.outcome === LEAD_TRANSITION_FAILED) {
    toast.error(routed.error?.messages?.[0] || __('Could not update the lead status'))
  }
  document.reload?.()
  if (routed.outcome === LEAD_TRANSITION_SAVED) {
    reload.value = true
    sections.reload()
    activities.value?.all_activities?.reload()
  }
}

async function runDiscovery() {
  try {
    const result = await runDiscoveryMeeting(props.leadId)
    // Cancel resolves null: nothing was sent, the lead stays at Discovery meeting set, so there
    // is nothing to refresh.
    if (!result) return

    // The submit applied the outcome atomically -- a resting status, or a conversion that
    // created/reused the Contact and one Opportunity. Refresh so the header, activity feed and
    // side panel catch up; a converted lead routes to its new deal.
    if (result.converted && result.deal) {
      router.push({ name: 'Deal', params: { dealId: result.deal } })
      return
    }
    document.reload?.()
    reload.value = true
    sections.reload()
  } catch (error) {
    toast.error(error.messages?.[0] || __('Could not run the discovery meeting'))
  }
}

function updateField(name, value) {
  value = Array.isArray(name) ? '' : value
  let oldValues = Array.isArray(name) ? {} : doc.value[name]

  if (Array.isArray(name)) {
    name.forEach((field) => (doc.value[field] = value))
  } else {
    doc.value[name] = value
  }

  document.save.submit(null, {
    onSuccess: () => (reload.value = true),
    onError: (err) => {
      if (Array.isArray(name)) {
        name.forEach((field) => (doc.value[field] = oldValues[field]))
      } else {
        doc.value[name] = oldValues
      }
      toast.error(err.messages?.[0] || __('Error updating field'))
    },
  })
}

function deleteLead() {
  showDeleteLinkedDocModal.value = true
}

function openEmailBox() {
  let currentTab = tabs.value[tabIndex.value]
  if (!['Emails', 'Comments', 'Activities'].includes(currentTab.name)) {
    activities.value.changeTabTo('emails')
  }
  nextTick(() => (activities.value.emailBox.show = true))
}

function statusLabel(status) {
  if (isTranslatable('CRM Lead Status')) return __(status)
  return status
}

const showLostReasonModal = ref(false)

// Native replacement for the `Disqualified Reason Prompt` script: opening a Disqualified
// lead whose reason is still unresolved (blank or Pending Review) re-opens the reason
// modal every time. Keying on the loaded lead name fires this once per opened lead;
// resolved leads (a real reason set) never auto-open it.
watch(
  () => doc.value?.name,
  (name) => {
    if (name && isDisqualifiedReasonUnresolved(doc.value, getLeadStatus)) {
      showLostReasonModal.value = true
    }
  },
  { immediate: true },
)

function setLostReason() {
  if (
    getLeadStatus(document.doc.status).type !== 'Lost' ||
    (document.doc.lost_reason && document.doc.lost_reason !== 'Other') ||
    (document.doc.lost_reason === 'Other' && document.doc.lost_notes)
  ) {
    document.save.submit(null, {
      onSuccess: () => sections.reload(),
    })
    return
  }

  showLostReasonModal.value = true
}

function beforeStatusChange(data) {
  const hasStatus = Object.hasOwn(data ?? {}, 'status')
  // The side panel and activity status controls are guarded the same way as the header
  // dropdown. They mutate the in-memory status optimistically but do not persist it here, so
  // route the target through the shared authority (prior status is already overwritten, so it
  // is passed as undefined and the target-only gates still fire). applyGuardedTransition reloads
  // afterwards to reflect the server state or discard the optimistic change on cancel/failure.
  if (hasStatus && isGuardedLeadTransition(undefined, data.status)) {
    resolveLeadStatusTransition(undefined, data.status, props.leadId, {
      actor: sessionUser,
    }).then(applyGuardedTransition)
    return
  }
  if (hasStatus && getLeadStatus(data.status).type == 'Lost') {
    setLostReason()
  } else {
    document.save.submit(null, {
      onSuccess: () => reloadResources(data),
    })
  }
}

function onEnriched() {
  document.reload?.()
  sections.reload()
}

function reloadResources(data) {
  if (Object.hasOwn(data ?? {}, 'lead_owner')) {
    assignees.reload()
  }
  if (
    Object.hasOwn(data ?? {}, 'status') &&
    getLeadStatus(data.status).type != 'Lost'
  ) {
    sections.reload()
  }
}
</script>

<template>
  <LayoutHeader>
    <header
      class="relative flex h-10.5 items-center justify-between gap-2 py-2.5 pl-2"
    >
      <Breadcrumbs :items="breadcrumbs">
        <template #prefix="{ item }">
          <Icon v-if="item.icon" :icon="item.icon" class="mr-2 h-4" />
        </template>
      </Breadcrumbs>
      <div class="absolute right-0">
        <Dropdown v-if="doc && canChangeStatus" :options="statuses">
          <template #default="{ open }">
            <Button
              v-if="doc.status"
              :label="statusLabel(doc.status)"
              :iconRight="open ? 'chevron-up' : 'chevron-down'"
            >
              <template #prefix>
                <IndicatorIcon :class="getDealStatus(doc.status).color" />
              </template>
            </Button>
          </template>
        </Dropdown>
        <Button
          v-else-if="doc?.status"
          :label="statusLabel(doc.status)"
          disabled
        >
          <template #prefix>
            <IndicatorIcon :class="getDealStatus(doc.status).color" />
          </template>
        </Button>
      </div>
    </header>
  </LayoutHeader>
  <div
    v-if="doc.name"
    class="flex h-12 items-center justify-between gap-2 border-b px-3 py-2.5"
  >
    <Button
      v-if="!userIsAdmin()"
      :label="__('Request Ownership')"
      @click="showRequestOwnership = true"
    />
    <AssignTo v-model="assignees.data" doctype="CRM Deal" :docname="dealId" />
    <div class="flex items-center gap-2">
      <Dropdown v-if="availableActions.length" :options="takeActionOptions">
        <Button :label="__('Take Action')" iconRight="chevron-down" />
      </Dropdown>
      <CustomActions
        v-if="document._actions?.length"
        :actions="document._actions"
      />
      <CustomActions
        v-if="document.actions?.length"
        :actions="document.actions"
      />
    </div>
  </div>
  <div v-if="doc.name" class="flex h-full overflow-hidden">
    <Tabs
      v-model="tabIndex"
      as="div"
      :tabs="tabs"
      class="flex flex-1 overflow-auto flex-col [&_[role='tab']]:px-0 [&_[role='tab']]:shrink-0 [&_[role='tablist']]:px-3 [&_[role='tablist']]:min-h-[45px] [&_[role='tablist']]:gap-7.5 [&_[role='tabpanel']:not([hidden])]:flex [&_[role='tabpanel']:not([hidden])]:grow"
    >
      <template #tab-panel="{ tab }">
        <div v-if="tab.name == 'Details'">
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
              doctype="CRM Deal"
              :docname="dealId"
              @reload="sections.reload"
              @action-completed="reload = true"
              @beforeFieldChange="beforeStatusChange"
              @afterFieldChange="reloadAssignees"
            >
              <template #actions="{ section }">
                <div v-if="section.name == 'contacts_section'" class="pr-2">
                  <Link
                    value=""
                    doctype="Contact"
                    :onCreate="
                      (value, close) => {
                        _contact = {
                          first_name: value,
                          company_name: doc.organization,
                        }
                        showContactModal = true
                        close()
                      }
                    "
                    @change="(e) => addContact(e)"
                  >
                    <template #target="{ togglePopover }">
                      <Button
                        class="h-7 px-3"
                        variant="ghost"
                        icon="lucide-plus"
                        @click="togglePopover()"
                      />
                    </template>
                  </Link>
                </div>
              </template>
              <template #default="{ section }">
                <div
                  v-if="section.name == 'contacts_section'"
                  class="contacts-area"
                >
                  <div
                    v-if="
                      dealContacts?.loading && dealContacts?.data?.length == 0
                    "
                    class="flex min-h-20 flex-1 items-center justify-center gap-3 text-base text-ink-gray-4"
                  >
                    <LoadingIndicator class="h-4 w-4" />
                    <span>{{ __('Loading...') }}</span>
                  </div>
                  <div
                    v-for="(contact, i) in section.contacts"
                    v-else-if="section.contacts.length"
                    :key="contact.name"
                  >
                    <div
                      class="px-2 pb-2.5"
                      :class="[i == 0 ? 'pt-5' : 'pt-2.5']"
                    >
                      <CollapsibleSection :opened="contact.opened">
                        <template #header="{ opened, toggle }">
                          <div
                            class="flex cursor-pointer items-center justify-between gap-2 pr-1 text-base leading-5 text-ink-gray-7"
                          >
                            <div
                              class="flex h-7 items-center gap-2 truncate"
                              @click="toggle()"
                            >
                              <Avatar
                                :label="contact.full_name"
                                :image="contact.image"
                                size="md"
                              />
                              <div class="truncate">
                                {{ contact.full_name }}
                              </div>
                              <Badge
                                v-if="contact.is_primary"
                                class="ml-2"
                                variant="outline"
                                :label="__('Primary')"
                                theme="green"
                              />
                            </div>
                            <div class="flex items-center">
                              <Dropdown :options="contactOptions(contact.name)">
                                <Button
                                  icon="lucide-more-horizontal"
                                  class="text-ink-gray-5"
                                  variant="ghost"
                                />
                              </Dropdown>
                              <Button
                                variant="ghost"
                                @click="
                                  router.push({
                                    name: 'Contact',
                                    params: { contactId: contact.name },
                                  })
                                "
                              >
                                <ArrowUpRightIcon class="h-4 w-4" />
                              </Button>
                              <Button variant="ghost" @click="toggle()">
                                <span
                                  class="lucide-chevron-right h-4 w-4 text-ink-gray-9 transition-all duration-300 ease-in-out"
                                  :class="{ 'rotate-90': opened }"
                                  aria-hidden="true"
                                />
                              </Button>
                            </div>
                          </div>
                        </template>
                        <div
                          class="flex flex-col gap-1.5 text-base text-ink-gray-8"
                        >
                          <div class="flex items-center gap-3 pb-1.5 pl-1 pt-4">
                            <Email2Icon class="h-4 w-4" />
                            {{ contact.email }}
                          </div>
                          <div class="flex items-center gap-3 p-1 py-1.5">
                            <PhoneIcon class="h-4 w-4" />
                            {{ contact.mobile_no }}
                          </div>
                        </div>
                      </CollapsibleSection>
                    </div>
                    <div
                      v-if="i != section.contacts.length - 1"
                      class="mx-2 h-px border-t border-outline-elevation-2"
                    />
                  </div>
                  <div
                    v-else
                    class="flex h-20 items-center justify-center text-base text-ink-gray-5"
                  >
                    {{ __('No Contacts Added') }}
                  </div>
                </div>
              </template>
            </SidePanelLayout>
          </div>
        </div>
        <Activities
          v-else
          v-model:reload="reload"
          v-model:tabIndex="tabIndex"
          doctype="CRM Deal"
          :docname="dealId"
          :tabs="tabs"
          @beforeSave="beforeStatusChange"
          @afterSave="reloadAssignees"
        />
      </template>
    </Tabs>
  </div>
  <ErrorPage
    v-else-if="errorTitle"
    :errorTitle="errorTitle"
    :errorMessage="errorMessage"
  />
  <OrganizationModal
    v-if="showOrganizationModal"
    v-model="showOrganizationModal"
    :data="_organization"
    :options="{
      redirect: false,
      afterInsert: (_doc) => updateField('organization', _doc.name),
    }"
  />
  <ContactModal
    v-if="showContactModal"
    v-model="showContactModal"
    :contact="_contact"
    :options="{
      redirect: false,
      afterInsert: (_doc) => addContact(_doc.name),
    }"
  />
  <DeleteLinkedDocModal
    v-if="showDeleteLinkedDocModal"
    v-model="showDeleteLinkedDocModal"
    :doctype="'CRM Deal'"
    :docname="dealId"
    name="Deals"
  />
  <LostReasonModal
    v-if="showLostReasonModal"
    v-model="showLostReasonModal"
    doctype="CRM Deal"
    :document="document"
  />
  <RequestOwnershipModal
    v-if="showRequestOwnership"
    v-model="showRequestOwnership"
    doctype="CRM Deal"
    :docname="dealId"
    :current-owner="doc?.deal_owner"
  />
</template>
<script setup>
import DeleteLinkedDocModal from '@/components/DeleteLinkedDocModal.vue'
import ErrorPage from '@/components/ErrorPage.vue'
import Icon from '@/components/Icon.vue'
import DetailsIcon from '@/components/Icons/DetailsIcon.vue'
import LoadingIndicator from '@/components/Icons/LoadingIndicator.vue'
import ActivityIcon from '@/components/Icons/ActivityIcon.vue'
import EmailIcon from '@/components/Icons/EmailIcon.vue'
import Email2Icon from '@/components/Icons/Email2Icon.vue'
import CommentIcon from '@/components/Icons/CommentIcon.vue'
import PhoneIcon from '@/components/Icons/PhoneIcon.vue'
import TaskIcon from '@/components/Icons/TaskIcon.vue'
import NoteIcon from '@/components/Icons/NoteIcon.vue'
import AttachmentIcon from '@/components/Icons/AttachmentIcon.vue'
import WhatsAppIcon from '@/components/Icons/WhatsAppIcon.vue'
import IndicatorIcon from '@/components/Icons/IndicatorIcon.vue'
import ArrowUpRightIcon from '@/components/Icons/ArrowUpRightIcon.vue'
import SuccessIcon from '@/components/Icons/SuccessIcon.vue'
import LayoutHeader from '@/components/LayoutHeader.vue'
import Activities from '@/components/Activities/Activities.vue'
import OrganizationModal from '@/components/Modals/OrganizationModal.vue'
import LostReasonModal from '@/components/Modals/LostReasonModal.vue'
import AssignTo from '@/components/AssignTo.vue'
import ContactModal from '@/components/Modals/ContactModal.vue'
import CollapsibleSection from '@/components/CollapsibleSection.vue'
import Link from '@/components/Controls/Link.vue'
import SidePanelLayout from '@/components/SidePanelLayout.vue'
import SLASection from '@/components/SLASection.vue'
import CustomActions from '@/components/CustomActions.vue'
import RequestOwnershipModal from '@/components/Modals/RequestOwnershipModal.vue'
import { setupCustomizations, isTranslatable } from '@/utils'
import { getView } from '@/utils/view'
import { allowedStatusesFor } from '@/utils/pipelineStatuses'
import { actionOptions, runAction } from '@/utils/takeAction'
import {
  allowedTargets,
  prefillFor,
  refreshCandidateActions,
} from '@/utils/dealTransitions'
import { chooseAction } from '@/utils/kanbanTransitions'
import { getSettings } from '@/stores/settings'
import { globalStore } from '@/stores/global'
import { statusesStore } from '@/stores/statuses'
import { transitionsStore } from '@/stores/transitions'
import { getMeta } from '@/stores/meta'
import { useDocument } from '@/data/document'
import { isMobileView } from '@/composables/settings'
import { whatsappEnabled } from '@/composables/whatsapp'
import { callEnabled } from '@/composables/telephony'
import { useActiveTabManager } from '@/composables/useActiveTabManager'
import {
  createResource,
  Dropdown,
  Avatar,
  Tabs,
  Breadcrumbs,
  call,
  usePageMeta,
  toast,
} from 'frappe-ui'
import { ref, computed, h, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const { brand } = getSettings()
const { $dialog, $socket } = globalStore()
const { statusOptions, getDealStatus, pipelineStatuses } = statusesStore()
const { transitionMap, isAdmin: userIsAdmin } = transitionsStore()
const isAdmin = computed(() => dealActions.data?.is_admin === true)

// Take Action, mirroring the desktop page. Shares a cache key with Deal.vue and the side
// panel, so one request answers for all of them.
const dealActions = createResource({
  url: 'crm.txb.api.actions.get_available_actions',
  cache: ['deal-actions', props.dealId],
  makeParams: () => ({ deal: props.dealId }),
  auto: true,
  initialData: { actions: [], can_change_status: true, is_admin: false },
})

const availableActions = computed(() => dealActions.data?.actions || [])
const canChangeStatus = computed(
  () => dealActions.data?.can_change_status !== false,
)

const takeActionOptions = computed(() =>
  actionOptions(availableActions.value, onTakeAction),
)

async function onTakeAction(action, defaults) {
  try {
    const result = await runAction(props.dealId, action, { defaults })
    if (!result) return

    // Activities watches this ref and reloads both the feed and the document, which is
    // what refreshes doc.status and therefore re-filters the action menu.
    reload.value = true
    dealActions.reload()
  } catch (error) {
    toast.error(error.messages?.[0] || __('Could not complete the action'))
  }
}

// Refetch once the server has accepted a change: a local edit mutates doc.status
// optimistically before the save is sent, so reacting to the status alone would ask the
// server while it still holds the previous value. See Deal.vue for the full reasoning.
watch(
  () => document.save?.loading,
  (saving, wasSaving) => {
    if (wasSaving && !saving) dealActions.reload()
  },
)

watch(
  () => doc.value?.status,
  () => {
    if (document.isDirty) return
    dealActions.reload()
  },
)

const statuses = computed(() => {
  // A form script may pin an explicit list; otherwise restrict to the deal's pipeline.
  let customStatuses = document.statuses?.length
    ? document.statuses
    : document._statuses || []

  if (!customStatuses.length) {
    customStatuses = allowedStatusesFor(
      doc.value?.pipeline_type,
      doc.value?.status,
      pipelineStatuses.data,
    )
  }

  // Non-Admins are offered only what the state machine can actually reach, so a user
  // never picks a status and then hears it was refused.
  if (!isAdmin.value) {
    const reachable = allowedTargets(
      transitionMap.data?.transitions,
      doc.value?.pipeline_type,
      doc.value?.status,
    )
    if (reachable.length) {
      customStatuses = [doc.value.status, ...reachable]
    }
  }

  return statusOptions('deal', customStatuses, triggerStatusChange)
})
const { doctypeMeta } = getMeta('CRM Deal')

const route = useRoute()
const router = useRouter()

const props = defineProps({
  dealId: { type: String, required: true },
})

const errorTitle = ref('')
const errorMessage = ref('')
const showDeleteLinkedDocModal = ref(false)
const showRequestOwnership = ref(false)

const {
  triggerOnChange,
  triggerOnRender,
  assignees,
  document,
  scripts,
  error,
} = useDocument('CRM Deal', props.dealId)

const doc = computed(() => document.doc || {})

onMounted(async () => {
  if (document.doc) await triggerOnRender()
})

watch(error, (err) => {
  if (err) {
    errorTitle.value = __(
      err.exc_type == 'DoesNotExistError'
        ? __('Document not found')
        : __('Error occurred'),
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
        deleteDoc: deleteDeal,
        call,
      })
      document._actions = s.actions || []
      document._statuses = s.statuses || []
    }
  },
  { once: true },
)

const reload = ref(false)
const showOrganizationModal = ref(false)
const _organization = ref({})

const breadcrumbs = computed(() => {
  let items = [{ label: __('Deals'), route: { name: 'Deals' } }]

  if (route.query.view || route.query.viewType) {
    let view = getView(route.query.view, route.query.viewType, 'CRM Deal')
    if (view) {
      items.push({
        label: __(view.label),
        icon: view.icon,
        route: {
          name: 'Deals',
          params: { viewType: route.query.viewType },
          query: { view: route.query.view },
        },
      })
    }
  }

  items.push({
    label: title.value,
    route: {
      name: 'Deal',
      params: { dealId: props.dealId },
      query: route.query,
    },
  })
  return items
})

const title = computed(() => {
  let t = doctypeMeta.value?.title_field || 'name'
  return doc.value?.[t] || props.dealId
})

usePageMeta(() => {
  return {
    title: title.value,
    icon: brand.favicon,
  }
})

const tabs = computed(() => {
  let tabOptions = [
    {
      name: 'Details',
      label: __('Details'),
      icon: DetailsIcon,
      condition: () => isMobileView.value,
    },
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
      name: 'Calls',
      label: __('Calls'),
      icon: PhoneIcon,
      condition: () => callEnabled.value,
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
const { tabIndex } = useActiveTabManager(tabs, 'lastDealTab')

const sections = createResource({
  url: 'crm.fcrm.doctype.crm_fields_layout.crm_fields_layout.get_sidepanel_sections',
  cache: ['sidePanelSections', 'CRM Deal'],
  params: { doctype: 'CRM Deal' },
  auto: true,
  transform: (data) => getParsedFields(data),
})

function getParsedFields(sections) {
  sections.forEach((section) => {
    if (section.name == 'contacts_section') return
    section.columns[0].fields.forEach((field) => {
      if (field.name == 'organization') {
        field.create = (value, close) => {
          _organization.value.organization_name = value
          showOrganizationModal.value = true
          close()
        }
        field.link = (org) =>
          router.push({
            name: 'Organization',
            params: { organizationId: org },
          })
      }
    })
  })
  return sections
}

const showContactModal = ref(false)
const _contact = ref({})

function contactOptions(contact) {
  let options = [
    {
      label: __('Delete'),
      icon: 'trash-2',
      onClick: () => removeContact(contact),
    },
  ]

  if (!contact.is_primary) {
    options.push({
      label: __('Set as Primary Contact'),
      icon: h(SuccessIcon, { class: 'h-4 w-4' }),
      onClick: () => setPrimaryContact(contact.name),
    })
  }

  return options
}

async function addContact(contact) {
  if (dealContacts.data?.find((c) => c.name === contact)) {
    toast.error(__('Contact Already Added'))
    return
  }

  let d = await call('crm.fcrm.doctype.crm_deal.crm_deal.add_contact', {
    deal: props.dealId,
    contact,
  })
  if (d) {
    dealContacts.reload()
    toast.success(__('Contact Added'))
  }
}

async function removeContact(contact) {
  let d = await call('crm.fcrm.doctype.crm_deal.crm_deal.remove_contact', {
    deal: props.dealId,
    contact,
  })
  if (d) {
    dealContacts.reload()
    toast.success(__('Contact Removed'))
  }
}

async function setPrimaryContact(contact) {
  let d = await call('crm.fcrm.doctype.crm_deal.crm_deal.set_primary_contact', {
    deal: props.dealId,
    contact,
  })
  if (d) {
    dealContacts.reload()
    toast.success(__('Primary Contact Set'))
  }
}

const dealContacts = createResource({
  url: 'crm.fcrm.doctype.crm_deal.api.get_deal_contacts',
  params: { name: props.dealId },
  cache: ['deal_contacts', props.dealId],
  auto: true,
  onSuccess: (data) => {
    let contactSection = sections.data?.find(
      (section) => section.name == 'contacts_section',
    )
    if (!contactSection) return
    contactSection.contacts = data.map((contact) => {
      return {
        name: contact.name,
        full_name: contact.full_name,
        email: contact.email,
        mobile_no: contact.mobile_no,
        image: contact.image,
        is_primary: contact.is_primary,
        opened: false,
      }
    })
  },
})

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

function deleteDeal() {
  showDeleteLinkedDocModal.value = true
}

function statusLabel(status) {
  if (isTranslatable('CRM Deal Status')) return __(status)
  return status
}

// The action that owns a transition runs it — for everyone, Admins included. Whoever
// changes the status, the note, the task and the deal fields that belong with the change
// are recorded. A bare write would reach "Session Set" with no BAP details at all, and
// would reach "Lost" with no reason, where CRMDeal.validate_lost_reason simply throws.
//
// The Admin hatch is for moves the state machine does NOT describe: only when no action
// covers this edge does an Admin write directly. A non-Admin is refused there.
async function triggerStatusChange(value) {
  const candidates = await refreshCandidateActions({
    transitions: transitionMap.data?.transitions,
    pipeline: doc.value?.pipeline_type,
    from: doc.value?.status,
    to: value,
    loadAvailable: () =>
      call('crm.txb.api.actions.get_available_actions', {
        deal: props.dealId,
      }),
  })

  if (candidates.length) {
    const action = await chooseAction(candidates, value)
    if (!action) return
    await onTakeAction(action, prefillFor(action, value))
    return
  }

  if (!isAdmin.value) {
    toast.error(
      __('"{0}" cannot be reached from "{1}".', [
        __(value),
        __(doc.value?.status),
      ]),
    )
    return
  }

  await triggerOnChange('status', value)
  setLostReason()
}

const showLostReasonModal = ref(false)

function setLostReason() {
  if (
    getDealStatus(doc.value.status).type !== 'Lost' ||
    (doc.value.lost_reason && doc.value.lost_reason !== 'Other') ||
    (doc.value.lost_reason === 'Other' && doc.value.lost_notes)
  ) {
    document.save.submit()
    return
  }

  showLostReasonModal.value = true
}

function beforeStatusChange(data) {
  if (
    Object.hasOwn(data ?? {}, 'status') &&
    getDealStatus(data.status).type == 'Lost'
  ) {
    setLostReason()
  } else {
    document.save.submit(null, {
      onSuccess: () => reloadAssignees(data),
    })
  }
}

function reloadAssignees(data) {
  if (Object.hasOwn(data ?? {}, 'deal_owner')) {
    assignees.reload()
  }
}
</script>

<template>
  <!-- Read-only aggregate mode (Contact) suppresses every composer/"New" affordance: the
       header is the entry point for emails, comments, notes, tasks, attachments, events and
       WhatsApp, so it is not rendered at all rather than gated button-by-button. -->
  <ActivityHeader
    v-if="!readOnly"
    v-model="tabIndex"
    v-model:showWhatsappTemplates="showWhatsappTemplates"
    v-model:showFilesUploader="showFilesUploader"
    v-model:emailBox="emailBox"
    :tabs="tabs"
    :title="title"
    :doc="doc"
    :whatsappBox="whatsappBox"
    :modalRef="modalRef"
  />
  <FadedScrollableDiv class="flex flex-col h-full overflow-y-auto">
    <div
      v-if="all_activities?.loading"
      class="flex flex-1 flex-col items-center justify-center gap-3 text-2xl-medium text-ink-gray-4"
    >
      <LoadingIndicator class="h-6 w-6" />
      <span>{{ __('Loading...') }}</span>
    </div>
    <div v-else-if="title == 'Events'" class="h-full activity">
      <EventArea :doctype="doctype" :docname="docname" />
    </div>
    <div
      v-else-if="
        activities?.length ||
        (whatsappMessages.data?.length && title == 'WhatsApp')
      "
      class="activities"
    >
      <div v-if="title == 'WhatsApp' && whatsappMessages.data?.length">
        <WhatsAppArea
          v-model="whatsappMessages"
          v-model:reply="replyMessage"
          class="px-3 sm:px-10"
          :messages="whatsappMessages.data"
        />
      </div>
      <div
        v-else-if="title == 'Notes'"
        class="grid grid-cols-1 gap-4 px-3 pb-3 sm:px-10 sm:pb-5 lg:grid-cols-2 xl:grid-cols-3"
      >
        <div
          v-for="note in activities"
          :key="note.name"
          @click="modalRef.showNote(note)"
        >
          <NoteArea v-model="all_activities" :note="note" />
        </div>
      </div>
      <div v-else-if="title == 'Comments'" class="pb-5">
        <div v-for="(comment, i) in activities" :key="comment.name">
          <div
            class="activity grid grid-cols-[30px_minmax(auto,_1fr)] gap-2 px-3 sm:gap-4 sm:px-10"
          >
            <div
              class="z-0 relative flex justify-center before:absolute before:left-[50%] before:-z-[1] before:top-0 before:border-l before:border-outline-elevation-2"
              :class="
                i != activities.length - 1 ? 'before:h-full' : 'before:h-4'
              "
            >
              <div
                class="flex h-8 w-7 items-center justify-center bg-surface-base"
              >
                <CommentIcon class="text-ink-gray-8" />
              </div>
            </div>
            <CommentArea
              class="mb-4"
              :activity="comment"
              @reload="all_activities.reload()"
            />
          </div>
        </div>
      </div>
      <div v-else-if="title == 'Tasks'" class="px-3 pb-3 sm:px-10 sm:pb-5">
        <TaskArea :modalRef="modalRef" :tasks="activities" :doctype="doctype" />
      </div>
      <div v-else-if="title == 'Calls'" class="activity">
        <div v-for="(call, i) in activities" :key="call.name">
          <div
            class="activity grid grid-cols-[30px_minmax(auto,_1fr)] gap-4 px-3 sm:px-10"
          >
            <div
              class="z-0 relative flex justify-center before:absolute before:left-[50%] before:-z-[1] before:top-0 before:border-l before:border-outline-elevation-2"
              :class="
                i != activities.length - 1 ? 'before:h-full' : 'before:h-4'
              "
            >
              <div
                class="flex h-8 w-7 items-center justify-center bg-surface-base text-ink-gray-8"
              >
                <MissedCallIcon
                  v-if="call.status == 'No Answer'"
                  class="text-ink-red-8"
                />
                <DeclinedCallIcon v-else-if="call.status == 'Busy'" />
                <component
                  :is="
                    call.type == 'Incoming' ? InboundCallIcon : OutboundCallIcon
                  "
                  v-else
                />
              </div>
            </div>
            <CallArea
              class="mb-4"
              :activity="call"
              :hideDuration="hideCallDuration(doctype)"
            />
          </div>
        </div>
      </div>
      <div
        v-else-if="title == 'Attachments'"
        class="px-3 pb-3 sm:px-10 sm:pb-5"
      >
        <AttachmentArea
          :attachments="activities"
          @reload="all_activities.reload() && scroll()"
        />
      </div>
      <template v-else>
        <template v-for="(activity, i) in activities" :key="activity.name">
          <!-- Aggregate Contact log: attribute each row to its archived Lead / linked
               Opportunity source and pre/post-conversion phase above the entry. -->
          <div
            v-if="readOnly"
            class="px-3 pt-3 sm:px-10"
          >
            <ActivitySourceBadge :activity="activity" :leadEmails="leadEmails" />
          </div>
          <div
            class="activity px-3 sm:px-10"
            :class="
              ['Activity', 'Emails'].includes(title)
                ? 'grid grid-cols-[30px_minmax(auto,_1fr)] gap-2 sm:gap-4'
                : ''
            "
          >
          <div
            v-if="['Activity', 'Emails'].includes(title)"
            class="z-0 relative flex justify-center before:absolute before:left-[50%] before:-z-[1] before:top-0 before:border-l before:border-outline-elevation-2"
            :class="[
              i != activities.length - 1 ? 'before:h-full' : 'before:h-4',
            ]"
          >
            <div
              class="flex h-7 w-7 items-center justify-center bg-surface-base"
              :class="{
                'mt-2.5': ['communication'].includes(activity.activity_type),
                'bg-surface-base': ['added', 'removed', 'changed'].includes(
                  activity.activity_type,
                ),
                'h-8': [
                  'comment',
                  'communication',
                  'incoming_call',
                  'outgoing_call',
                ].includes(activity.activity_type),
              }"
            >
              <UserAvatar
                v-if="activity.activity_type == 'communication'"
                :user="activity.data.sender"
                size="md"
              />
              <MissedCallIcon
                v-else-if="
                  ['incoming_call', 'outgoing_call'].includes(
                    activity.activity_type,
                  ) && activity.status == 'No Answer'
                "
                class="text-ink-red-8"
              />
              <DeclinedCallIcon
                v-else-if="
                  ['incoming_call', 'outgoing_call'].includes(
                    activity.activity_type,
                  ) && activity.status == 'Busy'
                "
              />
              <component
                :is="activity.icon"
                v-else
                :class="
                  ['added', 'removed', 'changed'].includes(
                    activity.activity_type,
                  )
                    ? 'text-ink-gray-4'
                    : 'text-ink-gray-8'
                "
              />
            </div>
          </div>
          <div
            v-if="activity.activity_type == 'communication'"
            class="pb-5 mt-px"
          >
            <EmailArea
              :activity="activity"
              :emailBox="emailBox || {}"
              :readOnly="readOnly"
            />
          </div>
          <div
            v-else-if="activity.activity_type == 'comment'"
            :id="activity.name"
            class="mb-4"
          >
            <CommentArea
              :activity="activity"
              :readOnly="readOnly"
              @reload="all_activities.reload()"
            />
          </div>
          <div
            v-else-if="activity.activity_type == 'attachment_log'"
            :id="activity.name"
            class="mb-4 flex flex-col gap-2 py-1.5"
          >
            <div class="flex items-center justify-stretch gap-2 text-base">
              <div
                class="inline-flex items-center flex-wrap gap-1.5 text-ink-gray-8 font-medium"
              >
                <span class="font-medium">{{ activity.owner_name }}</span>
                <span class="text-ink-gray-5">{{
                  __(activity.data.type)
                }}</span>
                <a
                  v-if="activity.data.file_url"
                  :href="activity.data.file_url"
                  target="_blank"
                >
                  <span>{{ activity.data.file_name }}</span>
                </a>
                <span v-else>{{ activity.data.file_name }}</span>
                <span
                  v-if="activity.data.is_private"
                  class="lucide-lock size-3"
                  aria-hidden="true"
                />
              </div>
              <div class="ml-auto whitespace-nowrap">
                <TimelineTimestamp :date="activity.creation" />
              </div>
            </div>
          </div>
          <div
            v-else-if="
              activity.activity_type == 'incoming_call' ||
              activity.activity_type == 'outgoing_call'
            "
            class="mb-4"
          >
            <CallArea
              :activity="activity"
              :hideDuration="hideCallDuration(doctype)"
            />
          </div>
          <div
            v-else-if="activity.activity_type == 'event'"
            :id="activity.name"
            class="mb-4 flex flex-col gap-2 py-1.5"
          >
            <div class="flex items-center justify-stretch gap-2 text-base">
              <div
                class="inline-flex items-center flex-wrap gap-1 text-ink-gray-5"
              >
                <span class="font-medium text-ink-gray-8">
                  {{ activity.owner_name }}
                </span>
                <span>{{ meetingVerb(activity) }}</span>
                <button
                  type="button"
                  class="max-w-xs truncate font-medium text-ink-gray-8 hover:underline"
                  @click="openEventActivity(activity)"
                >
                  {{ activity.summary || activity.data?.subject }}
                </button>
              </div>
              <div class="ml-auto whitespace-nowrap">
                <TimelineTimestamp :date="activity.creation" />
              </div>
            </div>
          </div>
          <div
            v-else-if="activity.activity_type == 'note'"
            :id="activity.name"
            class="mb-4 flex flex-col gap-2 py-1.5"
          >
            <div class="flex items-center justify-stretch gap-2 text-base">
              <div
                class="inline-flex items-center flex-wrap gap-1 text-ink-gray-5"
              >
                <span class="font-medium text-ink-gray-8">
                  {{ activity.owner_name }}
                </span>
                <span>{{ __('added a note') }}</span>
                <button
                  type="button"
                  class="max-w-xs truncate font-medium text-ink-gray-8"
                  :class="readOnly ? '' : 'hover:underline'"
                  :disabled="readOnly"
                  @click="openNoteActivity(activity)"
                >
                  {{ activity.summary || activity.data?.title }}
                </button>
              </div>
              <div class="ml-auto whitespace-nowrap">
                <TimelineTimestamp :date="activity.creation" />
              </div>
            </div>
          </div>
          <div v-else class="mb-4 flex flex-col gap-2 py-1.5">
            <div class="flex items-center justify-stretch gap-2 text-base">
              <div
                v-if="activity.other_versions"
                class="inline-flex flex-wrap gap-1.5 text-ink-gray-8 font-medium"
              >
                <span>{{
                  activity.show_others ? __('Hide') : __('Show')
                }}</span>
                <span> +{{ activity.other_versions.length + 1 }} </span>
                <span>{{ __('changes from') }}</span>
                <span>{{ activity.owner_name }}</span>
                <Button
                  class="!size-4"
                  variant="ghost"
                  :icon="SelectIcon"
                  @click="activity.show_others = !activity.show_others"
                />
              </div>
              <div
                v-else
                class="inline-flex items-center flex-wrap gap-1 text-ink-gray-5"
              >
                <span class="font-medium text-ink-gray-8">
                  {{ activity.owner_name }}
                </span>
                <span v-if="activity.type">{{ __(activity.type) }}</span>
                <span
                  v-if="activity.data?.field_label"
                  class="max-w-xs truncate font-medium text-ink-gray-8"
                >
                  {{ __(activity.data.field_label) }}
                </span>
                <span v-if="activity.value">{{ __(activity.value) }}</span>
                <span
                  v-if="activity.data?.old_value"
                  class="max-w-xs font-medium text-ink-gray-8"
                >
                  <div
                    v-if="activity.options == 'User'"
                    class="flex items-center gap-1"
                  >
                    <UserAvatar :user="activity.data.old_value" size="xs" />
                    {{ getUser(activity.data.old_value).full_name }}
                  </div>
                  <div v-else class="truncate">
                    {{ activity.data.old_value }}
                  </div>
                </span>
                <span v-if="activity.to">{{ __('to') }}</span>
                <span
                  v-if="activity.data?.value"
                  class="max-w-xs font-medium text-ink-gray-8"
                >
                  <div
                    v-if="activity.options == 'User'"
                    class="flex items-center gap-1"
                  >
                    <UserAvatar :user="activity.data.value" size="xs" />
                    {{ getUser(activity.data.value).full_name }}
                  </div>
                  <div v-else class="truncate">
                    {{ activity.data.value }}
                  </div>
                </span>
                <!-- TXB-133: a Task lifecycle row opens its canonical CRM Task so the full detail
                     stays in the specialized Tasks module. Suppressed in the read-only aggregate. -->
                <button
                  v-if="!readOnly && isTaskActivity(activity)"
                  type="button"
                  class="font-medium text-ink-gray-8 hover:underline"
                  @click="openTaskActivity(activity)"
                >
                  {{ __('Open task') }}
                </button>
              </div>

              <div class="ml-auto whitespace-nowrap">
                <TimelineTimestamp :date="activity.creation" />
              </div>
            </div>
            <div
              v-if="activity.other_versions && activity.show_others"
              class="flex flex-col gap-0.5"
            >
              <div
                v-for="a in sortByCreation([
                  activity,
                  ...activity.other_versions,
                ])"
                :key="a.creation"
                class="flex items-start justify-stretch gap-2 py-1.5 text-base"
              >
                <div class="inline-flex flex-wrap gap-1 text-ink-gray-5">
                  <span
                    v-if="a.data?.field_label"
                    class="max-w-xs truncate text-ink-gray-5"
                  >
                    {{ __(a.data.field_label) }}
                  </span>
                  <span
                    class="lucide-arrow-right mx-1 h-4 w-4 text-ink-gray-5"
                    aria-hidden="true"
                  />
                  <span v-if="a.type">
                    {{ startCase(__(a.type)) }}
                  </span>
                  <span
                    v-if="a.data?.old_value"
                    class="max-w-xs font-medium text-ink-gray-8"
                  >
                    <div
                      v-if="a.options == 'User'"
                      class="flex items-center gap-1"
                    >
                      <UserAvatar :user="a.data.old_value" size="xs" />
                      {{ getUser(a.data.old_value).full_name }}
                    </div>
                    <div v-else class="truncate">
                      {{ a.data.old_value }}
                    </div>
                  </span>
                  <span v-if="a.to">{{ __('to') }}</span>
                  <span
                    v-if="a.data?.value"
                    class="max-w-xs font-medium text-ink-gray-8"
                  >
                    <div
                      v-if="a.options == 'User'"
                      class="flex items-center gap-1"
                    >
                      <UserAvatar :user="a.data.value" size="xs" />
                      {{ getUser(a.data.value).full_name }}
                    </div>
                    <div v-else class="truncate">
                      {{ a.data.value }}
                    </div>
                  </span>
                </div>

                <div class="ml-auto whitespace-nowrap">
                  <TimelineTimestamp :date="a.creation" />
                </div>
              </div>
            </div>
          </div>
        </div>
        </template>
      </template>
    </div>
    <div v-else-if="title == 'Data'" class="h-full flex flex-col px-3 sm:px-10">
      <DataFields
        :doctype="doctype"
        :docname="docname"
        @beforeSave="(data) => emit('beforeSave', data)"
        @afterSave="(data) => emit('afterSave', data)"
      />
    </div>
    <EmptyState
      v-else
      :title="emptyText"
      :description="emptyTextDescription"
      :icon="emptyTextIcon"
      :top="top"
    />
  </FadedScrollableDiv>
  <div v-if="!readOnly">
    <CommunicationArea
      v-if="['Emails', 'Comments', 'Activity'].includes(title)"
      ref="emailBox"
      v-model="doc"
      v-model:reload="reload_email"
      :doctype="doctype"
      @scroll="scroll"
    />
    <WhatsAppBox
      v-if="title == 'WhatsApp'"
      ref="whatsappBox"
      v-model="doc"
      v-model:reply="replyMessage"
      v-model:whatsapp="whatsappMessages"
      :doctype="doctype"
      @scroll="scroll"
    />
  </div>
  <WhatsappTemplateSelectorModal
    v-if="whatsappEnabled && !readOnly"
    v-model="showWhatsappTemplates"
    :doctype="doctype"
    @send="(t) => sendTemplate(t)"
  />
  <AllModals
    v-if="!readOnly"
    ref="modalRef"
    v-model="all_activities"
    :doctype="doctype"
    :doc="doc"
    @refreshDocument="_document.reload()"
  />
  <FilesUploader
    v-if="!readOnly"
    v-model="showFilesUploader"
    :doctype="doctype"
    :docname="docname"
    @after="
      () => {
        all_activities.reload()
        changeTabTo('attachments')
      }
    "
  />
</template>
<script setup>
import ActivityHeader from '@/components/Activities/ActivityHeader.vue'
import EmailArea from '@/components/Activities/EmailArea.vue'
import CommentArea from '@/components/Activities/CommentArea.vue'
import CallArea from '@/components/Activities/CallArea.vue'
import NoteArea from '@/components/Activities/NoteArea.vue'
import TaskArea from '@/components/Activities/TaskArea.vue'
import AttachmentArea from '@/components/Activities/AttachmentArea.vue'
import DataFields from '@/components/Activities/DataFields.vue'
import UserAvatar from '@/components/UserAvatar.vue'
import ActivityIcon from '@/components/Icons/ActivityIcon.vue'
import EmailIcon from '@/components/Icons/EmailIcon.vue'
import DetailsIcon from '@/components/Icons/DetailsIcon.vue'
import CalendarIcon from '@/components/Icons/CalendarIcon.vue'
import PhoneIcon from '@/components/Icons/PhoneIcon.vue'
import NoteIcon from '@/components/Icons/NoteIcon.vue'
import TaskIcon from '@/components/Icons/TaskIcon.vue'
import AttachmentIcon from '@/components/Icons/AttachmentIcon.vue'
import WhatsAppIcon from '@/components/Icons/WhatsAppIcon.vue'
import EventArea from '@/components/Activities/EventArea.vue'
import WhatsAppArea from '@/components/Activities/WhatsAppArea.vue'
import WhatsAppBox from '@/components/Activities/WhatsAppBox.vue'
import LoadingIndicator from '@/components/Icons/LoadingIndicator.vue'
import EmptyState from '@/components/ListViews/EmptyState.vue'
import LeadsIcon from '@/components/Icons/LeadsIcon.vue'
import DealsIcon from '@/components/Icons/DealsIcon.vue'
import DotIcon from '@/components/Icons/DotIcon.vue'
import CommentIcon from '@/components/Icons/CommentIcon.vue'
import SelectIcon from '@/components/Icons/SelectIcon.vue'
import MissedCallIcon from '@/components/Icons/MissedCallIcon.vue'
import DeclinedCallIcon from '@/components/Icons/DeclinedCallIcon.vue'
import InboundCallIcon from '@/components/Icons/InboundCallIcon.vue'
import OutboundCallIcon from '@/components/Icons/OutboundCallIcon.vue'
import FadedScrollableDiv from '@/components/FadedScrollableDiv.vue'
import CommunicationArea from '@/components/CommunicationArea.vue'
import WhatsappTemplateSelectorModal from '@/components/Modals/WhatsappTemplateSelectorModal.vue'
import AllModals from '@/components/Activities/AllModals.vue'
import FilesUploader from '@/components/FilesUploader/FilesUploader.vue'
import TimelineTimestamp from '@/components/Activities/TimelineTimestamp.vue'
import ActivitySourceBadge from '@/components/Activities/ActivitySourceBadge.vue'
import { leadSourceNames } from '@/utils/contactActivity'
import { startCase } from '@/utils'
import { hideCallDuration } from '@/utils/dealPresentation'
import { sortByCreation } from '@/utils/activityOrdering'
import { globalStore } from '@/stores/global'
import { usersStore } from '@/stores/users'
import { useEvent, showEventModal, activeEvent } from '@/composables/event'
import { whatsappEnabled } from '@/composables/whatsapp'
import { useDocument } from '@/data/document'
import { useTelemetry } from 'frappe-ui/frappe'
import { Button, createResource, toast } from 'frappe-ui'
import { useElementVisibility } from '@vueuse/core'
import {
  ref,
  computed,
  h,
  markRaw,
  watch,
  nextTick,
  onMounted,
  onBeforeUnmount,
} from 'vue'
import { useRoute } from 'vue-router'

const { $socket } = globalStore()
const { getUser } = usersStore()
const { capture } = useTelemetry()

const props = defineProps({
  doctype: { type: String, default: 'CRM Lead' },
  docname: { type: String, default: '' },
  tabs: { type: Array, default: () => [] },
  // Read-only aggregate contract (Contact). When true the timeline renders the deduplicated
  // cross-source history with per-row source/phase attribution and suppresses every
  // Contact-side mutation control (composers, uploads, edits, deletes). Lead/Deal callers
  // leave this false and keep their existing editable behavior unchanged.
  readOnly: { type: Boolean, default: false },
})

const emit = defineEmits(['beforeSave', 'afterSave'])

const route = useRoute()

const reload = defineModel('reload', { type: Boolean, default: false })
const tabIndex = defineModel('tabIndex', { type: Number, default: 0 })

const { document: _document } = useDocument(props.doctype, props.docname)

const doc = computed(() => _document.doc || {})

const reload_email = ref(false)
const modalRef = ref(null)
const showFilesUploader = ref(false)

const title = computed(() => props.tabs?.[tabIndex.value]?.name || 'Activity')

const changeTabTo = (tabName) => {
  const tabNames = props.tabs?.map((tab) => tab.name?.toLowerCase())
  const index = tabNames?.indexOf(tabName)
  if (index == -1) return
  tabIndex.value = index
}

const all_activities = createResource({
  url: 'crm.api.activities.get_activities',
  params: { name: props.docname },
  cache: ['activity', props.docname],
  auto: true,
  transform: ([versions, calls, notes, tasks, attachments]) => {
    return { versions, calls, notes, tasks, attachments }
  },
  onSuccess: () => nextTick(() => scroll()),
})

// Aggregate Contact log: distinct archived-Lead sources across every loaded stream, so the
// per-row source badge can prefer a human email label over the immutable Lead docname.
const leadSourceDocnames = computed(() => {
  if (!props.readOnly || !all_activities.data) return []
  const { versions, calls, notes, tasks, attachments } = all_activities.data
  return leadSourceNames([
    ...(versions || []),
    ...(calls || []),
    ...(notes || []),
    ...(tasks || []),
    ...(attachments || []),
  ])
})

// Best-effort email resolution: a Lead the user cannot read simply keeps its docname label
// (activitySource falls back), so a failed or partial lookup never breaks the read-only log.
const leadEmailsResource = createResource({
  url: 'frappe.client.get_list',
  makeParams: () => ({
    doctype: 'CRM Lead',
    filters: [['name', 'in', leadSourceDocnames.value]],
    fields: ['name', 'email'],
    limit_page_length: 0,
  }),
})

watch(
  leadSourceDocnames,
  (names) => {
    if (names?.length) leadEmailsResource.reload()
  },
  { immediate: true },
)

const leadEmails = computed(() => {
  const map = {}
  for (const row of leadEmailsResource.data || []) {
    if (row.email) map[row.name] = row.email
  }
  return map
})

const showWhatsappTemplates = ref(false)

const whatsappMessages = createResource({
  url: 'crm.api.whatsapp.get_whatsapp_messages',
  cache: ['whatsapp_messages', props.docname],
  params: {
    reference_doctype: props.doctype,
    reference_name: props.docname,
  },
  auto: false,
  transform: (data) => sortByCreation(data),
  onSuccess: () => nextTick(() => scroll()),
})

watch(
  whatsappEnabled,
  (enabled) => {
    if (enabled) whatsappMessages.fetch()
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  $socket.off('whatsapp_message')
  $socket.off('docinfo_update', handleDocinfoUpdate)
  $socket.emit('doc_unsubscribe', props.doctype, props.docname)
})

onMounted(() => {
  $socket.emit('doc_subscribe', props.doctype, props.docname)
  $socket.on('docinfo_update', handleDocinfoUpdate)
  $socket.on('whatsapp_message', (data) => {
    if (
      data.reference_doctype === props.doctype &&
      data.reference_name === props.docname
    ) {
      whatsappMessages.reload()
    }
  })

  nextTick(() => {
    const hash = route.hash.slice(1) || null
    let tabNames = props.tabs?.map((tab) => tab.name)
    if (!tabNames?.includes(hash)) {
      scroll(hash)
    }
  })
})

function handleDocinfoUpdate({ doc, key }) {
  if (key !== 'comments') return
  if (doc.reference_doctype !== props.doctype) return
  if (doc.reference_name !== props.docname) return

  all_activities.reload()
  _document.reload()
}

function sendTemplate(template) {
  showWhatsappTemplates.value = false
  capture('send_whatsapp_template', { doctype: props.doctype })
  createResource({
    url: 'crm.api.whatsapp.send_whatsapp_template',
    params: {
      reference_doctype: props.doctype,
      reference_name: props.docname,
      to: doc.value.mobile_no,
      template,
    },
    auto: true,
    onError: (error) => {
      toast.error(error.messages?.[0] || __('Failed to send WhatsApp template'))
    },
    onSuccess: () => whatsappMessages.reload(),
  })
}

const replyMessage = ref({})

function get_activities() {
  if (!all_activities.data?.versions) return []
  if (!all_activities.data?.calls.length)
    return all_activities.data.versions || []
  return [...all_activities.data.versions, ...all_activities.data.calls]
}

const activities = computed(() => {
  let _activities = []
  if (title.value == 'Activity') {
    _activities = get_activities()
  } else if (title.value == 'Emails') {
    if (!all_activities.data?.versions) return []
    _activities = all_activities.data.versions.filter(
      (activity) => activity.activity_type === 'communication',
    )
  } else if (title.value == 'Comments') {
    if (!all_activities.data?.versions) return []
    _activities = all_activities.data.versions.filter(
      (activity) => activity.activity_type === 'comment',
    )
  } else if (title.value == 'Calls') {
    if (!all_activities.data?.calls) return []
    return sortByCreation(all_activities.data.calls)
  } else if (title.value == 'Tasks') {
    if (!all_activities.data?.tasks) return []
    return sortByModified(all_activities.data.tasks)
  } else if (title.value == 'Notes') {
    if (!all_activities.data?.notes) return []
    return sortByModified(all_activities.data.notes)
  } else if (title.value == 'Attachments') {
    if (!all_activities.data?.attachments) return []
    return sortByModified(all_activities.data.attachments)
  }

  _activities.forEach((activity) => {
    // A normalized Task lifecycle event reuses the generic creation/field-change activity_type, so
    // key its timeline icon off its canonical CRM Task target rather than the deal/lead default.
    activity.icon = isTaskActivity(activity)
      ? markRaw(TaskIcon)
      : timelineIcon(activity.activity_type, activity.is_lead)

    if (
      activity.activity_type == 'incoming_call' ||
      activity.activity_type == 'outgoing_call' ||
      activity.activity_type == 'communication'
    )
      return

    update_activities_details(activity)

    if (activity.other_versions) {
      // Default grouped changes to expanded, but only initialize once so a user's
      // manual collapse survives routine reactive recomputation of this timeline.
      if (activity.show_others === undefined) activity.show_others = true
      activity.other_versions.forEach((other_version) => {
        update_activities_details(other_version)
      })
    }
  })
  return sortByCreation(_activities)
})

function sortByModified(list) {
  return list.sort((b, a) => new Date(a.modified) - new Date(b.modified))
}

function update_activities_details(activity) {
  activity.owner_name = getUser(activity.owner).full_name
  activity.type = ''
  activity.value = ''
  activity.to = ''

  if (activity.activity_type == 'creation') {
    activity.type = activity.data
  } else if (activity.activity_type == 'added') {
    activity.type = 'added'
    activity.value = 'as'
  } else if (activity.activity_type == 'removed') {
    activity.type = 'removed'
    activity.value = 'value'
  } else if (activity.activity_type == 'changed') {
    activity.type = 'changed'
    activity.value = 'from'
    activity.to = 'to'
  }
}

// TXB-186: the normalized backend labels each meeting event with a lifecycle `meeting_action`;
// map it to a human verb here so the row reads "<actor> <verb> <meeting subject>".
const MEETING_VERBS = {
  scheduled: 'scheduled a meeting',
  rescheduled: 'rescheduled the meeting',
  completed: 'completed the meeting',
  cancelled: 'cancelled the meeting',
  status_changed: 'updated the meeting',
}
function meetingVerb(activity) {
  return __(MEETING_VERBS[activity.data?.meeting_action] || MEETING_VERBS.status_changed)
}

// Hydrate the canonical Event so a feed row opens the same modal as the Events tab. The event
// composable already reads this Lead/Opportunity's linked Events (with participants); the feed row
// only carries the open target, so resolve the full record by its canonical name.
const { events: linkedEvents } = useEvent({
  doctype: props.doctype,
  docname: props.docname,
})
function openEventActivity(activity) {
  const eventName = activity.target?.name || activity.canonical_docname
  const full = (linkedEvents.value || []).find((e) => e.name === eventName)
  if (!full) return
  activeEvent.value = full
  showEventModal.value = true
}

// Open the canonical Note in the authoritative Notes module; the feed entry is metadata only.
function openNoteActivity(activity) {
  if (props.readOnly) return
  const noteName = activity.target?.name || activity.canonical_docname
  modalRef.value?.showNote({ name: noteName })
}

// TXB-133: a Task lifecycle event (creation or a tracked field change) is normalized into the
// Activity stream with its canonical home on the CRM Task. The feed row only labels the moment; it
// opens the authoritative Task record so its full detail stays in the specialized Tasks module.
function isTaskActivity(activity) {
  return activity.target?.doctype === 'CRM Task'
}
function openTaskActivity(activity) {
  if (props.readOnly) return
  const taskName = activity.target?.name || activity.canonical_docname
  modalRef.value?.showTask({ name: taskName })
}

const top = computed(() => {
  if (['Activity', 'Emails', 'Comments'].includes(title.value)) {
    return '32.3%'
  }
  return '30%'
})

const emptyText = computed(() => {
  let text = 'No Activities Found'
  if (title.value == 'Emails') {
    text = 'No Emails Found'
  } else if (title.value == 'Comments') {
    text = 'No Comments Found'
  } else if (title.value == 'Data') {
    text = 'No Data Fields Added Yet'
  } else if (title.value == 'Calls') {
    text = 'No Call History'
  } else if (title.value == 'Notes') {
    text = 'No Notes Found'
  } else if (title.value == 'Tasks') {
    text = 'No Tasks Found'
  } else if (title.value == 'Attachments') {
    text = 'No Attachments Found'
  } else if (title.value == 'WhatsApp') {
    text = 'No WhatsApp Messages Found'
  }
  return text
})

const emptyTextDescription = computed(() => {
  let description =
    'There are no activities to display here. Go ahead and make some changes.'
  if (title.value == 'Emails') {
    description =
      'No emails found in your inbox. New messages will appear here soon.'
  } else if (title.value == 'Comments') {
    description = 'Be the first to add one.'
  } else if (title.value == 'Data') {
    description = 'No data fields have been added yet.'
  } else if (title.value == 'Calls') {
    description = 'No recent calls to display. Log a call or call someone now!'
  } else if (title.value == 'Notes') {
    description = 'Nothing here for now. Add a note to keep track of things.'
  } else if (title.value == 'Tasks') {
    description =
      'Nothing to do at the moment. Start organizing by adding one here.'
  } else if (title.value == 'Attachments') {
    description =
      'No files have been attached yet. Upload files to see them here.'
  } else if (title.value == 'WhatsApp') {
    description = 'Start a conversation now!'
  }
  return description
})

const emptyTextIcon = computed(() => {
  let icon = ActivityIcon
  if (title.value == 'Emails') {
    icon = EmailIcon
  } else if (title.value == 'Comments') {
    icon = CommentIcon
  } else if (title.value == 'Data') {
    icon = DetailsIcon
  } else if (title.value == 'Calls') {
    icon = PhoneIcon
  } else if (title.value == 'Notes') {
    icon = NoteIcon
  } else if (title.value == 'Tasks') {
    icon = TaskIcon
  } else if (title.value == 'Attachments') {
    icon = AttachmentIcon
  } else if (title.value == 'WhatsApp') {
    icon = WhatsAppIcon
  }
  return h(icon, { class: 'text-ink-gray-4' })
})

function timelineIcon(activity_type, is_lead) {
  let icon
  switch (activity_type) {
    case 'creation':
      icon = is_lead ? LeadsIcon : DealsIcon
      break
    case 'deal':
      icon = DealsIcon
      break
    case 'comment':
      icon = CommentIcon
      break
    case 'event':
      icon = CalendarIcon
      break
    case 'note':
      icon = NoteIcon
      break
    case 'incoming_call':
      icon = InboundCallIcon
      break
    case 'outgoing_call':
      icon = OutboundCallIcon
      break
    case 'attachment_log':
      icon = AttachmentIcon
      break
    default:
      icon = DotIcon
  }

  return markRaw(icon)
}

const emailBox = ref(null)
const whatsappBox = ref(null)

watch([reload, reload_email], ([reload_value, reload_email_value]) => {
  if (reload_value || reload_email_value) {
    all_activities.reload()
    _document.reload()
    reload.value = false
    reload_email.value = false
  }
})

function scroll(hash) {
  if (['tasks', 'notes', 'events'].includes(route.hash?.slice(1))) return
  setTimeout(() => {
    let el
    if (!hash) {
      let e = document.getElementsByClassName('activity')
      // The timeline is always newest-first now, so the latest entry is the first node.
      el = e[0]
    } else {
      el = document.getElementById(hash)
    }
    if (el && !useElementVisibility(el).value) {
      el.scrollIntoView({ behavior: 'smooth' })
      el.focus()
    }
  }, 500)
}

defineExpose({ emailBox, all_activities, changeTabTo })
</script>

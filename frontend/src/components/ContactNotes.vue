<template>
  <!-- Read-only Contact Notes workspace (TXB-132). Consumes the same deduplicated Contact
       aggregate activity stream as the Activity timeline (crm.api.activities.get_activities ->
       get_contact_activities) and presents only its `notes` category as a searchable,
       source-linked, read-only table (desktop) / list (mobile). No create/edit/delete
       affordances are rendered here; those stay Lead/Deal-only. -->
  <div class="flex flex-1 flex-col overflow-hidden">
    <div class="flex items-center gap-2 px-3 pt-4 sm:px-10">
      <TextInput
        v-model="search"
        class="w-full sm:w-80"
        type="text"
        :placeholder="__('Search notes')"
      >
        <template #prefix>
          <FeatherIcon name="search" class="h-4 w-4 text-ink-gray-4" />
        </template>
      </TextInput>
    </div>

    <!-- Loading -->
    <div
      v-if="contactActivities.loading"
      class="flex flex-1 flex-col items-center justify-center gap-3 text-2xl-medium text-ink-gray-4"
    >
      <LoadingIndicator class="h-6 w-6" />
      <span>{{ __('Loading...') }}</span>
    </div>

    <!-- Empty -->
    <div
      v-else-if="!filteredNotes.length"
      class="flex flex-1 flex-col items-center justify-center gap-3 text-xl-medium text-ink-gray-4"
    >
      <NoteIcon class="h-10 w-10" />
      <span>{{ search ? __('No matching notes') : __('No notes found') }}</span>
    </div>

    <!-- Table (desktop) / list (mobile) -->
    <FadedScrollableDiv v-else class="flex flex-1 flex-col overflow-y-auto">
      <!-- Desktop table -->
      <div class="hidden px-10 py-4 sm:block">
        <div
          class="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)_minmax(0,2fr)_minmax(0,1.5fr)_minmax(0,1.5fr)] gap-3 border-b pb-2 text-sm text-ink-gray-5"
        >
          <div>{{ __('Title') }}</div>
          <div>{{ __('Content') }}</div>
          <div>{{ __('Source') }}</div>
          <div>{{ __('Author') }}</div>
          <div>{{ __('Updated') }}</div>
        </div>
        <div
          v-for="note in filteredNotes"
          :key="note.name"
          class="grid cursor-pointer grid-cols-[minmax(0,2fr)_minmax(0,3fr)_minmax(0,2fr)_minmax(0,1.5fr)_minmax(0,1.5fr)] items-center gap-3 border-b py-3 text-base hover:bg-surface-gray-1"
          @click="openNote(note)"
        >
          <div class="truncate font-medium text-ink-gray-8" :title="note.title">
            {{ note.title || __('Untitled') }}
          </div>
          <div class="truncate text-ink-gray-6" :title="notePreview(note)">
            {{ notePreview(note) }}
          </div>
          <div class="flex min-w-0 flex-wrap items-center gap-1.5" @click.stop>
            <ContactSourceLink :activity="note" :leadEmails="leadEmails" />
            <Badge
              v-if="phaseLabel(note.phase)"
              :label="__(phaseLabel(note.phase))"
              variant="subtle"
              size="sm"
              :theme="note.phase === PHASE_PRE_CONVERSION ? 'gray' : 'green'"
            />
          </div>
          <div class="flex min-w-0 items-center gap-2">
            <UserAvatar :user="note.owner" size="xs" />
            <span
              class="truncate text-ink-gray-7"
              :title="getUser(note.owner).full_name"
            >
              {{ getUser(note.owner).full_name }}
            </span>
          </div>
          <TimelineTimestamp
            :date="note.modified"
            class-name="text-ink-gray-6"
          />
        </div>
      </div>

      <!-- Mobile list -->
      <div class="flex flex-col gap-3 px-3 py-4 sm:hidden">
        <div
          v-for="note in filteredNotes"
          :key="note.name"
          class="flex cursor-pointer flex-col gap-2 rounded-md bg-surface-gray-1 px-4 py-3 hover:bg-surface-gray-2"
          @click="openNote(note)"
        >
          <div class="truncate text-lg-medium text-ink-gray-8">
            {{ note.title || __('Untitled') }}
          </div>
          <div class="line-clamp-2 text-p-sm text-ink-gray-6">
            {{ notePreview(note) }}
          </div>
          <div class="flex flex-wrap items-center gap-1.5" @click.stop>
            <ContactSourceLink :activity="note" :leadEmails="leadEmails" />
            <Badge
              v-if="phaseLabel(note.phase)"
              :label="__(phaseLabel(note.phase))"
              variant="subtle"
              size="sm"
              :theme="note.phase === PHASE_PRE_CONVERSION ? 'gray' : 'green'"
            />
          </div>
          <div class="flex items-center justify-between gap-2">
            <div class="flex min-w-0 items-center gap-2">
              <UserAvatar :user="note.owner" size="xs" />
              <span class="truncate text-sm text-ink-gray-7">
                {{ getUser(note.owner).full_name }}
              </span>
            </div>
            <TimelineTimestamp
              :date="note.modified"
              class-name="text-sm text-ink-gray-6"
            />
          </div>
        </div>
      </div>
    </FadedScrollableDiv>

    <!-- Read-only Note detail -->
    <Dialog v-model="showNoteDetail" :options="{ size: '2xl' }">
      <template #body-title>
        <div class="flex items-center gap-2">
          <NoteIcon class="h-4 w-4 text-ink-gray-7" />
          <span class="text-2xl-medium text-ink-gray-9">
            {{ activeNote?.title || __('Untitled') }}
          </span>
        </div>
      </template>
      <template #body-content>
        <div v-if="activeNote" class="flex flex-col gap-4">
          <div class="flex flex-wrap items-center gap-2 text-sm">
            <ContactSourceLink :activity="activeNote" :leadEmails="leadEmails" />
            <Badge
              v-if="phaseLabel(activeNote.phase)"
              :label="__(phaseLabel(activeNote.phase))"
              variant="subtle"
              size="sm"
              :theme="
                activeNote.phase === PHASE_PRE_CONVERSION ? 'gray' : 'green'
              "
            />
          </div>
          <div class="flex items-center gap-2 text-sm text-ink-gray-7">
            <UserAvatar :user="activeNote.owner" size="xs" />
            <span>{{ getUser(activeNote.owner).full_name }}</span>
            <span class="text-ink-gray-4">&middot;</span>
            <TimelineTimestamp :date="activeNote.modified" />
          </div>
          <TextEditor
            v-if="activeNote.content"
            :content="activeNote.content"
            :editable="false"
            editor-class="prose-sm max-w-none text-ink-gray-8 focus:outline-none"
          />
          <div v-else class="text-base text-ink-gray-5">
            {{ __('This note has no content.') }}
          </div>
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import ContactSourceLink from '@/components/Activities/ContactSourceLink.vue'
import TimelineTimestamp from '@/components/Activities/TimelineTimestamp.vue'
import UserAvatar from '@/components/UserAvatar.vue'
import NoteIcon from '@/components/Icons/NoteIcon.vue'
import LoadingIndicator from '@/components/Icons/LoadingIndicator.vue'
import FadedScrollableDiv from '@/components/FadedScrollableDiv.vue'
import {
  activitySource,
  phaseLabel,
  leadSourceNames,
  PHASE_PRE_CONVERSION,
} from '@/utils/contactActivity'
import { usersStore } from '@/stores/users'
import {
  Badge,
  Dialog,
  FeatherIcon,
  TextEditor,
  TextInput,
  createResource,
} from 'frappe-ui'
import { computed, ref, watch } from 'vue'

const props = defineProps({
  doctype: { type: String, default: 'Contact' },
  docname: { type: String, required: true },
})

const { getUser } = usersStore()

// Shares the exact resource url + cache key with the Activity timeline, so the Notes tab reuses
// the already-deduplicated, source-tagged Contact aggregate stream instead of a second model.
const contactActivities = createResource({
  url: 'crm.api.activities.get_activities',
  params: { name: props.docname },
  cache: ['activity', props.docname],
  auto: true,
  transform: ([versions, calls, notes, tasks, attachments]) => {
    return { versions, calls, notes, tasks, attachments }
  },
})

// Best-effort email labels for archived-Lead sources, mirroring the activity log: a Lead the
// user cannot read simply keeps its docname label, so a partial lookup never breaks the table.
const leadSourceDocnames = computed(() =>
  leadSourceNames(contactActivities.data?.notes || []),
)

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

// Newest first by last-updated timestamp.
const notes = computed(() => {
  const list = [...(contactActivities.data?.notes || [])]
  return list.sort((a, b) => new Date(b.modified) - new Date(a.modified))
})

const search = ref('')

// Plain-text projection of the sanitized note content for both the preview cell and search.
function notePreview(note) {
  if (!note?.content) return ''
  const el = document.createElement('div')
  el.innerHTML = note.content
  return (el.textContent || el.innerText || '').replace(/\s+/g, ' ').trim()
}

function sourceLabel(note) {
  return activitySource(note, leadEmails.value)?.label || ''
}

const filteredNotes = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return notes.value
  return notes.value.filter((note) => {
    const haystack = [
      note.title || '',
      notePreview(note),
      sourceLabel(note),
      getUser(note.owner).full_name || '',
    ]
      .join(' ')
      .toLowerCase()
    return haystack.includes(q)
  })
})

const showNoteDetail = ref(false)
const activeNote = ref(null)

function openNote(note) {
  activeNote.value = note
  showNoteDetail.value = true
}
</script>

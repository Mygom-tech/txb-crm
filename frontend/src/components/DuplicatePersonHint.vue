<template>
  <div
    v-if="visible"
    class="mb-4 rounded-lg border p-3"
    :class="
      hasExact
        ? 'border-outline-red-2 bg-surface-red-1'
        : 'border-outline-gray-2'
    "
  >
    <div class="mb-2 flex items-center gap-2">
      <FeatherIcon
        :name="hasExact ? 'alert-triangle' : 'search'"
        class="h-4 w-4"
        :class="hasExact ? 'text-ink-red-3' : 'text-ink-gray-6'"
      />
      <span class="text-base font-medium text-ink-gray-8">
        {{ headline }}
      </span>
      <Spinner v-if="search.loading" class="h-3.5 w-3.5 text-ink-gray-5" />
    </div>

    <!-- Capped so a wide query cannot push the form itself off screen; the panel
         sits above the fields the user is still typing into. -->
    <ul
      v-if="matches.length"
      class="flex max-h-52 flex-col gap-1 overflow-y-auto"
    >
      <li
        v-for="match in matches"
        :key="`${match.doctype}-${match.name}`"
        class="flex items-center justify-between gap-3 rounded px-2 py-1.5 hover:bg-surface-gray-2"
      >
        <div class="flex min-w-0 items-center gap-2">
          <span
            class="shrink-0 rounded px-1.5 py-0.5 text-xs font-medium"
            :class="
              match.doctype === 'CRM Lead'
                ? 'bg-surface-amber-2 text-ink-amber-3'
                : 'bg-surface-blue-2 text-ink-blue-3'
            "
          >
            {{ match.doctype === 'CRM Lead' ? __('Lead') : __('Contact') }}
          </span>
          <div class="min-w-0">
            <div class="truncate text-base text-ink-gray-8">
              {{ match.full_name }}
              <span v-if="match.strength === 'exact'" class="text-ink-red-3">
                · {{ __('exact match') }}
              </span>
            </div>
            <div class="truncate text-sm text-ink-gray-5">
              {{ subtitle(match) }}
            </div>
          </div>
        </div>
        <Button
          :label="__('Open')"
          variant="ghost"
          class="shrink-0"
          @click="open(match)"
        />
      </li>
    </ul>

    <p v-else-if="!search.loading" class="px-2 text-sm text-ink-gray-5">
      {{ __('Checked both Leads and Contacts — no match found.') }}
    </p>

    <p v-if="hasExact" class="mt-2 px-2 text-sm text-ink-red-3">
      {{
        __(
          'Creating is disabled while an exact email or phone match exists. Open the record above, or change the email and phone.',
        )
      }}
    </p>

    <p v-if="restricted" class="mt-2 px-2 text-sm text-ink-gray-5">
      {{
        __(
          '{0} more matching record(s) exist but are not visible to you. Ask your manager before creating a new one.',
          [restricted],
        )
      }}
    </p>
  </div>
</template>

<script setup>
/**
 * Cross-object duplicate warning shown while a Lead or Contact is being typed
 * (TXB-112). Searches Leads and Contacts together, ignoring the current View and
 * its filters, so an existing person surfaces before Create — not after, as the
 * TXB-73 backend block does.
 *
 * Advisory only. A similar name never blocks anything; the backend stays the
 * boundary.
 */
import { usersStore } from '@/stores/users'
import { Button, FeatherIcon, Spinner, createResource } from 'frappe-ui'
import { watchDebounced } from '@vueuse/core'
import { computed, watch } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  firstName: { type: String, default: '' },
  lastName: { type: String, default: '' },
  email: { type: String, default: '' },
  phone: { type: String, default: '' },
})

const emit = defineEmits(['open'])

/**
 * Mirrors "an exact email or phone match is on screen" to the parent, which
 * disables its Create button. Frontend-only by design: TXB-73 remains the actual
 * boundary, so the Facebook lead sync, the guest registration endpoint and bulk
 * imports keep creating records the UI would refuse.
 */
const blocked = defineModel('blocked', { type: Boolean, default: false })

const router = useRouter()
const { getUser } = usersStore()

// Long enough that a name is meaningful, short enough that the panel appears
// while the user is still on the same field. Mirrors MIN_NAME_LENGTH server-side.
const MIN_NAME_LENGTH = 3
const DEBOUNCE_MS = 400

const query = computed(() => ({
  name: [props.firstName, props.lastName].filter(Boolean).join(' ').trim(),
  email: (props.email || '').trim(),
  phone: (props.phone || '').trim(),
}))

const hasQuery = computed(
  () =>
    query.value.name.length >= MIN_NAME_LENGTH ||
    !!query.value.email ||
    !!query.value.phone,
)

const search = createResource({
  url: 'crm.txb.api.people_search.search_people',
  // A failed lookup must not derail the create flow it is assisting — the panel
  // simply stays empty.
  onError: () => {},
})

const matches = computed(() => search.data?.matches || [])
const restricted = computed(() => search.data?.restricted || 0)
const hasExact = computed(() =>
  matches.value.some((match) => match.strength === 'exact'),
)
const visible = computed(
  () => hasQuery.value && (search.loading || !!search.data),
)

watch(hasExact, (value) => (blocked.value = value), { immediate: true })

const headline = computed(() => {
  if (hasExact.value) return __('This person may already exist')
  if (matches.value.length) return __('Possible matches')
  return __('No existing record found')
})

function subtitle(match) {
  const owner = match.owner ? getUser(match.owner)?.full_name : null
  return [match.email, match.phone, match.status, owner]
    .filter(Boolean)
    .join(' · ')
}

function open(match) {
  emit('open')
  if (match.doctype === 'CRM Lead') {
    router.push({ name: 'Lead', params: { leadId: match.name } })
  } else {
    router.push({ name: 'Contact', params: { contactId: match.name } })
  }
}

watchDebounced(
  query,
  (value) => {
    if (!hasQuery.value) {
      search.reset()
      return
    }
    search.submit(value)
  },
  { deep: true, debounce: DEBOUNCE_MS },
)
</script>

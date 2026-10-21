<template>
  <Dialog
    v-model:open="showGlobalSearch"
    size="2xl"
    position="top"
    :show-close-button="false"
  >
    <div class="flex flex-col gap-2" @keydown="onKeydown">
      <label
        class="flex items-center gap-2 rounded-md border border-outline-gray-2 px-3"
      >
        <LucideSearch class="size-4 text-ink-gray-5" />
        <input
          ref="input"
          v-model="query"
          type="text"
          class="h-10 flex-1 border-0 bg-transparent p-0 text-base text-ink-gray-9 placeholder:text-ink-gray-5 focus:ring-0"
          :placeholder="__('Search leads, contacts, companies and deals')"
          :aria-label="__('Search')"
          autocomplete="off"
        />
        <span
          class="rounded border border-outline-gray-2 px-1.5 text-xs text-ink-gray-5"
        >
          Esc
        </span>
      </label>

      <div class="max-h-[60vh] overflow-y-auto" role="listbox">
        <p v-if="hint" class="px-2 py-8 text-center text-base text-ink-gray-5">
          {{ hint }}
        </p>
        <template
          v-for="(row, index) in rows"
          :key="`${row.doctype}:${row.name}`"
        >
          <div
            v-if="row.heading"
            class="flex justify-between px-2 pb-1 pt-3 text-xs font-medium uppercase text-ink-gray-5"
          >
            <span>{{ row.heading.label }}</span>
            <span class="normal-case">{{ row.heading.total }}</span>
          </div>
          <button
            type="button"
            role="option"
            :aria-selected="index === active"
            class="flex w-full items-center gap-3 rounded px-2 py-2 text-left"
            :class="index === active ? 'bg-surface-gray-2' : ''"
            @mouseenter="active = index"
            @click="open(row)"
          >
            <span class="flex min-w-0 flex-1 flex-col">
              <span class="truncate text-base font-medium text-ink-gray-9">
                {{ row.title }}
              </span>
              <span
                v-if="row.subtitle"
                class="truncate text-sm text-ink-gray-5"
              >
                {{ row.subtitle }}
              </span>
            </span>
            <Badge
              v-if="row.badge"
              theme="gray"
              variant="subtle"
              :label="row.badge"
            />
          </button>
        </template>
      </div>

      <div
        class="flex gap-4 border-t border-outline-gray-1 pt-2 text-xs text-ink-gray-5"
      >
        <span>{{ __('↑ ↓ to move') }}</span>
        <span>{{ __('Enter to open') }}</span>
        <span>{{ __('Ctrl/⌘ K to search from anywhere') }}</span>
      </div>
    </div>
  </Dialog>
</template>

<script setup>
import LucideSearch from '~icons/lucide/search'
import { taskReference } from '@/components/ContactInactivity/taskReference'
import { showGlobalSearch } from '@/composables/modals'
import { flatRows, isSearchShortcut, moveIndex } from '@/utils/globalSearch'
import { watchDebounced } from '@vueuse/core'
import { Badge, createResource, Dialog } from 'frappe-ui'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

// Below this the server returns nothing, so no request is made.
const MIN_QUERY_LENGTH = 2

const router = useRouter()
const input = ref(null)
const query = ref('')
const active = ref(-1)

const results = createResource({ url: 'crm.txb.api.global_search.search' })

const rows = computed(() =>
  query.value.trim().length < MIN_QUERY_LENGTH
    ? []
    : flatRows(results.data?.groups),
)

const hint = computed(() => {
  const typed = query.value.trim()
  if (typed.length < MIN_QUERY_LENGTH) {
    return __('Type a name, email, phone or company code')
  }
  // Still waiting for this query's own results: say nothing rather than "no matches".
  if (results.loading || results.params?.query !== typed) return ''
  if (rows.value.length) return ''
  return __('No matches for “{0}”', [typed])
})

watchDebounced(
  query,
  (value) => {
    if (value.trim().length >= MIN_QUERY_LENGTH) {
      results.submit({ query: value.trim() })
    }
  },
  { debounce: 250 },
)

watch(rows, (list) => (active.value = list.length ? 0 : -1))

watch(showGlobalSearch, (opened) => {
  if (opened) nextTick(() => input.value?.focus())
})

function open(row) {
  const { route } = taskReference(row.doctype, row.name)
  if (!route) return
  showGlobalSearch.value = false
  router.push(route)
}

function onKeydown(event) {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    const delta = event.key === 'ArrowDown' ? 1 : -1
    active.value = moveIndex(active.value, delta, rows.value.length)
  } else if (event.key === 'Enter' && rows.value[active.value]) {
    event.preventDefault()
    open(rows.value[active.value])
  }
}

// The one app-wide shortcut: mounted once with the other global modals.
function onShortcut(event) {
  if (!isSearchShortcut(event)) return
  event.preventDefault()
  showGlobalSearch.value = true
}

onMounted(() => window.addEventListener('keydown', onShortcut))
onBeforeUnmount(() => window.removeEventListener('keydown', onShortcut))
</script>

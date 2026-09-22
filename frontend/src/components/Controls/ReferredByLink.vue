<template>
  <Autocomplete
    ref="autocomplete"
    :value="selected"
    :options="options"
    size="sm"
    :placeholder="placeholder"
    :disabled="disabled"
    :filterable="false"
    @change="onSelect"
  >
    <template #item-label="{ option }">
      <div class="flex min-w-0 flex-1 flex-col gap-0.5">
        <div class="flex items-center gap-2">
          <div class="truncate font-semibold text-ink-gray-8">
            {{ option.label }}
          </div>
          <Badge
            class="shrink-0"
            size="sm"
            variant="subtle"
            :theme="option.doctype === 'Contact' ? 'blue' : 'gray'"
            :label="typeLabel(option)"
          />
        </div>
        <div
          v-if="option.description"
          class="truncate text-sm text-ink-gray-5"
        >
          {{ option.description }}
        </div>
      </div>
    </template>

    <template #footer="{ close }">
      <Button
        variant="ghost"
        class="w-full !justify-start"
        :label="__('Clear')"
        iconLeft="x"
        @click="() => clear(close)"
      />
    </template>
  </Autocomplete>
</template>

<script>
// TXB-255: the Lead-or-Contact Referred By reference on CRM Lead (TXB-254 contract). The
// visible Dynamic Link holds the referrer's document name; its `options` names the hidden
// discriminator that holds the DocType. Both are always written together.
export const REFERRED_BY_FIELD = 'custom_referred_by'

export function isReferredByField(field, doctype) {
  return (
    doctype === 'CRM Lead' &&
    field?.fieldname === REFERRED_BY_FIELD &&
    field?.fieldtype === 'Dynamic Link'
  )
}

// Human labels of already-seen referrers, so a selection shows its name at once and a reloaded
// document only asks the server for a reference it has not resolved yet.
const labelCache = new Map()
const keyOf = (doctype, name) => `${doctype}::${name}`
</script>

<script setup>
import Autocomplete from '@/components/frappe-ui/Autocomplete.vue'
import { watchDebounced } from '@vueuse/core'
import { Badge, call } from 'frappe-ui'
import { computed, ref, watch } from 'vue'

const props = defineProps({
  referenceType: { type: String, default: '' },
  referenceName: { type: String, default: '' },
  excludeLead: { type: String, default: '' },
  placeholder: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
})

// Emits `{ doctype, name }` for a selection and `null` for a clear; never a half reference.
const emit = defineEmits(['change'])

const autocomplete = ref(null)
const options = ref([])
const resolvedLabel = ref('')
let searchSeq = 0

const selected = computed(() => {
  if (!props.referenceType || !props.referenceName) return null
  return {
    label: resolvedLabel.value || props.referenceName,
    value: keyOf(props.referenceType, props.referenceName),
  }
})

watch(
  () => [props.referenceType, props.referenceName],
  async ([doctype, name]) => {
    resolvedLabel.value = ''
    if (!doctype || !name) return
    const key = keyOf(doctype, name)
    if (labelCache.has(key)) {
      resolvedLabel.value = labelCache.get(key)
      return
    }
    const label = await resolveLabel(doctype, name)
    labelCache.set(key, label)
    if (keyOf(props.referenceType, props.referenceName) === key) {
      resolvedLabel.value = label
    }
  },
  { immediate: true },
)

async function resolveLabel(doctype, name) {
  const isLead = doctype === 'CRM Lead'
  try {
    const row = await call('frappe.client.get_value', {
      doctype,
      filters: { name },
      fieldname: isLead
        ? ['lead_name', 'first_name', 'last_name']
        : ['full_name', 'first_name', 'last_name'],
    })
    const parts = [row?.first_name, row?.last_name].filter(Boolean).join(' ')
    return (isLead ? row?.lead_name : row?.full_name) || parts || name
  } catch {
    // Unreadable or missing referrer: show the stored ID rather than nothing.
    return name
  }
}

watchDebounced(
  () => autocomplete.value?.query,
  (query) => search(query || ''),
  { debounce: 300 },
)

async function search(query) {
  const seq = ++searchSeq
  if (query.trim().length < 2) {
    options.value = []
    return
  }
  let rows = []
  try {
    rows = await call('crm.txb.api.people_search.search_referrers', {
      query,
      exclude_lead: props.excludeLead || null,
      limit: 20,
    })
  } catch {
    rows = []
  }
  if (seq !== searchSeq) return
  options.value = (rows || []).map((row) => ({
    label: row.full_name || row.name,
    value: keyOf(row.doctype, row.name),
    doctype: row.doctype,
    name: row.name,
    converted: row.converted,
    description: [row.email, row.mobile_no].filter(Boolean).join(' · '),
  }))
}

function typeLabel(option) {
  if (option.doctype === 'Contact') return __('Contact')
  return option.converted ? __('Lead (Converted)') : __('Lead')
}

function onSelect(option) {
  // The Autocomplete's own clear button emits null.
  if (!option) return emit('change', null)
  if (!option.doctype || !option.name) return
  labelCache.set(option.value, option.label)
  emit('change', { doctype: option.doctype, name: option.name })
}

function clear(close) {
  emit('change', null)
  close()
}
</script>

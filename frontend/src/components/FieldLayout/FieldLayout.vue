<template>
  <div
    class="flex flex-col"
    :class="{
      'border border-outline-gray-1 rounded-lg': hasTabs,
      'border-outline-elevation-2': hasTabs,
    }"
  >
    <Tabs
      v-model="tabIndex"
      as="div"
      :tabs="processedTabs"
      :class="[
        !hasTabs ? `[&_[role='tablist']]:hidden` : '',
        `[&_[role='tablist']::-webkit-scrollbar]:h-0 [&_[role='tab']]:shrink-0 [&_[role='tabpanel']]:overflow-visible !overflow-visible`,
      ]"
    >
      <template #tab-panel="{ tab }">
        <div class="sections" :class="{ 'my-4 sm:my-5': hasTabs }">
          <template v-for="section in tab.sections" :key="section.name">
            <Section :section="section" :data-name="section.name" />
          </template>
        </div>
      </template>
    </Tabs>
  </div>
</template>

<script setup>
import Section from '@/components/FieldLayout/Section.vue'
import { useDocument } from '@/data/document'
import { reconcileTabSelection, tabIdentity } from '@/utils/fieldLayoutTabs'
import { Tabs } from 'frappe-ui'
import { ref, computed, provide, watch } from 'vue'

const props = defineProps({
  tabs: { type: Array, default: () => [] },
  data: { type: Object, default: () => ({}) },
  doctype: { type: String, default: 'CRM Lead' },
  docname: { type: String, default: '' },
  isGridRow: { type: Boolean, default: false },
  preview: { type: Boolean, default: false },
  context: { type: Object, default: null },
})

const tabIndex = ref(0)

// The authoritative document name. Prefer the explicit docname prop (known
// synchronously by the parent modal) over data.name, which is empty while the
// document is still loading and would bind field changes to the wrong cache.
const resolvedDocname = computed(() => props.docname || props.data?.name || '')

// Get fieldPropertyOverrides for tab/section overrides
let overrides = {}
if (props.context) {
  // Standalone mode: use externally managed context, skip useDocument
  overrides = computed(() => props.context?.fieldPropertyOverrides || {})
} else if (!props.isGridRow) {
  const { document: doc } = useDocument(props.doctype, resolvedDocname.value)
  overrides = computed(() => doc?.fieldPropertyOverrides || {})
} else {
  overrides = computed(() => ({}))
}

const processedTabs = computed(() => {
  const ov = overrides.value
  return props.tabs
    .map((tab) => {
      const tabOverrides = ov[tab.name]
      const processedTab = tabOverrides ? { ...tab, ...tabOverrides } : tab
      return {
        ...processedTab,
        sections: processedTab.sections.map((section) => {
          const sectionOverrides = ov[section.name]
          return sectionOverrides
            ? { ...section, ...sectionOverrides }
            : section
        }),
      }
    })
    .filter((tab) => !tab.hidden)
})

const hasTabs = computed(() => {
  return (
    processedTabs.value.length > 1 ||
    (processedTabs.value.length == 1 && processedTabs.value[0].label)
  )
})

// Track the selected tab by identity, not just its index. processedTabs can change while the
// component is mounted — e.g. the Opportunity Data tabs react to pipeline_type (TXB-171) — so a
// raw tabIndex can end up pointing at a different tab or past the end (a blank panel). Reconcile
// on every change: keep the selection when the same tab survives, otherwise fall back to the
// first visible tab.
const activeTabKey = ref(null)

watch(
  processedTabs,
  (tabs) => {
    const { index, key } = reconcileTabSelection(tabs, activeTabKey.value)
    tabIndex.value = index
    activeTabKey.value = key
  },
  { immediate: true },
)

// A user selecting a tab moves tabIndex; remember which tab that is so the reconciliation above
// can preserve it across the next processedTabs change.
watch(tabIndex, (index) => {
  const tabs = processedTabs.value
  if (index >= 0 && index < tabs.length) {
    activeTabKey.value = tabIdentity(tabs[index])
  }
})

provide(
  'data',
  computed(() => props.data),
)
provide('hasTabs', hasTabs)
provide('doctype', props.doctype)
provide('docname', resolvedDocname)
provide('preview', props.preview)
provide('isGridRow', props.isGridRow)
provide('fieldLayoutContext', props.context)
</script>
<style scoped>
.section:not(:has(.field)) {
  display: none;
}

.section:has(.field):nth-child(1 of .section:has(.field)) {
  border-top: none;
  margin-top: 0;
  padding-top: 0;
}
</style>

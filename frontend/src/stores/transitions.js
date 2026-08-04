import { computed } from 'vue'
import { defineStore } from 'pinia'
import { createResource } from 'frappe-ui'

/**
 * The opportunity transition graph, fetched once and shared.
 *
 * Mirrors the pipelineStatuses resource in stores/statuses.js: one source of truth,
 * served by the backend, cached by frappe-ui's module-level resource cache so every
 * board and every deal page reads the same answer.
 */
export const transitionsStore = defineStore('crm-transitions', () => {
  const transitionMap = createResource({
    url: 'crm.txb.api.transitions.get_transition_map',
    cache: 'deal-transitions',
    initialData: { transitions: {}, can_change_status: {} },
    auto: true,
  })

  const transitions = computed(() => transitionMap.data?.transitions || {})

  /**
   * Whether this user may move statuses in a pipeline at all (TXB-105).
   * Unknown pipelines are unrestricted, matching the backend default.
   */
  function canChangeStatusFor(pipeline) {
    return transitionMap.data?.can_change_status?.[pipeline] !== false
  }

  return { transitionMap, transitions, canChangeStatusFor }
})

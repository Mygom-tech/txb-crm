import { defineStore } from 'pinia'
import { createResource } from 'frappe-ui'

/**
 * The opportunity transition graph, fetched once and shared.
 *
 * Mirrors the pipelineStatuses resource in stores/statuses.js: one source of truth,
 * served by the backend, cached by frappe-ui's module-level resource cache so every
 * board and every deal page reads the same answer.
 *
 * Deliberately exposes the resource itself rather than a `computed` over its data.
 * Pinia builds a setup store with `reactive()`, which unwraps top-level refs — so
 * `const { transitions } = transitionsStore()` would hand back a frozen plain snapshot
 * (`{}` before the fetch resolves) and `.value` would be `undefined`. A `createResource`
 * is a reactive *object*, so destructuring it keeps the same reference and `.data` stays
 * live. That is exactly why `pipelineStatuses` is safe to destructure everywhere today.
 */
export const transitionsStore = defineStore('crm-transitions', () => {
  const transitionMap = createResource({
    url: 'crm.txb.api.transitions.get_transition_map',
    cache: 'deal-transitions',
    initialData: { transitions: {}, can_change_status: {} },
    auto: true,
  })

  /**
   * Whether this user may move statuses in a pipeline at all (TXB-105).
   * Unknown pipelines are unrestricted, matching the backend default.
   */
  function canChangeStatusFor(pipeline) {
    return transitionMap.data?.can_change_status?.[pipeline] !== false
  }

  return { transitionMap, canChangeStatusFor }
})

/**
 * Tab-selection reconciliation for FieldLayout (TXB-171).
 *
 * FieldLayout stores its selection as a numeric `tabIndex`, but the visible tab list is not
 * static: the Opportunity Data tabs react to `pipeline_type` (see `resolveVisibleTabs`), so a
 * raw index can silently point at a different tab, or past the end of the list — a blank panel.
 * These pure helpers reconcile the selection by *identity* instead of index, so they can be
 * unit-tested without mounting a component.
 */

/** Stable identity of a rendered tab: its layout `name`, falling back to its `label`. */
export function tabIdentity(tab) {
  return tab?.name ?? tab?.label ?? null
}

/**
 * Reconcile a tab selection against the current visible tab list.
 *
 * Returns the index and identity of the tab that should be active: the previously active tab
 * when it is still present (selection preserved by identity), otherwise the first tab
 * (deterministic fallback), or an empty selection (`index 0`, `key null`) when there are no
 * tabs. Never returns an index outside the list, so the renderer never shows a blank panel.
 *
 * @param {Array<Object>} tabs      the currently visible tabs
 * @param {string|null} activeKey   the identity of the previously selected tab
 * @returns {{ index: number, key: string|null }}
 */
export function reconcileTabSelection(tabs, activeKey) {
  if (!Array.isArray(tabs) || tabs.length === 0) {
    return { index: 0, key: null }
  }
  let index = tabs.findIndex((tab) => tabIdentity(tab) === activeKey)
  if (index === -1) index = 0
  return { index, key: tabIdentity(tabs[index]) }
}

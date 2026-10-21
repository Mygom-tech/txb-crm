// Global search (Ctrl/⌘ K): pure helpers behind components/GlobalSearch.vue.

/** Whether a keydown is the search shortcut: Ctrl+K, or ⌘K on a Mac. */
export function isSearchShortcut(event) {
  return Boolean(
    (event.ctrlKey || event.metaKey) &&
    !event.altKey &&
    event.key?.toLowerCase() === 'k',
  )
}

/**
 * The rows of every group in display order, as one list the arrow keys walk. Each row carries
 * its group's doctype; the first row of a group also carries the group's heading.
 */
export function flatRows(groups = []) {
  return groups.flatMap((group) =>
    group.rows.map((row, index) => ({
      ...row,
      doctype: group.doctype,
      heading: index ? null : { label: group.label, total: group.total },
    })),
  )
}

/** The highlighted row after moving `delta` steps, wrapping at both ends. */
export function moveIndex(index, delta, length) {
  if (!length) return -1
  return (((index + delta) % length) + length) % length
}

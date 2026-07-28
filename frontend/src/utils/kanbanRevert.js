/**
 * Moves a kanban card back from `to` column to `from` column after a
 * cancelled or failed transition. Identity-based on purpose: while the
 * confirm modal was open, `list.reload()` (Load More, view switch) may have
 * replaced the column arrays wholesale, so captured references and DOM
 * indices from the drag event cannot be trusted.
 *
 * @param {Object} args
 * @param {Array} args.columns - live kanban columns: [{ column: { name }, data: [rows] }]
 * @param {string} args.itemName - row `name` of the moved card
 * @param {string} args.from - source column name
 * @param {string} args.to - target column name
 * @param {number} [args.oldIndex] - original position in the source column
 * @returns {boolean} false when the board changed underneath and the caller
 *   should fall back to a reload
 */
export function revertCardMove({ columns, itemName, from, to, oldIndex }) {
  if (!Array.isArray(columns) || !columns.length) return false

  const sourceColumn = columns.find((col) => col.column?.name == from)
  const targetColumn = columns.find((col) => col.column?.name == to)
  if (!sourceColumn?.data || !targetColumn?.data) return false

  const cardIndex = targetColumn.data.findIndex((row) => row.name == itemName)
  if (cardIndex === -1) return false

  const [card] = targetColumn.data.splice(cardIndex, 1)
  const insertAt = Math.min(
    typeof oldIndex === 'number' ? oldIndex : sourceColumn.data.length,
    sourceColumn.data.length,
  )
  sourceColumn.data.splice(insertAt, 0, card)
  return true
}

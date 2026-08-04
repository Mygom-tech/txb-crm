/**
 * Options for a Select field, whatever shape its meta is currently in.
 *
 * `stores/meta.js` getFields() rewrites Select `options` from Frappe's newline string
 * into an array of {label, value} *on the shared doctypesMeta object*. Whether a given
 * field is a string or an array therefore depends on whether anything called getFields()
 * for that doctype earlier in the session — ViewControls does, on every list view. Reading
 * raw meta means handling both.
 *
 * @param {Object} field  a doctype meta field, possibly undefined before meta loads
 * @returns {Array<{label: string, value: string}>}
 */
export function selectFieldOptions(field) {
  const options = field?.options

  if (Array.isArray(options)) {
    return options.filter((option) => option?.value)
  }

  if (typeof options !== 'string') return []

  return options
    .split('\n')
    .filter(Boolean)
    .map((value) => ({ label: value, value }))
}

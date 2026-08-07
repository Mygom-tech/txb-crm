/**
 * Fields the Convert to Deal modal renders with its own controls.
 *
 * `get_fields_layout(type="Required Fields")` synthesises a section from every reqd field
 * without a default. On CRM Deal that is `status`, which the modal already renders in its
 * Pipeline section — filtered to the chosen pipeline, where the synthesised one offers
 * every status including ones invalid for that pipeline. `pipeline_type` is listed too so
 * the duplicate cannot reappear if it is ever made required.
 */
export const SELF_RENDERED_FIELDS = ['status', 'pipeline_type']

/**
 * Strip the given fieldnames from a fields layout, dropping anything left empty.
 *
 * Returns [] when nothing survives, because the modal keys its FieldLayout on
 * `dealTabs.data?.length` — an empty array is how the section is told not to render.
 *
 * @param {Array} tabs        as returned by get_fields_layout
 * @param {string[]} fieldnames
 * @returns {Array} tabs containing at least one field, or []
 */
export function excludeSelfRenderedFields(tabs, fieldnames) {
  const kept = (tabs || []).map((tab) => ({
    ...tab,
    sections: (tab.sections || [])
      .map((section) => ({
        ...section,
        columns: (section.columns || [])
          .map((column) => ({
            ...column,
            fields: (column.fields || []).filter(
              (field) => !fieldnames.includes(field?.fieldname),
            ),
          }))
          .filter((column) => column.fields.length),
      }))
      .filter((section) => section.columns.length),
  }))

  return kept.filter((tab) => tab.sections.length)
}

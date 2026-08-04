/**
 * Which deal statuses a pipeline may use.
 *
 * The mapping itself comes from the backend (crm.txb.api.pipelines.get_pipeline_statuses)
 * so there is exactly one source of truth. These helpers are pure so they can be tested
 * without a browser or a Frappe site.
 */

/**
 * Statuses selectable for a deal, in display order.
 *
 * The current status is always included even when it falls outside the pipeline's list.
 * Real data already contains such a deal (a Workshop sitting in "Active"), and filtering
 * it out would leave that deal unable to show — or change — its own status.
 *
 * @param {string} pipelineType     the deal's pipeline_type
 * @param {string} currentStatus    the deal's status right now
 * @param {Object<string,string[]>} pipelineStatuses  map from the backend
 * @returns {string[]} allowed statuses, or [] meaning "no opinion, show everything"
 */
export function allowedStatusesFor(
  pipelineType,
  currentStatus,
  pipelineStatuses,
) {
  const allowed = pipelineStatuses?.[pipelineType]

  // Unknown or unset pipeline: fall back to showing every status rather than trapping
  // the user with an empty dropdown.
  if (!allowed?.length) return []

  if (!currentStatus || allowed.includes(currentStatus)) return [...allowed]

  return [...allowed, currentStatus]
}

/**
 * Link-field filter restricting a CRM Deal Status lookup to the allowed set.
 *
 * Returns null when there is no restriction to apply, so callers can leave the field's
 * existing filters untouched.
 *
 * @returns {Object|null} e.g. { name: ['in', ['Session Set', 'Won']] }
 */
export function statusLinkFilters(
  pipelineType,
  currentStatus,
  pipelineStatuses,
) {
  const allowed = allowedStatusesFor(
    pipelineType,
    currentStatus,
    pipelineStatuses,
  )
  if (!allowed.length) return null

  return { name: ['in', allowed] }
}

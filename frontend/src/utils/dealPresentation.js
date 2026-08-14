/**
 * Deal presentation rules, ported from the `Notes Tab Rename` and `Hide Call Duration`
 * Form Scripts.
 *
 * Those scripts mutated the DOM after render: one relabelled the Notes tab for coaching
 * deals, the other stripped the duration badge off call activities on the deal page. Both
 * are now normal, code-owned component behaviour driven by the deal's pipeline and the
 * host doctype. These helpers are pure so they can be unit-tested without a browser or a
 * Frappe site.
 */

/** The pipeline whose deals are coaching engagements (crm/txb/constants.py). */
export const COACHING_PIPELINE_TYPE = 'Delivering Coaching'

/** The Deal doctype whose call activities hide their duration badge. */
export const DEAL_DOCTYPE = 'CRM Deal'

/**
 * Whether a deal belongs to the coaching pipeline.
 *
 * @param {string} pipelineType  the deal's pipeline_type
 * @returns {boolean}
 */
export function isCoachingPipeline(pipelineType) {
  return pipelineType === COACHING_PIPELINE_TYPE
}

/**
 * The label for a deal's Notes tab.
 *
 * Coaching deals call it "Coaching Notes"; every other pipeline keeps "Notes". Returns the
 * raw English label; callers pass it through `__()` for translation exactly as before.
 *
 * @param {string} pipelineType  the deal's pipeline_type
 * @returns {string}
 */
export function notesTabLabel(pipelineType) {
  return isCoachingPipeline(pipelineType) ? 'Coaching Notes' : 'Notes'
}

/**
 * Whether a call activity should hide its duration badge.
 *
 * Duration is suppressed only for calls shown on a Deal; every other entity page (Lead,
 * Contact, ...) keeps the badge. Scoping on the host doctype replaces the old blanket DOM
 * strip, which removed the badge wherever the deal page happened to be mounted.
 *
 * @param {string} doctype  the entity the call activity is rendered under
 * @returns {boolean}
 */
export function hideCallDuration(doctype) {
  return doctype === DEAL_DOCTYPE
}

// Read-only Contact Activity aggregate presentation (TXB-179 / TXB-132).
//
// The Contact activity endpoint (crm.api.activities.get_contact_activities) returns the same
// (versions, calls, notes, tasks, attachments) shape as the Lead/Deal endpoints, but every
// record is additionally tagged by the backend aggregator with:
//
//   source_doctype  "CRM Lead" (archived, pre-conversion) | "CRM Deal" (linked Opportunity)
//   source_docname  the immutable Lead/Deal identity -- the route identity and label fallback
//   phase           "pre_conversion" | "post_conversion"
//
// These helpers are pure so the aggregate presentation contract (source badge label, route,
// and phase attribution) is unit-testable without mounting the timeline. The UI stays a thin
// consumer: it renders whatever `activitySource` returns and never re-derives routing itself.

export const LEAD_SOURCE = 'CRM Lead'
export const DEAL_SOURCE = 'CRM Deal'

export const PHASE_PRE_CONVERSION = 'pre_conversion'
export const PHASE_POST_CONVERSION = 'post_conversion'

// Human phase labels. Pre-conversion history comes from the archived Lead; post-conversion
// history comes from the linked Opportunity. Anything else is left unlabeled rather than guessed.
export const PHASE_LABELS = {
  [PHASE_PRE_CONVERSION]: 'Before conversion',
  [PHASE_POST_CONVERSION]: 'After conversion',
}

export function phaseLabel(phase) {
  return PHASE_LABELS[phase] || ''
}

/**
 * Describe how one aggregated activity should be attributed and linked in the read-only Contact
 * log. Returns null for records without source tags (e.g. non-aggregate Lead/Deal timelines),
 * so callers can `v-if` the badge off outside aggregate mode.
 *
 * Archived Lead entries PREFER the Lead email as the human label when one is known, but always
 * navigate by the immutable Lead docname -- the email is display-only and never the route
 * identity, and the docname is the label fallback when no email is available. Opportunity
 * entries navigate to their source Deal.
 *
 * @param {object} activity - a single aggregated record carrying source_* / phase tags
 * @param {Record<string,string>} leadEmails - map of Lead docname -> email (optional)
 */
export function activitySource(activity, leadEmails = {}) {
  if (!activity || typeof activity !== 'object') return null

  const doctype = activity.source_doctype
  const docname = activity.source_docname
  if (!doctype || !docname) return null

  const phase = activity.phase || ''
  const base = {
    doctype,
    docname,
    phase,
    phaseLabel: phaseLabel(phase),
  }

  if (doctype === LEAD_SOURCE) {
    const email = leadEmails?.[docname]
    return {
      ...base,
      kind: 'lead',
      // Prefer email as the human label; fall back to the immutable docname.
      label: email || docname,
      // Route identity is always the immutable Lead docname, never the (mutable) email.
      route: { name: 'Lead', params: { leadId: docname } },
    }
  }

  if (doctype === DEAL_SOURCE) {
    return {
      ...base,
      kind: 'deal',
      label: docname,
      route: { name: 'Deal', params: { dealId: docname } },
    }
  }

  return null
}

/**
 * Distinct archived-Lead docnames referenced across a set of aggregated records. Used to batch
 * a single email lookup so the log can prefer email labels without querying per row.
 */
export function leadSourceNames(activities) {
  if (!Array.isArray(activities)) return []
  const names = new Set()
  for (const activity of activities) {
    if (activity?.source_doctype === LEAD_SOURCE && activity.source_docname) {
      names.add(activity.source_docname)
    }
  }
  return [...names]
}

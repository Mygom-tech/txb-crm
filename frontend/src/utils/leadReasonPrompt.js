// Native replacements for the `Disqualified Reason Prompt` and `Lead Creation Redirect`
// runtime Form Scripts (retired in crm/txb/retired_scripts.py). The decision logic lives
// here as pure functions so Lead.vue stays declarative and the behaviour is unit-testable.

// Placeholder reason meaning a Disqualified lead still owes a real reason. Persisting it
// keeps the lead flagged as pending while remaining "unresolved", so the prompt re-opens
// on every visit until someone picks an actual reason.
export const PENDING_REVIEW = 'Pending Review'

// A Disqualified (Lost-type) lead is unresolved until it carries a real reason. Both a
// blank reason and the Pending Review placeholder count as unresolved, which is what makes
// the prompt re-open every time the lead is opened. `getLeadStatus` maps a status name to
// its `{ type }` (same helper Lead.vue already uses for the status-change flow).
export function isDisqualifiedReasonUnresolved(doc, getLeadStatus) {
  if (!doc || !doc.status || typeof getLeadStatus !== 'function') return false

  const status = getLeadStatus(doc.status)
  if (!status || status.type !== 'Lost') return false

  const reason = (doc.lost_reason || '').trim()
  return reason === '' || reason === PENDING_REVIEW
}

// Skipping the prompt must not lose the fact that a reason is still owed: a blank (or
// already Pending Review) reason becomes the Pending Review placeholder, while an existing
// real reason is preserved untouched.
export function reasonAfterSkip(currentReason) {
  const reason = (currentReason || '').trim()
  return reason && reason !== PENDING_REVIEW ? reason : PENDING_REVIEW
}

// Native replacement for the `Lead Creation Redirect` Form Script. The router guard fills
// the tab hash for a Lead/Deal route that arrives without one; this decides which tab that
// is. A freshly opened *lead* route defaults to the Data tab, while deals keep their
// last-visited-tab behavior. The guard only calls this when the route has no hash, so an
// explicit in-session tab selection — which always carries a hash — is never overridden.
export function initialRouteTab(routeName, lastVisitedTab) {
  if (routeName === 'Lead') return 'data'
  return lastVisitedTab || 'activity'
}

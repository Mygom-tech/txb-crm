// Native replacement for the `Organization Reload After Create` runtime Form Script
// (retired in crm/txb/retired_scripts.py). That script waited on a timer and then forced a
// full browser reload so a freshly inserted Organization would show its server-committed
// state. The decision logic lives here as pure functions so Organization.vue stays
// declarative and the behaviour is unit-testable.
//
// Flow: OrganizationModal inserts the doc via `frappe.client.insert` (server-committed) and
// routes to /organizations/:organizationId with a one-shot `created` flag. On mount,
// Organization.vue reconciles its document resource with the canonical saved Organization
// (a scoped resource reload — never a full-page reload or timer), then strips the flag from
// the URL so a subsequent reload/back/breadcrumb navigation never re-triggers the reconcile
// and the flag never leaks into breadcrumb links built from route.query.

export const CREATED_QUERY_KEY = 'created'

// A route carrying the one-shot created flag arrived straight from a fresh insert and still
// needs its resource reconciled to the server-committed document.
export function isFreshlyCreatedRoute(query) {
  return Boolean(query && query[CREATED_QUERY_KEY])
}

// Return a copy of a vue-router query object with the one-shot created flag removed,
// preserving every other param (e.g. the list view/viewType the Organization breadcrumbs
// build their links from). Organization.vue feeds this to `router.replace` so the flag is
// dropped from the router's reactive query — keeping breadcrumb links clean — without
// re-triggering the reconcile on a later reload/back navigation.
export function queryWithoutCreatedFlag(query) {
  const rest = { ...(query || {}) }
  delete rest[CREATED_QUERY_KEY]
  return rest
}

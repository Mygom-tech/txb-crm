/**
 * Shared resolver for the CRM Deal → source Lead reference shown in the Deal
 * sidebar.
 *
 * A Deal's `lead` field is an *editable* Link to `CRM Lead`, so the generic
 * sidebar Link control renders it as a selector: clicking the displayed value
 * opens the dropdown instead of navigating to the originating Lead. Authorized
 * users still need that selector to re-point the relationship, so this helper
 * does not touch the editing control. Instead it narrowly recognizes the
 * populated Deal source-Lead field and exposes navigation by the immutable Lead
 * docname through the existing named `Lead` route — the sidebar pairs it with a
 * separate, independently clickable affordance (mirroring the read-only Deal
 * handover links). Navigation by docname stays valid for converted/archived
 * Leads that are filtered out of the normal Leads list.
 */

/**
 * True when a field is the populated, source-Lead Link on a CRM Deal — i.e. the
 * reference that should offer navigation alongside its editing selector. Scoped
 * to the `lead` field of `CRM Deal` so arbitrary Link fields are unaffected.
 */
export function isDealSourceLeadField(doctype, field, value) {
  return Boolean(
    doctype === 'CRM Deal' &&
      field?.fieldname === 'lead' &&
      field?.fieldtype === 'Link' &&
      field?.options === 'CRM Lead' &&
      value,
  )
}

/**
 * Navigate to the source Lead using the named `Lead` route and its immutable
 * CRM Lead id (docname), which resolves even for archived/converted Leads.
 */
export function navigateToLead(router, leadId) {
  if (!router || !leadId) return
  router.push({ name: 'Lead', params: { leadId } })
}

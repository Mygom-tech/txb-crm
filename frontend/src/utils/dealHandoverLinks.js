/**
 * Shared resolver for the TXB handover references between a Won/Sold sales
 * Opportunity and its linked Delivering Coaching Opportunity.
 *
 * The handover fields (`custom_sales_source_deal`, `custom_delivery_deal`) are
 * read-only Link fields to `CRM Deal`. The shared field renderers short-circuit
 * read-only values to plain text before the generic Link branch, so those
 * references would otherwise never become navigable. This helper narrowly
 * recognizes a read-only Link → CRM Deal reference and drives navigation through
 * the existing named `Deal` route, without touching arbitrary read-only text or
 * making the field editable.
 */

/**
 * True when a field is a populated, read-only Link that points at a CRM Deal —
 * i.e. a handover reference that should navigate instead of rendering as text.
 */
export function isDealNavLink(field, value) {
  return Boolean(
    field?.read_only &&
      field?.fieldtype === 'Link' &&
      field?.options === 'CRM Deal' &&
      value,
  )
}

/**
 * Navigate to the referenced Opportunity using the named `Deal` route and its
 * CRM Deal id.
 */
export function navigateToDeal(router, dealId) {
  if (!router || !dealId) return
  router.push({ name: 'Deal', params: { dealId } })
}

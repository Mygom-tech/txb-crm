// Newest-first chronology is a fixed CRM-wide invariant for every activity stream (TXB-198):
// the combined Activity feed, the filtered Emails/Comments/Calls tabs, expanded grouped Version
// children and WhatsApp messages all render latest -> oldest, regardless of any historical
// crm_timeline_sort_order preference. This is the single pure comparator behind that invariant.
//
// Direction comes from the comparator operand order, not .reverse(): a consistent comparator
// keeps .sort() idempotent, so sorting a reactive array in place doesn't re-trigger a computed.
export function sortByCreation(list) {
  return list.sort((a, b) => new Date(b.creation) - new Date(a.creation))
}

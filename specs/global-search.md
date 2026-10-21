# Global search

**Ctrl/⌘ K** anywhere, or **Search** at the top of the sidebar, opens one search box over
Leads, Contacts, Companies and Deals (`frontend/src/components/GlobalSearch.vue`,
`crm.txb.api.global_search.search`).

- **Matches** when every typed word appears in a name, email or company code (Companies also
  match their website), or when 6+ typed digits match a phone number in any formatting
  (`+370 612 34567`, `861234567` and `612 34567` are the same number).
- **Groups**, in this order: Leads · Contacts · Companies · Deals, each showing its 5 newest
  matches and how many there are in total. Narrow the query to see others.
- **Converted Leads are included**: they open as the read-only archived Lead and show their
  "Converted" status as the badge.
- **Visibility**: candidates are re-read through `frappe.get_list`, so the existing Lead/Deal
  visibility rules apply and nobody finds a record they could not open.
- **Keyboard**: ↑/↓ move, Enter opens, Esc closes. At least 2 characters before searching.

Built on the same two-pass approach as `crm.txb.api.people_search.search_referrers`.

Limits: name/email/code matching uses `LIKE '%…%'` and phone matching strips digits in SQL, so
neither uses an index; candidates are capped at 200 per type. Fine at CRM volumes.

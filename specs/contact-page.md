# Contact page: Source Lead, Calls tab, Log a Call in the header

- **Source Lead**: the sidebar (desktop) / Details tab (mobile) lists the archived Leads this
  Contact came from (`ContactSourceLeads.vue`, `crm.api.contact.get_source_leads`): Leads whose
  conversion recorded it (`converted_contact`, indexed) plus the Lead behind any Opportunity it
  is the **primary** contact of (older conversions). A secondary contact on someone else's
  Opportunity gets none. Read through `frappe.get_list`, so Lead permissions apply.
  A converted Lead keeps all its data and opens read-only.
- **Calls tab** next to Notes (`ContactCalls.vue`): every call in the Contact's aggregate history
  (its archived Leads, linked Opportunities and calls logged on the Contact), newest first, with
  type, status, source, caller, duration and time; searchable; a row opens the call's detail.
  Notes and Calls share `useContactActivityFeed` and the Activity timeline's single fetch.
- **Log a Call** sits in the desktop header. On mobile (no header room) it is in the Activity
  and Calls tabs.

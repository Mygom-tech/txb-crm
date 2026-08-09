"""Retire the Form Scripts the native ownership UI replaces.

`Lead Owner Read-Only` injected CSS to grey out lead_owner for non-Admins and enforced
nothing -- a PATCH to the field succeeded regardless. `restrict_owner_field` does the
rendering and `guard_owner_change` does the enforcing.

`Contact_Create Opportunity` built a Create Deal modal from raw HTML strings, then fired a
second PUT to correct the status the insert had defaulted. That second write is now
refused by TXB-110's transition guard for non-Admins on Workshop and Selling Training,
so the script is not merely redundant -- it is broken. CreateDealFromContactModal.vue
replaces it with a single insert.

Must ship in the same deploy as the Vue changes, or both the native controls and the
injected ones appear.

Kept for the record; the retirement itself now re-runs on every migrate via
`crm.txb.retired_scripts`, which owns the list this file used to hold.
"""

from crm.txb.retired_scripts import retire_scripts


def execute():
	retire_scripts()

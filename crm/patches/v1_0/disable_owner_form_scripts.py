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
"""

import frappe

RETIRED_SCRIPTS = (
	"Lead Owner Read-Only",
	"Contact_Create Opportunity",
)


def execute():
	touched = False

	for name in RETIRED_SCRIPTS:
		if not frappe.db.exists("CRM Form Script", name):
			continue

		if not frappe.db.get_value("CRM Form Script", name, "enabled"):
			continue

		frappe.db.set_value("CRM Form Script", name, "enabled", 0)
		touched = True
		frappe.logger().info(f"[disable_owner_form_scripts] Disabled {name}")

	if touched:
		frappe.clear_cache()

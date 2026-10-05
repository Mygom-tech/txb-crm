"""Start every Contact's inactivity cycle (TXB-278).

The `CRM Contact Inactivity Cycle` DocType arrives with the normal model sync; this patch adds
the FCRM Settings interval field (blank: six calendar months), records the rollout date and opens
one live cycle per Contact, anchored on its latest verified human contact or else the rollout.

Idempotent: `start_cycle` returns a Contact's existing live cycle, so a re-run opens nothing.
"""

import frappe

from crm.txb.contact_inactivity import (
	CONTACT_DOCTYPE,
	ensure_interval_setting,
	rollout_date,
	start_cycle,
)


def execute():
	ensure_interval_setting()
	rollout_date()
	for contact in frappe.get_all(CONTACT_DOCTYPE, pluck="name", order_by="creation asc"):
		start_cycle(contact)

"""Pipeline configuration served to the frontend.

One endpoint, one source of truth. Every surface that needs to know which statuses a
pipeline may use reads it from here instead of carrying its own copy.
"""

import frappe

from crm.txb.constants import PIPELINE_STATUSES


@frappe.whitelist()
def get_pipeline_statuses() -> dict:
	"""Return `{pipeline_type: [status, ...]}` in display order.

	Only statuses that actually exist in CRM Deal Status are returned, so a stale entry in
	the mapping can never surface an unselectable option in the UI -- which is exactly the
	failure mode the old Form Script copies had.
	"""
	existing = set(frappe.get_all("CRM Deal Status", pluck="name"))

	return {
		pipeline: [status for status in statuses if status in existing]
		for pipeline, statuses in PIPELINE_STATUSES.items()
	}

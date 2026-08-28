"""TXB-15: workshop registration v2.

Installs the Contact registration fields, re-points every issued registration link at the
new page on this site, and retires the interim web-form version (the seeded "Workshop
Registration" Web Form + its Notification) and the legacy `registracija` Web Page.
"""

import frappe

from crm.txb.constants import FIELD_REGISTRATION_TOKEN, PIPELINE_WORKSHOP
from crm.txb.doc_events.deal import issue_registration_link
from crm.txb.registration_setup import ensure_registration_setup

RETIRED_WEB_FORM_ROUTE = "workshop-registration"
RETIRED_NOTIFICATION = "Workshop Registration Confirmation"
LEGACY_WEB_PAGE = "registracija"
RETIRED_DEAL_FIELDS = ("custom_workshop_interest", "custom_comments")


def execute():
	ensure_registration_setup()

	for name in frappe.get_all(
		"CRM Deal",
		{"pipeline_type": PIPELINE_WORKSHOP, FIELD_REGISTRATION_TOKEN: ["is", "set"]},
		pluck="name",
	):
		doc = frappe.get_doc("CRM Deal", name)
		link = issue_registration_link(doc)
		frappe.db.set_value("CRM Deal", name, "custom_registration_link", link, update_modified=False)

	form = frappe.db.get_value("Web Form", {"route": RETIRED_WEB_FORM_ROUTE, "module": "FCRM"})
	if form:
		frappe.delete_doc("Web Form", form, ignore_permissions=True, force=True)
	if frappe.db.exists("Notification", RETIRED_NOTIFICATION):
		frappe.delete_doc("Notification", RETIRED_NOTIFICATION, ignore_permissions=True, force=True)
	for fieldname in RETIRED_DEAL_FIELDS:  # only ever installed on dev sites
		cf = frappe.db.get_value("Custom Field", {"dt": "CRM Deal", "fieldname": fieldname})
		if cf:
			frappe.delete_doc("Custom Field", cf, ignore_permissions=True, force=True)
	if frappe.db.exists("Web Page", LEGACY_WEB_PAGE):
		frappe.db.set_value("Web Page", LEGACY_WEB_PAGE, "published", 0)

	frappe.clear_cache(doctype="CRM Deal")
	frappe.db.commit()

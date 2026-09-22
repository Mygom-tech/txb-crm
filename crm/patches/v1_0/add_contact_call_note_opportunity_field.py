"""Install the optional `opportunity` link on CRM Call Log and FCRM Note (TXB-256).

TXB-248 added the nullable `opportunity` Link (-> CRM Deal) to both DocType JSONs, but the model
sync skips a DocType whose stored `modified` is not older than the file's, so sites upgraded from
the pre-TXB-248 schema could keep the old metadata and table without the field. This force-reloads
each DocType only while its field or column is still missing: the reload adds the nullable column
in place, so existing rows and their canonical reference_doctype/reference_docname are untouched,
and re-running the patch is a no-op.
"""

import frappe

FIELD_OPPORTUNITY = "opportunity"
DOCTYPES = (("crm_call_log", "CRM Call Log"), ("fcrm_note", "FCRM Note"))


def execute():
	for doctype_dir, doctype in DOCTYPES:
		if has_opportunity_field(doctype):
			continue
		frappe.reload_doc("fcrm", "doctype", doctype_dir, force=True)
		frappe.clear_cache(doctype=doctype)


def has_opportunity_field(doctype):
	return frappe.get_meta(doctype).has_field(FIELD_OPPORTUNITY) and frappe.db.has_column(
		doctype, FIELD_OPPORTUNITY
	)

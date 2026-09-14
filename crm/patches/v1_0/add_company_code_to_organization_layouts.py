"""Expose the Company Code field on existing sites' Organization layouts (TXB-244).

``custom_company_code`` is the user-entered half of the Company Code contract (TXB-243). The
default Organization Quick Entry (the create/quick-create modal) and Side Panel (the edit
sidebar) layouts gain it in ``crm/install.py``, but that seeder is skip-if-exists -- so sites
created before this change never show the field and users cannot enter a code where the
backend now requires one. This patch injects ``custom_company_code`` next to
``organization_name`` in those two managed layouts. Idempotent: a layout already carrying the
field (or a site without the custom field) is left untouched.
"""

import json

import frappe

from crm.fcrm.doctype.crm_organization.company_code import FIELD_COMPANY_CODE

TARGET_LAYOUTS = [
	# Quick Entry = the create-modal layout; Side Panel = the Organization edit sidebar.
	"CRM Organization-Quick Entry",
	"CRM Organization-Side Panel",
]


def _iter_columns(layout):
	"""Yield every column dict in a layout tree (handles tabbed layouts that nest sections
	under a top-level section's ``sections`` key)."""
	for section in layout:
		yield from section.get("columns", [])
		for nested in section.get("sections", []):
			yield from nested.get("columns", [])


def _column_with(layout, fieldname):
	"""The first column already holding ``fieldname`` (organization_name anchors the code)."""
	for column in _iter_columns(layout):
		if fieldname in column.get("fields", []):
			return column
	return None


def execute():
	for name in TARGET_LAYOUTS:
		if not frappe.db.exists("CRM Fields Layout", name):
			continue

		doc = frappe.get_doc("CRM Fields Layout", name)
		try:
			layout = json.loads(doc.layout or "[]")
		except (ValueError, TypeError):
			continue

		if not frappe.get_meta(doc.dt).has_field(FIELD_COMPANY_CODE):
			continue

		present = {f for col in _iter_columns(layout) for f in col.get("fields", [])}
		if FIELD_COMPANY_CODE in present:
			continue

		# Place the code right after organization_name so it reads as part of the identity;
		# fall back to the first available column if the anchor was moved out of the layout.
		anchor = _column_with(layout, "organization_name")
		if anchor is None:
			anchor = next(iter(_iter_columns(layout)), None)
		if anchor is None:
			continue

		fields = anchor.setdefault("fields", [])
		if "organization_name" in fields:
			fields.insert(fields.index("organization_name") + 1, FIELD_COMPANY_CODE)
		else:
			fields.append(FIELD_COMPANY_CODE)

		doc.layout = json.dumps(layout)
		doc.save()

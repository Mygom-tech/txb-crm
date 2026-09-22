"""Install the TXB-254 Lead-or-Contact Referred By reference on CRM Lead.

A Lead's referrer is a person who is either another CRM Lead (including a converted/archived
one) or a Contact. A plain Link targets one DocType, so the reference is a typed pair:

- `custom_referred_by_type` -- hidden Select discriminator, ``CRM Lead`` or ``Contact``;
- `custom_referred_by` -- the visible Dynamic Link labelled "Referred By", holding the
  referrer's stable document name.

The legacy `custom_referred_by_user` (Link -> User) is kept, hidden and relabelled, never
cleared or mapped: its values stay in the database untouched. Sites without it get it created
hidden, so every environment carries the same schema.

The visible reference takes the legacy field's place in every CRM Lead fields layout. Every
step is guarded, so a re-run -- or a run after a partial one -- is a clean no-op.
"""

import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from crm.txb.constants import (
	FIELD_REFERRED_BY,
	FIELD_REFERRED_BY_LEGACY_USER,
	FIELD_REFERRED_BY_TYPE,
	REFERRED_BY_DOCTYPES,
)

LEAD_DOCTYPE = "CRM Lead"
LEGACY_LABEL = "Referred By (Legacy User)"

LEGACY_FIELD = {
	"fieldname": FIELD_REFERRED_BY_LEGACY_USER,
	"fieldtype": "Link",
	"options": "User",
	"label": LEGACY_LABEL,
	"hidden": 1,
	"insert_after": "source",
}

# Declared in order: the Dynamic Link's options name the discriminator, which must exist first.
FIELD_DEFINITIONS = [
	{
		"fieldname": FIELD_REFERRED_BY_TYPE,
		"fieldtype": "Select",
		"options": "\n" + "\n".join(REFERRED_BY_DOCTYPES),
		"label": "Referred By Type",
		"hidden": 1,
		"insert_after": FIELD_REFERRED_BY_LEGACY_USER,
	},
	{
		"fieldname": FIELD_REFERRED_BY,
		"fieldtype": "Dynamic Link",
		"options": FIELD_REFERRED_BY_TYPE,
		"label": "Referred By",
		"description": "The Lead or Contact who referred this Lead.",
		"insert_after": FIELD_REFERRED_BY_TYPE,
	},
]


def execute():
	_preserve_legacy_field()

	meta = frappe.get_meta(LEAD_DOCTYPE)
	missing = [field for field in FIELD_DEFINITIONS if not meta.has_field(field["fieldname"])]
	if missing:
		create_custom_fields({LEAD_DOCTYPE: missing})

	frappe.clear_cache(doctype=LEAD_DOCTYPE)
	_replace_in_layouts()


def _preserve_legacy_field():
	"""Hide the legacy Link -> User; create it hidden where it never existed. Values untouched."""
	name = frappe.db.get_value(
		"Custom Field", {"dt": LEAD_DOCTYPE, "fieldname": FIELD_REFERRED_BY_LEGACY_USER}
	)
	if not name:
		create_custom_fields({LEAD_DOCTYPE: [LEGACY_FIELD]})
		return

	current = frappe.db.get_value("Custom Field", name, ["hidden", "label"], as_dict=True)
	if current.hidden and current.label == LEGACY_LABEL:
		return

	# A metadata-only update: no column change, and the stored User values are not read or
	# written, so nothing is deleted or reinterpreted.
	frappe.db.set_value("Custom Field", name, {"hidden": 1, "label": LEGACY_LABEL})


def _replace_in_layouts():
	"""Put the visible reference where the legacy field sat in each CRM Lead layout."""
	for name in frappe.get_all("CRM Fields Layout", filters={"dt": LEAD_DOCTYPE}, pluck="name"):
		doc = frappe.get_doc("CRM Fields Layout", name)
		try:
			layout = json.loads(doc.layout or "[]")
		except (ValueError, TypeError):
			continue

		columns = list(_iter_columns(layout))
		present = {_fieldname(f) for column in columns for f in column.get("fields", [])}
		if FIELD_REFERRED_BY_LEGACY_USER not in present:
			continue

		changed = False
		for column in columns:
			fields = column.get("fields", [])
			for index, field in enumerate(fields):
				if _fieldname(field) != FIELD_REFERRED_BY_LEGACY_USER:
					continue
				if FIELD_REFERRED_BY in present:
					fields[index] = None
				else:
					fields[index] = FIELD_REFERRED_BY
					present.add(FIELD_REFERRED_BY)
				changed = True
			column["fields"] = [f for f in fields if f is not None]

		if changed:
			doc.layout = json.dumps(layout)
			doc.save(ignore_permissions=True)


def _iter_columns(layout):
	"""Every column dict in a layout tree, including sections nested under tabs."""
	for section in layout if isinstance(layout, list) else []:
		yield from section.get("columns", [])
		for nested in section.get("sections", []):
			yield from nested.get("columns", [])


def _fieldname(field):
	return field.get("fieldname") if isinstance(field, dict) else field

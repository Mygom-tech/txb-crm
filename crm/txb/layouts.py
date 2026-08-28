"""Idempotent edits to CRM Fields Layout records (side panels, quick entry)."""

import json

import frappe


def add_fields_after(layout_name: str, anchor: str, fieldnames: list[str]) -> None:
	"""Insert `fieldnames` right after `anchor` in the layout (or at the end of the first
	column when the anchor is absent). Fields already present anywhere are left alone."""
	if not frappe.db.exists("CRM Fields Layout", layout_name):
		return
	doc = frappe.get_doc("CRM Fields Layout", layout_name)
	layout = json.loads(doc.layout or "[]")
	columns = [col for section in layout for col in section.get("columns", [])]
	if not columns:
		return
	present = {f for col in columns for f in col.get("fields", [])}
	missing = [f for f in fieldnames if f not in present]
	if not missing:
		return
	target = next((col for col in columns if anchor in col.get("fields", [])), columns[0])
	fields = target.setdefault("fields", [])
	at = fields.index(anchor) + 1 if anchor in fields else len(fields)
	fields[at:at] = missing
	doc.layout = json.dumps(layout)
	doc.save(ignore_permissions=True)

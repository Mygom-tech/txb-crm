"""Give unowned records to whoever created them, where that is a real person.

TXB-106 makes owner changes Admin-only, so anything left unowned needs an Admin to assign
it by hand. Frappe's `owner` column already records the creator, which is the honest answer
for records a person made.

`Administrator` and `Guest` are excluded deliberately. On this data set 548 of the 593
unowned contacts were created by `Administrator` during a bulk import, and handing those to
that account would be inventing ownership -- which here decides commission. They stay
Unassigned and are claimed through the Claim Request flow when someone actually works them.

Writes with `update_modified=False` so a backfill does not reorder activity feeds, and via
`db.set_value` so `guard_owner_change` is not asked to adjudicate a migration.
"""

import frappe

from crm.txb.constants import OWNER_FIELDS

EXCLUDED_CREATORS = ("Administrator", "Guest")


def execute():
	for doctype, field in OWNER_FIELDS.items():
		if not frappe.get_meta(doctype).has_field(field):
			continue

		backfill(doctype, field)


def backfill(doctype: str, field: str):
	candidates = frappe.get_all(
		doctype,
		filters={field: ("is", "not set"), "owner": ("not in", EXCLUDED_CREATORS)},
		fields=["name", "owner"],
	)

	filled = 0
	for record in candidates:
		if not frappe.db.get_value("User", record.owner, "enabled"):
			continue

		frappe.db.set_value(doctype, record.name, field, record.owner, update_modified=False)
		filled += 1

	frappe.logger().info(
		f"[backfill_record_owners] {doctype}: filled {filled} of {len(candidates)} candidates"
	)

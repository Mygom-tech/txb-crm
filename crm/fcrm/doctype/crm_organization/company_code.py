"""Canonical Company Code identity for CRM Organization (TXB-243).

One place owns the Company Code contract so every Organization write -- direct inserts and
updates, plus the automated Lead-conversion, Deal/web-form and Workshop-registration creation
paths -- resolves and enforces it identically instead of each caller re-deriving the rule.

Two fields carry the identity:

- ``custom_company_code`` is the user-entered value, kept verbatim (punctuation such as
  hyphens is meaningful and preserved).
- ``custom_company_code_key`` is a hidden, read-only *normalized* key: all Unicode
  whitespace removed and case-folded. It is UNIQUE at the database level, which is the
  race-safe backstop -- two concurrent requests that both pass the in-request duplicate
  SELECT still cannot both commit the same key.

Normalized duplicates are detected against *every* Organization (there is no active/inactive
split on CRM Organization, so an unfiltered lookup already covers both). Detection also reaches
legacy rows whose key was never populated -- see ``find_organization_by_code`` -- so an
unresolved legacy collision group cannot be quietly extended by a new write.

Legacy safety: 1,600+ Organizations predate this contract with a blank code, and a handful of
populated codes already contain a normalized collision. So the contract *requires* a code for
new Organizations and whenever an existing code is supplied or changed, but grandfathers rows
that stay blank, and the migration (:func:`backfill_company_code_keys`) only claims keys for
unambiguous codes -- never inventing a value nor arbitrarily resolving a collision group.
"""

import frappe
from frappe import _

DOCTYPE = "CRM Organization"

# The user-entered Custom Field (pre-existing) and the hidden normalized key this task adds.
FIELD_COMPANY_CODE = "custom_company_code"
FIELD_COMPANY_CODE_KEY = "custom_company_code_key"


def normalize_company_code(value: str | None) -> str:
	"""Normalized identity key for a Company Code: whitespace removed, case-folded.

	Removes *all* Unicode whitespace (``str.isspace`` covers non-breaking and other Unicode
	spaces, not just ASCII) and case-folds aggressively via ``str.casefold`` so that e.g.
	``"AB 12"``, ``"ab12"`` and ``"AB 12"`` collapse to the same key. Punctuation is
	deliberately preserved, so ``"AB-12"`` stays distinct from ``"AB12"``. Returns ``""`` for
	a blank/whitespace-only input.
	"""
	if not value:
		return ""
	return "".join(ch for ch in value if not ch.isspace()).casefold()


def find_organization_by_code(key: str, exclude: str | None = None):
	"""The Organization already carrying this normalized key, or ``None``.

	Two lookups, because legacy rows may hold a populated ``custom_company_code`` whose key
	was never derived (the migration leaves collision groups unkeyed):

	1. the indexed key column -- covers new and migrated (unambiguous) rows;
	2. a narrow scan of rows that have a code but no claimed key -- covers exactly the legacy
	   collision-group members, normalized in Python since the transform is not expressible in
	   SQL. This is what stops a new write from silently joining an unresolved collision group.

	Returns a dict with ``name`` and ``organization_name`` (the label UI consumers show) so a
	duplicate failure can identify the conflicting Organization.
	"""
	if not key:
		return None

	filters = {FIELD_COMPANY_CODE_KEY: key}
	if exclude:
		filters["name"] = ["!=", exclude]
	match = frappe.db.get_value(DOCTYPE, filters, ["name", "organization_name"], as_dict=True)
	if match:
		return match

	legacy = frappe.get_all(
		DOCTYPE,
		filters={FIELD_COMPANY_CODE_KEY: ["is", "not set"], FIELD_COMPANY_CODE: ["is", "set"]},
		fields=["name", "organization_name", FIELD_COMPANY_CODE],
	)
	for row in legacy:
		if exclude and row["name"] == exclude:
			continue
		if normalize_company_code(row.get(FIELD_COMPANY_CODE)) == key:
			return frappe._dict(name=row["name"], organization_name=row.get("organization_name"))
	return None


@frappe.whitelist()
def check_company_code(company_code: str | None = None, organization: str | None = None) -> dict:
	"""Structured Company Code check for UI consumers.

	Returns ``{"valid": True}`` when the code is free, or a duplicate result identifying the
	conflicting Organization: ``{"valid": False, "reason": "duplicate", "organization": name,
	"label": organization_name}``. A blank code reports ``reason == "required"``.
	"""
	key = normalize_company_code(company_code)
	if not key:
		return {"valid": False, "reason": "required"}

	match = find_organization_by_code(key, exclude=organization)
	if match:
		return {
			"valid": False,
			"reason": "duplicate",
			"organization": match["name"],
			"label": match.get("organization_name"),
		}
	return {"valid": True}


def reuse_or_create_organization(
	organization_name: str | None,
	company_code: str | None = None,
	extra_fields: dict | None = None,
) -> str | None:
	"""Resolve the Organization for an automated caller, reusing or creating through validation.

	Reuse order: an Organization already claiming the supplied code (its authoritative
	identity), then -- to preserve legacy behaviour -- one matching the name. Otherwise a new
	Organization is created *through the document*, so the same Company Code contract applies:
	a missing code is rejected exactly as a direct insert would be, rather than bypassed.

	Race-safe on the creating branch: mirrors ``create_coaching_deal`` -- a lost duplicate-key
	race (unique key, or the pre-existing unique ``organization_name``) is recovered by reusing
	the winner instead of surfacing the raw conflict.
	"""
	if not organization_name:
		return None

	code = (company_code or "").strip()
	key = normalize_company_code(code)
	if key:
		match = find_organization_by_code(key)
		if match:
			return match["name"]

	existing = frappe.db.exists(DOCTYPE, {"organization_name": organization_name})
	if existing:
		return existing

	organization = frappe.new_doc(DOCTYPE)
	organization.update({"organization_name": organization_name})
	if code:
		organization.set(FIELD_COMPANY_CODE, code)
	if extra_fields:
		organization.update(extra_fields)

	savepoint = "txb_org_company_code"
	frappe.db.savepoint(savepoint)
	try:
		organization.insert(ignore_permissions=True)
	except frappe.UniqueValidationError:
		frappe.db.rollback(save_point=savepoint)
		winner = None
		if key:
			winner = find_organization_by_code(key)
			winner = winner["name"] if winner else None
		if not winner:
			winner = frappe.db.exists(DOCTYPE, {"organization_name": organization_name})
		if not winner:
			raise
		return winner

	return organization.name


def audit_company_codes() -> dict:
	"""Read-only audit of legacy Company Codes, without writing or inventing anything.

	Groups every Organization by its normalized key and reports:

	- ``unambiguous``: ``{key: {name, code}}`` for keys held by exactly one Organization --
	  the only rows the migration may safely claim;
	- ``collisions``: ``{key: [names]}`` for keys shared by several Organizations, left for
	  business remediation;
	- ``missing``: names of Organizations with a blank/whitespace-only code.
	"""
	rows = frappe.get_all(DOCTYPE, fields=["name", "organization_name", FIELD_COMPANY_CODE])

	groups: dict[str, list] = {}
	missing: list[str] = []
	for row in rows:
		key = normalize_company_code(row.get(FIELD_COMPANY_CODE))
		if not key:
			missing.append(row["name"])
			continue
		groups.setdefault(key, []).append(row)

	unambiguous = {
		key: {"name": rows_[0]["name"], "code": rows_[0].get(FIELD_COMPANY_CODE)}
		for key, rows_ in groups.items()
		if len(rows_) == 1
	}
	collisions = {key: [r["name"] for r in rows_] for key, rows_ in groups.items() if len(rows_) > 1}
	return {"unambiguous": unambiguous, "collisions": collisions, "missing": missing}


def backfill_company_code_keys() -> dict:
	"""Idempotently claim normalized keys for unambiguous legacy codes; report the rest.

	Only single-holder codes get a key (so the unique index can never conflict); collision
	groups and blank rows are left untouched and returned for remediation. Re-running is a
	no-op once the unambiguous keys are set.
	"""
	report = audit_company_codes()
	for key, entry in report["unambiguous"].items():
		current = frappe.db.get_value(DOCTYPE, entry["name"], FIELD_COMPANY_CODE_KEY)
		if current != key:
			frappe.db.set_value(
				DOCTYPE, entry["name"], FIELD_COMPANY_CODE_KEY, key, update_modified=False
			)
	return report

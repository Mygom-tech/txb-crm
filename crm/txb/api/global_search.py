"""Global search: Leads, Contacts, Companies and Deals from one box (Ctrl/⌘ K).

Same two passes as `crm.txb.api.people_search.search_referrers`: candidates are gathered in SQL
without permission filtering, then re-read through `frappe.get_list`, so each user's normal
Lead/Deal visibility decides what is returned and nothing is said about the rest.

A record matches when every typed word appears in one of its text columns (names, email,
company code), or when the query's digits match one of its phone numbers whatever the
formatting. Converted Leads are left out: the person lives on as a Contact and a Deal.
"""

import frappe
from frappe import _

from crm.txb.api.people_search import CANDIDATE_CAP, escape_like, search_tokens
from crm.txb.people import PHONE_MIN_DIGITS, normalize_phone

ROWS_PER_GROUP = 5

# Digits-only form of a phone column, compared on its trailing digits (crm.txb.people).
_PHONE_DIGITS = "RIGHT(REGEXP_REPLACE(COALESCE(`{column}`, ''), '[^0-9]', ''), %(phone_len)s)"

# Per DocType, in display order: what is matched, what is read back and any standing filter.
GROUPS = (
	{
		"doctype": "CRM Lead",
		"label": "Leads",
		"text": ("first_name", "last_name", "lead_name", "organization", "email"),
		"phone": ("mobile_no", "phone"),
		"fields": ("name", "lead_name", "first_name", "last_name", "email", "mobile_no", "status"),
		"filters": {"converted": 0},
	},
	{
		"doctype": "Contact",
		"label": "Contacts",
		"text": ("first_name", "last_name", "full_name", "company_name", "email_id"),
		"phone": ("mobile_no", "phone"),
		"fields": ("name", "full_name", "first_name", "last_name", "email_id", "mobile_no", "company_name"),
	},
	{
		"doctype": "CRM Organization",
		"label": "Companies",
		"text": ("organization_name", "custom_company_code", "website"),
		"phone": (),
		"fields": ("name", "organization_name", "custom_company_code", "website"),
	},
	{
		"doctype": "CRM Deal",
		"label": "Deals",
		"text": ("first_name", "last_name", "lead_name", "organization", "email"),
		"phone": ("mobile_no", "phone"),
		"fields": ("name", "first_name", "last_name", "lead_name", "organization", "pipeline_type", "status"),
	},
)


@frappe.whitelist()
def search(query: str = "") -> dict:
	"""``{"groups": [{doctype, label, total, rows: [{name, title, subtitle, badge}]}]}``.

	Every group with a visible match, in display order, holding its newest ROWS_PER_GROUP rows
	and the total the user may see. Fewer than two meaningful characters returns no groups.
	"""
	tokens = search_tokens(query)
	phone = normalize_phone(query) if sum(c.isdigit() for c in query or "") >= PHONE_MIN_DIGITS else ""
	if not tokens and not phone:
		return {"groups": []}

	groups = []
	for group in GROUPS:
		try:
			found = _search_group(group, tokens, phone)
		except Exception as error:
			# One DocType failing must not hide the others.
			frappe.log_error(f"[search] Failed to search {group['doctype']}. {error}", "TXB Global Search")
			continue
		if found:
			groups.append(found)
	return {"groups": groups}


def _search_group(group: dict, tokens: list[str], phone: str) -> dict | None:
	doctype = group["doctype"]
	candidates = _candidates(
		doctype, _columns(doctype, group["text"]), _columns(doctype, group["phone"]), tokens, phone
	)
	if not candidates:
		return None
	visible = frappe.get_list(
		doctype,
		filters={"name": ("in", list(candidates)), **group.get("filters", {})},
		fields=_columns(doctype, group["fields"]),
		order_by="modified desc",
		limit_page_length=0,
	)
	if not visible:
		return None
	return {
		"doctype": doctype,
		"label": _(group["label"]),
		"total": len(visible),
		"rows": [_row(doctype, row) for row in visible[:ROWS_PER_GROUP]],
	}


def _columns(doctype: str, columns) -> list[str]:
	"""The columns this site actually has (TxB custom fields are not on every site)."""
	return [column for column in columns if column == "name" or frappe.db.has_column(doctype, column)]


def _candidates(doctype: str, text: list[str], phones: list[str], tokens: list[str], phone: str) -> set[str]:
	"""Names of records matching the query, ignoring permissions; at most CANDIDATE_CAP."""
	values: dict[str, object] = {"cap": CANDIDATE_CAP}
	matches = []

	if tokens and text:
		clauses = []
		for index, token in enumerate(tokens):
			key = f"token_{index}"
			values[key] = f"%{escape_like(token)}%"
			clauses.append(
				"(" + " OR ".join(f"LOWER(COALESCE(`{column}`, '')) LIKE %({key})s" for column in text) + ")"
			)
		matches.append("(" + " AND ".join(clauses) + ")")

	if phone and phones:
		values["phone"], values["phone_len"] = phone, len(phone)
		matches.extend(f"{_PHONE_DIGITS.format(column=column)} = %(phone)s" for column in phones)

	if not matches:
		return set()
	rows = frappe.db.sql(
		f"SELECT `name` FROM `tab{doctype}` WHERE {' OR '.join(matches)} "
		"ORDER BY `modified` DESC LIMIT %(cap)s",
		values,
	)
	return {row[0] for row in rows}


def _person(row: dict) -> str:
	return " ".join(part for part in (row.get("first_name"), row.get("last_name")) if part).strip()


def _row(doctype: str, row: dict) -> dict:
	"""One result as the palette shows it: a title, a line of context and a badge."""
	if doctype == "CRM Lead":
		title = row.get("lead_name") or _person(row)
		subtitle, badge = row.get("email") or row.get("mobile_no"), row.get("status")
	elif doctype == "Contact":
		title = row.get("full_name") or _person(row)
		subtitle, badge = row.get("email_id") or row.get("mobile_no"), row.get("company_name")
	elif doctype == "CRM Organization":
		title = row.get("organization_name")
		subtitle, badge = row.get("website"), row.get("custom_company_code")
	else:
		title = _person(row) or row.get("lead_name") or row.get("organization")
		subtitle = " · ".join(v for v in (row.get("pipeline_type"), row.get("organization")) if v)
		badge = row.get("status")
	return {
		"name": row["name"],
		"title": title or row["name"],
		"subtitle": subtitle or "",
		"badge": badge or "",
	}

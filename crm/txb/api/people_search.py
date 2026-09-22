"""Cross-object people search shown before a Lead or Contact is created (TXB-112).

The problem it solves: the Leads list search is bound to the current View, its
filters and one doctype, so an existing Contact is invisible while the user types
a whole new Lead -- and only `prevent_duplicate` (TXB-73) tells them, after Create.
This endpoint answers "does this person already exist, anywhere?" while they type.

It is advisory. TXB-73 remains the boundary; both read their notion of an exact
duplicate from `crm.txb.people`.

Permissions: candidates are gathered without permission filtering, then re-read
through `frappe.get_list` so only permitted records are returned in full. The
difference is reported as `restricted` -- a bare count, no names. That is what
lets the UI say "a record exists but is not visible to you" without leaking it,
and it is strictly less than what TXB-73's error message already discloses.
"""

import frappe

from crm.txb.people import normalize_email, normalize_name, normalize_phone

LEAD_DOCTYPE = "CRM Lead"
CONTACT_DOCTYPE = "Contact"

# Below this a name fragment matches most of the database; the UI is debounced but
# a short query still reaches us on the way to a longer one.
MIN_NAME_LENGTH = 3

# A person's name is one or two words worth searching; more than that is a paste,
# and every extra token multiplies the OR legs in the candidate query.
MAX_NAME_TOKENS = 3

DEFAULT_LIMIT = 8
MAX_LIMIT = 20

# Ceiling on rows the candidate pass may consider. A query broad enough to exceed
# it is too broad to be a duplicate check, and the cap keeps the second pass small.
CANDIDATE_CAP = 200

# Digits-only expression for a phone column, so `+370 612 34567` and `861234567`
# compare equal. Not sargable -- see the module note in `_candidates`.
_PHONE_DIGITS = "RIGHT(REGEXP_REPLACE(COALESCE(`{column}`, ''), '[^0-9]', ''), %(phone_len)s)"

_SEARCH_COLUMNS = {
	LEAD_DOCTYPE: {
		"name_columns": ("first_name", "last_name", "lead_name"),
		"email_column": "email",
		"phone_columns": ("mobile_no", "phone"),
	},
	CONTACT_DOCTYPE: {
		"name_columns": ("first_name", "last_name"),
		"email_column": "email_id",
		"phone_columns": ("mobile_no", "phone"),
	},
}

_RESULT_FIELDS = {
	LEAD_DOCTYPE: [
		"name",
		"lead_name",
		"first_name",
		"last_name",
		"email",
		"mobile_no",
		"phone",
		"status",
		"lead_owner",
	],
	CONTACT_DOCTYPE: [
		"name",
		"first_name",
		"last_name",
		"email_id",
		"mobile_no",
		"phone",
		"custom_contact_owner",
	],
}


@frappe.whitelist()
def search_people(
	name: str = "", email: str = "", phone: str = "", limit: int = DEFAULT_LIMIT
) -> dict:
	"""Find people matching any of `name`, `email` or `phone` across Leads and Contacts.

	Returns ``{"matches": [...], "restricted": int}``. Each match carries its
	`doctype` so the caller can label it and open the right record; `strength` is
	``"exact"`` when the email or the normalized phone matched outright and
	``"possible"`` when only the name did -- a similar name alone must not stop
	anyone creating a genuinely different person.
	"""
	name = (name or "").strip()
	email = normalize_email(email)
	phone = normalize_phone(phone)

	if len(name) < MIN_NAME_LENGTH and not email and not phone:
		return {"matches": [], "restricted": 0}

	limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))

	matches = []
	restricted = 0
	tokens = _name_tokens(name)

	for doctype in (LEAD_DOCTYPE, CONTACT_DOCTYPE):
		try:
			candidates = _candidates(doctype, name, email, phone)
			if not candidates:
				continue

			visible = frappe.get_list(
				doctype,
				filters={"name": ("in", list(candidates))},
				fields=_RESULT_FIELDS[doctype],
				limit_page_length=0,
			)
			restricted += len(candidates) - len(visible)
			matches.extend(_as_match(doctype, row, email, phone, tokens) for row in visible)
		except Exception as error:
			# A search that fails must never block the create flow it is helping.
			frappe.log_error(
				f"[search_people] Failed to search {doctype}. {error}", "TXB People Search"
			)

	# Exact hits first, then whoever matched more of the typed name, then alphabetically.
	# Without the token count, "Jonas Jonaitis" ranks below every other Jonas in the
	# database and falls off the end of `limit` -- which is the bug this endpoint exists
	# to fix, reintroduced one layer up.
	matches.sort(key=lambda m: (m["strength"] != "exact", -m["score"], m["full_name"].casefold()))

	return {"matches": matches[:limit], "restricted": restricted}


def _name_tokens(name: str) -> list[str]:
	"""Normalized name words worth searching for, longest first, at most a handful.

	Each token becomes its own OR leg per column, so a two-word query costs a fixed,
	small number of conditions rather than growing with whatever was pasted in.
	"""
	tokens = [token for token in normalize_name(name).split(" ") if len(token) >= MIN_NAME_LENGTH]
	return sorted(set(tokens), key=len, reverse=True)[:MAX_NAME_TOKENS]


def _candidates(doctype: str, name: str, email: str, phone: str) -> set[str]:
	"""Names of records matching any supplied term, ignoring permissions.

	Permission filtering happens in the caller's `get_list`; this pass exists to
	learn that a hidden match *exists*. The phone leg strips non-digits in SQL, so
	it cannot use an index -- acceptable against Lead/Contact volumes, and only that
	leg pays for it because the conditions are ORed per supplied term.
	"""
	columns = _SEARCH_COLUMNS[doctype]
	conditions: list[str] = []
	values: dict[str, object] = {"cap": CANDIDATE_CAP}

	# Tokenised so "Jonas Jonaitis" still finds a Contact, which stores the two halves
	# in separate columns and has no full-name column to LIKE against.
	for index, token in enumerate(_name_tokens(name)):
		key = f"name_{index}"
		values[key] = f"%{token}%"
		conditions.extend(f"LOWER(`{column}`) LIKE %({key})s" for column in columns["name_columns"])

	if email:
		values["email"] = email
		conditions.append(f"LOWER(`{columns['email_column']}`) = %(email)s")

	if phone:
		values["phone"] = phone
		values["phone_len"] = len(phone)
		conditions.extend(
			f"{_PHONE_DIGITS.format(column=column)} = %(phone)s" for column in columns["phone_columns"]
		)

	if not conditions:
		return set()

	rows = frappe.db.sql(
		f"SELECT `name` FROM `tab{doctype}` WHERE {' OR '.join(conditions)} LIMIT %(cap)s",
		values,
		as_dict=False,
	)
	return {row[0] for row in rows}


def _as_match(doctype: str, row: dict, email: str, phone: str, tokens: list[str]) -> dict:
	"""Shape one row for the UI and decide how strong a duplicate signal it is."""
	is_lead = doctype == LEAD_DOCTYPE

	row_email = row.get("email") if is_lead else row.get("email_id")
	row_phone = row.get("mobile_no") or row.get("phone")
	full_name = (
		row.get("lead_name")
		or " ".join(part for part in (row.get("first_name"), row.get("last_name")) if part).strip()
		or row["name"]
	)

	# The candidate pass ORs both phone columns, so either one matching is exact.
	phone_hit = phone and phone in {
		normalize_phone(row.get("mobile_no")),
		normalize_phone(row.get("phone")),
	}
	exact = bool(email and normalize_email(row_email) == email) or bool(phone_hit)

	# How much of the typed name this record accounts for. Ranking only -- a name
	# match of any width stays "possible".
	haystack = normalize_name(full_name)
	score = sum(1 for token in tokens if token in haystack)

	return {
		"doctype": doctype,
		"name": row["name"],
		"full_name": full_name,
		"score": score,
		"email": row_email,
		"phone": row_phone,
		"status": row.get("status"),
		"owner": row.get("lead_owner") if is_lead else row.get("custom_contact_owner"),
		"strength": "exact" if exact else "possible",
	}


# --- Referred By person search (TXB-254) -------------------------------------------------
#
# The Lead-or-Contact Referred By picker needs one list over both DocTypes, which the stock
# Dynamic Link control cannot give (it searches only the discriminator-selected DocType). Unlike
# `search_people` this is a picker, not a duplicate check: only readable records are returned and
# nothing is said about the rest. Converted (archived) Leads are deliberately included -- a past
# Lead is still a person who can refer someone.

# A partial first or last name: two letters is already a useful prefix ("Jo", "Al").
REFERRER_MIN_TOKEN_LENGTH = 2

_REFERRER_NAME_COLUMNS = {
	LEAD_DOCTYPE: ("first_name", "last_name", "lead_name"),
	CONTACT_DOCTYPE: ("first_name", "last_name", "full_name"),
}

_REFERRER_FIELDS = {
	LEAD_DOCTYPE: [
		"name",
		"lead_name",
		"first_name",
		"last_name",
		"email",
		"mobile_no",
		"phone",
		"converted",
	],
	CONTACT_DOCTYPE: ["name", "full_name", "first_name", "last_name", "email_id", "mobile_no", "phone"],
}


@frappe.whitelist()
def search_referrers(
	query: str = "", exclude_lead: str | None = None, limit: int = DEFAULT_LIMIT
) -> list[dict]:
	"""Readable CRM Leads (active and converted) and Contacts whose name matches `query`.

	Every word of `query` must appear in some name column -- first, last or full name -- so
	"Jon" finds Jonas and Jonaitis alike and "Jonas Jon" narrows to Jonas Jonaitis. Rows are
	re-read through `frappe.get_list`, so the caller's normal read permissions decide what is
	returned. `exclude_lead` drops the Lead being edited, which cannot refer itself.

	Each row is ``{"doctype", "name", "full_name", "email", "mobile_no", "converted"}``;
	``doctype`` + ``name`` is exactly the typed reference to store.
	"""
	tokens = _referrer_tokens(query)
	if not tokens:
		return []

	limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
	results = []

	for doctype in (LEAD_DOCTYPE, CONTACT_DOCTYPE):
		candidates = _referrer_candidates(doctype, tokens)
		if doctype == LEAD_DOCTYPE and exclude_lead:
			candidates.discard(exclude_lead)
		if not candidates:
			continue

		visible = frappe.get_list(
			doctype,
			filters={"name": ("in", list(candidates))},
			fields=_REFERRER_FIELDS[doctype],
			limit_page_length=0,
		)
		results.extend(_as_referrer(doctype, row) for row in visible)

	# Names that start with the typed text first, then alphabetically, Leads before Contacts on
	# a tie so the ordering is stable.
	prefix = normalize_name(query)
	results.sort(
		key=lambda r: (
			not normalize_name(r["full_name"]).startswith(prefix),
			r["full_name"].casefold(),
			r["doctype"],
			r["name"],
		)
	)
	return results[:limit]


def _referrer_tokens(query: str) -> list[str]:
	tokens = [t for t in normalize_name(query).split(" ") if len(t) >= REFERRER_MIN_TOKEN_LENGTH]
	return sorted(set(tokens), key=len, reverse=True)[:MAX_NAME_TOKENS]


def _referrer_candidates(doctype: str, tokens: list[str]) -> set[str]:
	"""Names of records whose name columns contain every token, ignoring permissions."""
	columns = _REFERRER_NAME_COLUMNS[doctype]
	values: dict[str, object] = {"cap": CANDIDATE_CAP}
	clauses = []

	for index, token in enumerate(tokens):
		key = f"token_{index}"
		values[key] = f"%{_escape_like(token)}%"
		clauses.append(
			"(" + " OR ".join(f"LOWER(COALESCE(`{column}`, '')) LIKE %({key})s" for column in columns) + ")"
		)

	rows = frappe.db.sql(
		f"SELECT `name` FROM `tab{doctype}` WHERE {' AND '.join(clauses)} "
		"ORDER BY `modified` DESC LIMIT %(cap)s",
		values,
		as_dict=False,
	)
	return {row[0] for row in rows}


def _escape_like(value: str) -> str:
	return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _as_referrer(doctype: str, row: dict) -> dict:
	is_lead = doctype == LEAD_DOCTYPE
	parts = " ".join(part for part in (row.get("first_name"), row.get("last_name")) if part).strip()
	full_name = (row.get("lead_name") if is_lead else row.get("full_name")) or parts or row["name"]

	return {
		"doctype": doctype,
		"name": row["name"],
		"full_name": full_name,
		"email": (row.get("email") if is_lead else row.get("email_id")) or None,
		"mobile_no": row.get("mobile_no") or row.get("phone") or None,
		"converted": bool(row.get("converted")) if is_lead else False,
	}

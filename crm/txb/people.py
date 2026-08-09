"""Shared people-matching primitives for Leads and Contacts (TXB-112).

Two features have to agree about what "the same person" means: the pre-Create
search (`crm.txb.api.people_search`) and the Create-time duplicate block
(`crm.txb.doc_events.lead.prevent_duplicate`, TXB-73). If the search says
"exact duplicate" the insert must be refused, and if the insert is refused the
search must have shown it. They therefore share the predicates below instead of
each rolling its own comparison.

`find_exact_duplicate` is TXB-73's rule moved here verbatim -- what it blocks is
unchanged. The `normalize_*` helpers are used only by the search, which is
advisory and deliberately looser than the block.
"""

import re

import frappe

_NON_DIGITS = re.compile(r"\D+")
_WHITESPACE = re.compile(r"\s+")

# A phone matches on its trailing digits, so the same number written
# `+370 612 34567`, `861234567` or `(8-612) 34567` collapses to one value.
# Eight covers a Lithuanian subscriber number without the country/trunk prefix.
PHONE_SIGNIFICANT_DIGITS = 8

# Fewer digits than this matches half the database, so such a fragment is ignored
# rather than turned into noise.
PHONE_MIN_DIGITS = 6


def normalize_name(value: str | None) -> str:
	"""Case- and whitespace-insensitive form of a name, for comparison only."""
	return _WHITESPACE.sub(" ", (value or "").strip()).casefold()


def normalize_email(value: str | None) -> str:
	"""Case-insensitive form of an email, for comparison only."""
	return (value or "").strip().casefold()


def normalize_phone(value: str | None) -> str:
	"""Trailing significant digits of a phone number, or "" if too short to be useful.

	Formatting, spacing, country code and trunk prefix all drop out, so the two
	spellings a user is most likely to type resolve to the same key.
	"""
	digits = _NON_DIGITS.sub("", value or "")
	if len(digits) < PHONE_MIN_DIGITS:
		return ""
	return digits[-PHONE_SIGNIFICANT_DIGITS:]


def find_exact_duplicate(
	first_name: str | None, last_name: str | None, email: str | None
) -> str | None:
	"""TXB-73's duplicate rule: same first name, last name and email.

	Returns ``"Contact"``, ``"Lead"`` or ``None``. Records without a first name or
	without an email are never duplicates, matching the original Server Script.

	Deliberately *not* routed through the `normalize_*` helpers above: this decides
	whether an insert is refused, and loosening it would start rejecting records the
	system accepts today. Comparison is whatever the database collation does, which
	on MariaDB's `utf8mb4_unicode_ci` is already case-insensitive.
	"""
	first_name = (first_name or "").strip()
	last_name = (last_name or "").strip()
	email = (email or "").strip()

	if not first_name or not email:
		return None

	# `("is", "not set")` is how the original expressed "last name is empty".
	last_name_filter = last_name if last_name else ("is", "not set")

	if frappe.db.count(
		"Contact",
		filters={"first_name": first_name, "last_name": last_name_filter, "email_id": email},
	):
		return "Contact"

	if frappe.db.count(
		"CRM Lead",
		filters={"first_name": first_name, "last_name": last_name_filter, "email": email},
	):
		return "Lead"

	return None

"""Replace predictable registration tokens and retire the Server Script that made them.

The `Generate Registration Token` Server Script derived tokens from ``now() + doc.name``,
stripped non-alphanumerics and kept the last 16 characters. A stripped deal name is itself
16 characters long, so the timestamp was discarded and every token was just the deal number
in lower case (``CRM-DEAL-2026-00356`` -> ``crmdeal202600356``).

Those tokens guard a guest-accessible endpoint that creates Contacts, Organizations and
Deals, so they were enumerable by anyone. Any token still matching that pattern is reissued
from `secrets`.

Existing registration links become invalid -- that is the point, since the old ones are
public knowledge by construction. Anyone holding a live link needs a fresh one.
"""

import secrets

import frappe

from crm.txb.constants import (
	FIELD_REGISTRATION_LINK,
	FIELD_REGISTRATION_TOKEN,
	REGISTRATION_BASE_URL,
	REGISTRATION_TOKEN_BYTES,
)

SERVER_SCRIPT = "Generate Registration Token"


def execute():
	disable_superseded_server_script()
	reissue_predictable_tokens()


def disable_superseded_server_script():
	"""Retire the Server Script now that `crm.txb.doc_events.deal` owns this logic.

	Without this the hook and the script both run; the script would then overwrite the
	secure token with a predictable one.
	"""
	if not frappe.db.exists("Server Script", SERVER_SCRIPT):
		return

	if frappe.db.get_value("Server Script", SERVER_SCRIPT, "disabled"):
		return

	frappe.db.set_value("Server Script", SERVER_SCRIPT, "disabled", 1)
	frappe.clear_cache(doctype="CRM Deal")


def reissue_predictable_tokens():
	if not frappe.db.has_column("CRM Deal", FIELD_REGISTRATION_TOKEN):
		return

	deals = frappe.get_all(
		"CRM Deal",
		filters={FIELD_REGISTRATION_TOKEN: ["is", "set"]},
		fields=["name", FIELD_REGISTRATION_TOKEN],
	)

	for deal in deals:
		if not is_predictable(deal.name, deal.get(FIELD_REGISTRATION_TOKEN)):
			continue

		token = secrets.token_urlsafe(REGISTRATION_TOKEN_BYTES)
		frappe.db.set_value(
			"CRM Deal",
			deal.name,
			{
				FIELD_REGISTRATION_TOKEN: token,
				FIELD_REGISTRATION_LINK: f"{REGISTRATION_BASE_URL}/registration?token={token}",
			},
			update_modified=False,
		)
		frappe.logger().info(f"[reissue_registration_tokens] Reissued token for {deal.name}")


def is_predictable(deal_name: str, token: str) -> bool:
	"""True when the token is the old deal-name-derived value.

	Tokens that already look random are left alone, so the patch is safe to re-run.
	"""
	if not token:
		return False

	return token == "".join(c for c in deal_name if c.isalnum())[-16:].lower()

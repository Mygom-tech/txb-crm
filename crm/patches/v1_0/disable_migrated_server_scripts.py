"""Retire the Server Scripts now owned by `crm.txb`, and repoint the registration page.

Server Scripts live as database rows: invisible to git, unreviewable, and free to drift
between environments. Their logic now lives in `crm/txb/`, wired through `hooks.py`.

This patch must land in the same deploy as that code. Until the rows are disabled both
copies run -- harmless for the idempotent handlers, but duplicated work and, for the
registration token, actively wrong.

The two API scripts also change address. `/api/method/process_registration` only resolved
because a Server Script claimed that name; the whitelisted functions answer on their
dotted paths instead. The published registration page calls those URLs, so it is rewritten
here rather than left pointing at endpoints that no longer exist.

The script list moved to `crm.txb.retired_scripts`, which re-asserts it on every migrate
rather than once per site. The page rewrite stays here: it is a genuine one-off.
"""

import frappe

from crm.txb.retired_scripts import retire_scripts

# `Generate Registration Token` is retired by reissue_registration_tokens, which runs first.

REGISTRATION_PAGE = "registracija"

ENDPOINT_REWRITES = (
	(
		"/api/method/process_registration",
		"/api/method/crm.txb.api.registration.process_registration",
	),
	(
		"/api/method/validate_registration_token",
		"/api/method/crm.txb.api.registration.validate_registration_token",
	),
)


def execute():
	retire_scripts()
	repoint_registration_page()


def repoint_registration_page():
	"""Rewrite the published page's API calls to the new dotted paths.

	Idempotent by construction: an old path never occurs inside its replacement, since the
	new one puts the module path directly after `/api/method/`. After the first run there
	are no old occurrences left to match.
	"""
	if not frappe.db.exists("Web Page", REGISTRATION_PAGE):
		return

	page = frappe.get_doc("Web Page", REGISTRATION_PAGE)
	content = page.main_section or ""
	if not content:
		return

	updated = content
	for old, new in ENDPOINT_REWRITES:
		updated = updated.replace(old, new)

	if updated == content:
		return

	page.main_section = updated
	page.save(ignore_permissions=True)
	frappe.logger().info(f"[disable_migrated_server_scripts] Repointed {REGISTRATION_PAGE} endpoints")

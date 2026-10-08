"""Delivery Coach assignment notifications: configuration, ledger contract and email template.

The foundation only (TXB-269). Nothing here detects an assignment, calls Slack or sends mail;
it gives the notification consumer three things to build on:

* Site-specific settings on FCRM Settings -- a delivery mode (Disabled / Test redirect / Live),
  the Test redirect recipient, and the Slack bot token as an encrypted Password. Every site
  starts Disabled; Live is only ever reached by an operator switching it on.
* The `CRM Coach Assignment Notification` ledger: one row per committed assignment event,
  unique on `event_key`, with independent email and Slack states so a channel can be retried
  without resending the other or one already Sent. Its captured routing and assignment
  generation never change after insert; each channel counts its attempts and stamps its
  latest claim and recovery (TXB-288).
* An editable Email Template in the TXB-116 wrapper and signature, rendered from
  `build_assignment_email_context`.

Only a System Manager may change the settings (`crm.txb.permissions.guard_notification_settings`).
The decrypted Slack token is never stored outside Frappe's encrypted password store and is
redacted from every error summary written to the ledger.
"""

import hashlib
import re

import frappe
from bs4 import BeautifulSoup
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import format_datetime, get_url
from frappe.utils.password import get_decrypted_password

from crm.txb.constants import CONFIRMATION_TEMPLATE, SETTING_FIRST_CALL_REMINDER_MINUTES

SETTINGS_DOCTYPE = "FCRM Settings"
LEDGER_DOCTYPE = "CRM Coach Assignment Notification"

SETTING_MODE = "delivery_coach_notification_mode"
SETTING_TEST_USER = "delivery_coach_notification_test_user"
SETTING_SLACK_BOT_TOKEN = "slack_bot_token"

MODE_DISABLED = "Disabled"
MODE_TEST_REDIRECT = "Test redirect"
MODE_LIVE = "Live"
MODES = (MODE_DISABLED, MODE_TEST_REDIRECT, MODE_LIVE)

CHANNELS = ("email", "slack")
STATUS_PENDING = "Pending"
STATUS_SENT = "Sent"
STATUS_FAILED = "Failed"
STATUS_SKIPPED = "Skipped"
# The provider may or may not have accepted the message (a timeout after the request left).
STATUS_UNCERTAIN = "Uncertain"

# Which channel state may follow which. Sent and Skipped are final, so a retry can never
# resend a delivered channel; Failed may be retried (back to Pending, or straight to an
# outcome); Pending may reach any outcome. Uncertain is held for reconciliation: never back to
# Pending, so it is not resent automatically -- only confirmed Sent, Failed or Skipped.
CHANNEL_TRANSITIONS = {
	STATUS_PENDING: {STATUS_PENDING, STATUS_SENT, STATUS_FAILED, STATUS_SKIPPED, STATUS_UNCERTAIN},
	STATUS_FAILED: {STATUS_FAILED, STATUS_PENDING, STATUS_SENT, STATUS_SKIPPED, STATUS_UNCERTAIN},
	STATUS_UNCERTAIN: {STATUS_UNCERTAIN, STATUS_SENT, STATUS_FAILED, STATUS_SKIPPED},
	STATUS_SENT: {STATUS_SENT},
	STATUS_SKIPPED: {STATUS_SKIPPED},
}
# The states a delivery attempt may be claimed from.
CLAIMABLE_STATUSES = (STATUS_PENDING, STATUS_FAILED)

ASSIGNMENT_EMAIL_TEMPLATE = "TxB Delivery Coach Assignment"
ERROR_SUMMARY_MAX_LENGTH = 500
# Shown for a blank organization or program type, so every row of the message is present.
NOT_PROVIDED = "Not provided"

SETTINGS_FIELDS = [
	{
		"fieldname": SETTING_MODE,
		"fieldtype": "Select",
		"label": "Delivery Coach Notification Mode",
		"options": "\n".join(MODES),
		"default": MODE_DISABLED,
		"description": (
			"Disabled sends nothing. Test redirect sends every Delivery Coach assignment "
			"notification to the Test Recipient, labelled with the intended coach. Live sends "
			"to the assigned coach."
		),
		"insert_after": SETTING_FIRST_CALL_REMINDER_MINUTES,
	},
	{
		"fieldname": SETTING_TEST_USER,
		"fieldtype": "Link",
		"options": "User",
		"label": "Delivery Coach Notification Test Recipient",
		"description": "Receives both email and Slack notifications while the mode is Test redirect.",
		"insert_after": SETTING_MODE,
	},
	{
		"fieldname": SETTING_SLACK_BOT_TOKEN,
		"fieldtype": "Password",
		"label": "Slack Bot Token",
		"description": "Bot token used to send private Slack notifications. Stored encrypted.",
		"length": 255,
		"insert_after": SETTING_TEST_USER,
	},
]

_SUBJECT = (
	"{% if is_test_redirect %}[TEST for {{ intended_coach_name }}] {% endif %}"
	"New client assigned: {{ client_name }}"
)

# The assignment content only. The branded wrapper and signature around it are not kept here:
# they are TXB-116's, taken from the site's confirmation template by `build_assignment_email_body`.
# Values are escaped, since a guest registration names the client.
_CONTENT = (
	"{% if is_test_redirect %}"
	'<p style="background: #fff4e5; padding: 10px; border: 1px solid #f5a623;">'
	"<strong>Test notification.</strong> This assignment is intended for "
	"<strong>{{ intended_coach_name | e }}</strong>.</p>"
	"{% endif %}"
	"<p>Hi {{ intended_coach_name | e }},</p>"
	"<p>{{ assigned_by_name | e }} assigned a client to you as Delivery Coach on "
	"{{ assigned_at | e }}.</p>"
	"<table>"
	"<tr><td>Client:</td><td>{{ client_name | e }}</td></tr>"
	"<tr><td>Organization:</td><td>{{ organization | e }}</td></tr>"
	"<tr><td>Program:</td><td>{{ program_type | e }}</td></tr>"
	"<tr><td>Status:</td><td>{{ status | e }}</td></tr>"
	"</table>"
	'<p><a href="{{ opportunity_url | e }}">Open the Opportunity in CRM</a></p>'
)

# SHA-256 of the body TXB-269 seeded with its own navy header and English signature. A template
# still holding exactly that -- or the bare `_CONTENT` -- was never edited and may be re-seeded.
_UNEDITED_SEED_SHA256 = "0be7b47f2a590a8b7de3ca4d6bf56a962c95c161e43d8e8224fc43764e222aed"

# The confirmation's greeting, which sits in the TXB-116 padded content container.
_GREETING_RE = re.compile(r"\{\{\s*first_name\s*\}\}")
_CONTENT_SLOT = "txb-assignment-content-slot"

_TOKEN_PATTERNS = (
	re.compile(r"xox[a-z]-[A-Za-z0-9-]+"),
	re.compile(r"xapp-[A-Za-z0-9-]+"),
	re.compile(r"(?i)bearer\s+\S+"),
)


def ensure_notification_settings() -> None:
	"""Add the settings fields a site is missing and pin a blank mode to Disabled.

	Idempotent: fields are only ever added, and an operator's chosen mode is never touched.
	"""
	meta = frappe.get_meta(SETTINGS_DOCTYPE)
	missing = [field for field in SETTINGS_FIELDS if not meta.has_field(field["fieldname"])]
	if missing:
		create_custom_fields({SETTINGS_DOCTYPE: missing})
		frappe.clear_cache(doctype=SETTINGS_DOCTYPE)

	if not frappe.db.get_single_value(SETTINGS_DOCTYPE, SETTING_MODE):
		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_MODE, MODE_DISABLED)


def build_assignment_email_body() -> str:
	"""The assignment content inside this site's TXB-116 wrapper and signature.

	The TXB-116 wrapper is not code: it is the operator-authored registration confirmation
	Email Template (`CONFIRMATION_TEMPLATE`) -- branded header, padded content container ending
	in the signature, footer. Its content container (the element holding the greeting) gets
	the assignment content in place of the registration text; the header, footer and the
	container's closing signature paragraph are reused as they are. A confirmation with no
	recognisable container gives the bare content, never a second, independent brand.
	"""
	html = frappe.db.get_value("Email Template", CONFIRMATION_TEMPLATE, "response_html")
	soup = BeautifulSoup(html or "", "html.parser")
	greeting = soup.find(string=_GREETING_RE)
	container = greeting.find_parent(["div", "td"]) if greeting else None
	# A container holding all the text is the whole email, not the content between header and footer.
	if container is None or container.get_text(strip=True) == soup.get_text(strip=True):
		return _CONTENT

	children = container.find_all(True, recursive=False)
	last = children[-1] if children else None
	signature = str(last) if last is not None and last.name == "p" and "{" not in str(last) else ""
	container.clear()
	container.append(_CONTENT_SLOT)
	return str(soup).replace(_CONTENT_SLOT, _CONTENT + signature, 1)


def _is_unedited_seed(body: str | None) -> bool:
	return body == _CONTENT or hashlib.sha256((body or "").encode()).hexdigest() == _UNEDITED_SEED_SHA256


def ensure_assignment_email_template() -> None:
	"""Seed the assignment Email Template, or bring a never-edited seed to the TXB-116 body.

	Any other body is the operator's and is left exactly as it is, so re-running the migration
	keeps their edits.
	"""
	body = build_assignment_email_body()
	if not frappe.db.exists("Email Template", ASSIGNMENT_EMAIL_TEMPLATE):
		frappe.get_doc(
			{
				"doctype": "Email Template",
				"name": ASSIGNMENT_EMAIL_TEMPLATE,
				"subject": _SUBJECT,
				"use_html": 1,
				"response_html": body,
			}
		).insert(ignore_permissions=True)
		return

	template = frappe.get_doc("Email Template", ASSIGNMENT_EMAIL_TEMPLATE)
	if template.response_html != body and _is_unedited_seed(template.response_html):
		template.response_html = body
		template.save(ignore_permissions=True)


def get_notification_config() -> dict:
	"""The site's mode and Test redirect recipient, failing closed.

	An unknown or blank mode, or Test redirect with no recipient, reads as Disabled so a
	half-configured site never contacts anyone -- least of all a real coach.
	"""
	meta = frappe.get_meta(SETTINGS_DOCTYPE)
	if not meta.has_field(SETTING_MODE):
		return {"mode": MODE_DISABLED, "test_user": None}

	mode = frappe.db.get_single_value(SETTINGS_DOCTYPE, SETTING_MODE)
	test_user = frappe.db.get_single_value(SETTINGS_DOCTYPE, SETTING_TEST_USER) or None
	if mode not in MODES or (mode == MODE_TEST_REDIRECT and not test_user):
		mode = MODE_DISABLED
	return {"mode": mode, "test_user": test_user}


def get_slack_bot_token() -> str | None:
	"""The decrypted bot token for an outbound Slack call. Never log or persist the result."""
	return get_decrypted_password(
		SETTINGS_DOCTYPE, SETTINGS_DOCTYPE, SETTING_SLACK_BOT_TOKEN, raise_exception=False
	)


def sanitize_error(error) -> str | None:
	"""A short, secret-free error summary safe to store on the ledger."""
	if not error:
		return None
	text = str(error)
	token = get_slack_bot_token()
	if token:
		text = text.replace(token, "[redacted]")
	for pattern in _TOKEN_PATTERNS:
		text = pattern.sub("[redacted]", text)
	text = " ".join(text.split())
	if len(text) > ERROR_SUMMARY_MAX_LENGTH:
		text = text[: ERROR_SUMMARY_MAX_LENGTH - 3] + "..."
	return text


def _user_name(user: str | None) -> str:
	if not user:
		return ""
	return frappe.db.get_value("User", user, "full_name") or user


def _client_name(deal) -> str:
	parts = [deal.get("first_name"), deal.get("last_name")]
	name = " ".join(part for part in parts if part)
	return name or deal.get("lead_name") or deal.get("organization") or deal.name


def build_assignment_email_context(
	deal, intended_user: str, assigned_by: str, assigned_at, is_test_redirect: bool
) -> dict:
	"""The documented context of the assignment Email Template.

	The Opportunity URL is built from this site's own address, so each environment links to
	itself. A blank organization or program type reads "Not provided" in both messages.
	"""
	return {
		"client_name": _client_name(deal),
		"organization": (deal.get("organization") or "").strip() or NOT_PROVIDED,
		"program_type": (deal.get("custom_program_type") or "").strip() or NOT_PROVIDED,
		"status": deal.get("status") or "",
		"assigned_by_name": _user_name(assigned_by),
		"assigned_at": format_datetime(assigned_at),
		"opportunity_url": get_url(f"/crm/deals/{deal.name}"),
		"intended_coach_name": _user_name(intended_user),
		"is_test_redirect": bool(is_test_redirect),
	}


def render_assignment_email(context: dict) -> tuple[str, str]:
	"""(subject, html body) of the assignment Email Template for `context`."""
	template = frappe.get_doc("Email Template", ASSIGNMENT_EMAIL_TEMPLATE)
	body = template.response_html if template.use_html else template.response
	return (
		frappe.render_template(template.subject, context),
		frappe.render_template(body, context),
	)

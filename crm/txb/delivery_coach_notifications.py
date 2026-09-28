"""Delivery Coach assignment notifications: configuration, ledger contract and email template.

The foundation only (TXB-269). Nothing here detects an assignment, calls Slack or sends mail;
it gives the notification consumer three things to build on:

* Site-specific settings on FCRM Settings -- a delivery mode (Disabled / Test redirect / Live),
  the Test redirect recipient, and the Slack bot token as an encrypted Password. Every site
  starts Disabled; Live is only ever reached by an operator switching it on.
* The `CRM Coach Assignment Notification` ledger: one row per committed assignment event,
  unique on `event_key`, with independent email and Slack states so a channel can be retried
  without resending the other or one already Sent.
* An editable Email Template in the TxB wrapper, rendered from `build_assignment_email_context`.

The decrypted Slack token is never stored outside Frappe's encrypted password store and is
redacted from every error summary written to the ledger.
"""

import re

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import format_datetime, get_url
from frappe.utils.password import get_decrypted_password

from crm.txb.constants import SETTING_FIRST_CALL_REMINDER_MINUTES

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

# Which channel state may follow which. Sent and Skipped are final, so a retry can never
# resend a delivered channel; Failed may be retried (back to Pending, or straight to an
# outcome); Pending may reach any outcome.
CHANNEL_TRANSITIONS = {
	STATUS_PENDING: {STATUS_PENDING, STATUS_SENT, STATUS_FAILED, STATUS_SKIPPED},
	STATUS_FAILED: {STATUS_FAILED, STATUS_PENDING, STATUS_SENT, STATUS_SKIPPED},
	STATUS_SENT: {STATUS_SENT},
	STATUS_SKIPPED: {STATUS_SKIPPED},
}

ASSIGNMENT_EMAIL_TEMPLATE = "TxB Delivery Coach Assignment"
ERROR_SUMMARY_MAX_LENGTH = 500

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

# The TxB email wrapper: outer container, branded navy header, padded content, signature footer.
_BODY = (
	'<div style="max-width: 600px; margin: 0 auto; font-family: Arial, sans-serif;">'
	'<div style="background: #002d5b; padding: 20px; text-align: center; color: #ffffff;'
	' font-size: 22px; font-weight: bold;">TxB</div>'
	'<div style="padding: 30px; color: #333;">'
	"{% if is_test_redirect %}"
	'<p style="background: #fff4e5; padding: 10px; border: 1px solid #f5a623;">'
	"<strong>Test notification.</strong> This assignment is intended for "
	"<strong>{{ intended_coach_name }}</strong>.</p>"
	"{% endif %}"
	"<p>Hi {{ intended_coach_name }},</p>"
	"<p>{{ assigned_by_name }} assigned a client to you as Delivery Coach on {{ assigned_at }}.</p>"
	"<table>"
	"<tr><td>Client:</td><td>{{ client_name }}</td></tr>"
	"{% if organization %}<tr><td>Organization:</td><td>{{ organization }}</td></tr>{% endif %}"
	"{% if program_type %}<tr><td>Program:</td><td>{{ program_type }}</td></tr>{% endif %}"
	"<tr><td>Status:</td><td>{{ status }}</td></tr>"
	"</table>"
	'<p><a href="{{ opportunity_url }}">Open the Opportunity in CRM</a></p>'
	"</div>"
	'<div style="background: #f4f4f4; padding: 20px; color: #666; font-size: 12px;">'
	"<p>Best regards,<br>TxB team</p>"
	"</div>"
	"</div>"
)

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


def ensure_assignment_email_template() -> None:
	"""Seed the assignment Email Template once; an existing one is the operator's to edit."""
	if frappe.db.exists("Email Template", ASSIGNMENT_EMAIL_TEMPLATE):
		return
	frappe.get_doc(
		{
			"doctype": "Email Template",
			"name": ASSIGNMENT_EMAIL_TEMPLATE,
			"subject": _SUBJECT,
			"use_html": 1,
			"response_html": _BODY,
		}
	).insert(ignore_permissions=True)


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
	itself.
	"""
	return {
		"client_name": _client_name(deal),
		"organization": deal.get("organization") or "",
		"program_type": (deal.get("custom_program_type") or "").strip(),
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

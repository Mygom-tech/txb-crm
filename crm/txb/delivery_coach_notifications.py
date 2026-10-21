"""Delivery Coach notifications: configuration, ledger contract and email templates.

The foundation only (TXB-269). Nothing here detects an assignment or sends mail; it gives the
notification consumer three things to build on:

* Site-specific settings on FCRM Settings -- a delivery mode (Disabled / Test redirect / Live)
  and the Test redirect recipient. Every site starts Disabled; Live is only ever reached by an
  operator switching it on.
* The `CRM Coach Assignment Notification` ledger: one row per committed event, unique on
  `event_key`, with independent coach email and client email states so a channel can be
  retried without resending the other or one already Sent. Its captured routing and assignment
  generation never change after insert; each channel counts its attempts and stamps its
  latest claim and recovery (TXB-288).
* Two editable Email Templates holding content only -- the site email shell
  (`crm/templates/emails/standard.html`) brands every email: the coach's, with the client's
  details, and the client's (Lithuanian), both rendered from `build_assignment_email_context`.

Only a System Manager may change the settings (`crm.txb.permissions.guard_notification_settings`).
"""

import hashlib
import re

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import format_datetime, get_url

from crm.txb.constants import CONFIRMATION_TEMPLATE, SETTING_FIRST_CALL_REMINDER_MINUTES

SETTINGS_DOCTYPE = "FCRM Settings"
LEDGER_DOCTYPE = "CRM Coach Assignment Notification"

SETTING_MODE = "delivery_coach_notification_mode"
SETTING_TEST_USER = "delivery_coach_notification_test_user"

MODE_DISABLED = "Disabled"
MODE_TEST_REDIRECT = "Test redirect"
MODE_LIVE = "Live"
MODES = (MODE_DISABLED, MODE_TEST_REDIRECT, MODE_LIVE)

# The coach's email and the client's email; both go to the Test Recipient in Test redirect.
CHANNELS = ("email", "client_email")
STATUS_PENDING = "Pending"
STATUS_SENT = "Sent"
STATUS_FAILED = "Failed"
STATUS_SKIPPED = "Skipped"
# The provider may or may not have accepted the message. Kept for rows recorded while a Slack
# channel existed; no current sender reports it.
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
CLIENT_EMAIL_TEMPLATE = "TxB Coach Assigned to Client"
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
		"description": "Receives both the coach's and the client's email while the mode is Test redirect.",
		"insert_after": SETTING_MODE,
	},
]

_SUBJECT = (
	"{% if is_test_redirect %}[TEST for {{ intended_coach_name }}] {% endif %}"
	"New client assigned: {{ client_name }}"
)

# The assignment content only; the site email shell brands it. Values are escaped, since a guest
# registration names the client.
_CONTENT = (
	"{% if is_test_redirect %}"
	'<p style="background: #fff4e5; padding: 10px; border: 1px solid #f5a623;">'
	"<strong>Test notification.</strong> This assignment is intended for "
	"<strong>{{ intended_coach_name | e }}</strong>.</p>"
	"{% endif %}"
	"<p>Hi {{ intended_coach_name | e }},</p>"
	"<p>{{ assigned_by_name | e }} assigned a client to you as Delivery Coach on "
	"{{ assigned_at | e }}. Please reach out to them within the next 48 hours.</p>"
	"<table>"
	"<tr><td>Client:</td><td>{{ client_name | e }}</td></tr>"
	"<tr><td>Organization:</td><td>{{ organization | e }}</td></tr>"
	"<tr><td>Email:</td><td>{{ client_email | e }}</td></tr>"
	"<tr><td>Phone:</td><td>{{ client_phone | e }}</td></tr>"
	"<tr><td>Program:</td><td>{{ program_type | e }}</td></tr>"
	"<tr><td>Status:</td><td>{{ status | e }}</td></tr>"
	"</table>"
	'<p><a href="{{ opportunity_url | e }}">Open the Opportunity in CRM</a></p>'
)

# The TXB-288 content before the client's email, phone and the 48-hour line were added. A
# template still holding it (bare or wrapped) was never edited and may be re-seeded.
_PREVIOUS_CONTENT = (
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

_CLIENT_SUBJECT = "{% if is_test_redirect %}[TEST for {{ client_name }}] {% endif %}Jums paskirtas koučeris"

# The client's message, in Lithuanian like the registration confirmation it is wrapped in.
_CLIENT_CONTENT = (
	"{% if is_test_redirect %}"
	'<p style="background: #fff4e5; padding: 10px; border: 1px solid #f5a623;">'
	"<strong>Test notification.</strong> This email is intended for "
	"<strong>{{ client_name | e }}</strong> ({{ client_email | e }}).</p>"
	"{% endif %}"
	"<p>Sveiki, {{ client_first_name | e }},</p>"
	"<p>Jums paskirtas koučeris <strong>{{ intended_coach_name | e }}</strong>"
	"{% if client_program %} programai „{{ client_program | e }}“{% endif %}.</p>"
	"<p>Koučeris susisieks su Jumis per artimiausias 48 valandas.</p>"
	"<p>Pagarbiai,<br>TXB komanda</p>"
)

# SHA-256 of the body TXB-269 seeded with its own navy header and English signature. A template
# still holding exactly that -- or the bare `_CONTENT` -- was never edited and may be re-seeded.
_UNEDITED_SEED_SHA256 = "0be7b47f2a590a8b7de3ca4d6bf56a962c95c161e43d8e8224fc43764e222aed"

# Credential-shaped strings scrubbed from every stored error summary (the coaching recap uses it too).
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


def _is_unedited_seed(body: str | None) -> bool:
	return (
		body in (_CONTENT, _PREVIOUS_CONTENT)
		or hashlib.sha256((body or "").encode()).hexdigest() == _UNEDITED_SEED_SHA256
	)


def ensure_assignment_email_template() -> None:
	"""Seed the coach's Email Template, or bring a never-edited seed to the current body.

	Any other body is the operator's and is left exactly as it is, so re-running the migration
	keeps their edits.
	"""
	if not _seed_template(ASSIGNMENT_EMAIL_TEMPLATE, _SUBJECT, _CONTENT):
		template = frappe.get_doc("Email Template", ASSIGNMENT_EMAIL_TEMPLATE)
		if template.response_html != _CONTENT and _is_unedited_seed(template.response_html):
			template.response_html = _CONTENT
			template.save(ignore_permissions=True)


def ensure_client_email_template() -> None:
	"""Seed the client's Email Template once; an existing one is the operator's to edit."""
	_seed_template(CLIENT_EMAIL_TEMPLATE, _CLIENT_SUBJECT, _CLIENT_CONTENT)


def _seed_template(name: str, subject: str, body: str) -> bool:
	"""Insert the Email Template `name` if it is missing; True when it was inserted."""
	if frappe.db.exists("Email Template", name):
		return False
	frappe.get_doc(
		{"doctype": "Email Template", "name": name, "subject": subject, "use_html": 1, "response_html": body}
	).insert(ignore_permissions=True)
	return True


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


def sanitize_error(error) -> str | None:
	"""A short, secret-free error summary safe to store on the ledger."""
	if not error:
		return None
	text = str(error)
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


def client_name(deal) -> str:
	"""The client's first and last name, else the best name the Deal has."""
	parts = [deal.get("first_name"), deal.get("last_name")]
	name = " ".join(part for part in parts if part)
	return name or deal.get("lead_name") or deal.get("organization") or deal.name


def build_assignment_email_context(
	deal, intended_user: str, assigned_by: str, assigned_at, is_test_redirect: bool
) -> dict:
	"""The documented context of both Email Templates.

	The Opportunity URL is built from this site's own address, so each environment links to
	itself. A blank organization, email, phone or program reads "Not provided" for the coach;
	the client's message uses `client_program`, blank when there is none.
	"""
	# Imported here: coaching_call_recap reaches the pipelines, which import this module.
	from crm.txb.coaching_call_recap import primary_contact_email

	program = (deal.get("custom_program_type") or "").strip()
	return {
		"client_name": client_name(deal),
		"client_first_name": deal.get("first_name") or client_name(deal),
		"client_email": primary_contact_email(deal)[1] or deal.get("email") or NOT_PROVIDED,
		"client_phone": deal.get("mobile_no") or deal.get("phone") or NOT_PROVIDED,
		"client_program": program,
		"organization": (deal.get("organization") or "").strip() or NOT_PROVIDED,
		"program_type": program or NOT_PROVIDED,
		"status": deal.get("status") or "",
		"assigned_by_name": _user_name(assigned_by),
		"assigned_at": format_datetime(assigned_at),
		"opportunity_url": get_url(f"/crm/deals/{deal.name}"),
		"intended_coach_name": _user_name(intended_user),
		"is_test_redirect": bool(is_test_redirect),
	}


def render_assignment_email(context: dict, template_name: str = ASSIGNMENT_EMAIL_TEMPLATE) -> tuple[str, str]:
	"""(subject, html body) of the Email Template `template_name` for `context`."""
	template = frappe.get_doc("Email Template", template_name)
	body = template.response_html if template.use_html else template.response
	return (
		frappe.render_template(template.subject, context),
		frappe.render_template(body, context),
	)

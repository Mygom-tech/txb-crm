"""Public workshop registration (TXB-15).

A Workshop deal issues a tokenised link (see `crm.txb.doc_events.deal.issue_registration_link`;
the deal page's Generate Link button and the "Workshop set" hook both call it). Attendees open
`/register?token=…`, fill in the branded page (`crm/www/register.py`, rendered by the same
template as CRM web forms) and answer one question. `process_registration` is guest-accessible
and WRITES; the token is the only thing gating it.

What a submission creates:

- always: a Contact (reused when the email/phone is already known), stamped with the answer
  and the workshop it registered for; the Organization is upserted from the company name;
- "I am in":                     a Delivering Coaching opportunity ("Submitted") linked to the
                                 workshop via `custom_source_deal`, owned by the workshop's owner;
- "I am in, but have questions": a task for the workshop's owner;
- "Not for me":                  nothing but a note on the workshop deal.

Known weaknesses deliberately carried over from the original Server Scripts, tracked in
specs/server-script-migration.md: no rate limiting; no consent capture or retention rule on
personal data (GDPR); email-format validation only via the browser.
"""

import io
import json

import frappe
from frappe import _
from frappe.utils import get_url

from crm.api.form import _apply_utm_source
from crm.fcrm.doctype.crm_deal.crm_deal import contact_exists
from crm.txb.constants import (
	FIELD_CONTACT_REGISTRATION_STATUS,
	FIELD_CONTACT_SOURCE_WORKSHOP,
	FIELD_REGISTRATION_LINK,
	FIELD_REGISTRATION_TOKEN,
	PIPELINE_DELIVERING_COACHING,
	PIPELINE_WORKSHOP,
	REGISTRATION_DEAL_STATUS,
	WORKSHOP_INTEREST_IN,
	WORKSHOP_INTEREST_NOT_FOR_ME,
	WORKSHOP_INTEREST_OPTIONS,
	WORKSHOP_INTEREST_QUESTIONS,
)
from crm.txb.doc_events.deal import issue_registration_link
from crm.txb.pipelines.common import add_note, add_task, deal_link, lines

CONFIRMATION_TEMPLATE = "Registracijos patvirtinimas"

# What the page collects, in the shape the CRM form template renders (see crm/www/register.py).
FORM_FIELDS = (
	"first_name",
	"last_name",
	"email",
	"phone",
	"company_name",
	"job_title",
	"comments",
	"workshop_interest",
)
REQUIRED_FIELDS = (
	("first_name", "Vardas yra privalomas laukas"),
	("last_name", "Pavardė yra privalomas laukas"),
	("email", "El. paštas yra privalomas laukas"),
	("workshop_interest", "Prašome pasirinkti atsakymą"),
)

QR_SVG_SCALE = 12  # SVG is resolution-independent; scale only sets its nominal size
QR_PNG_MODULE_PX = 40  # ~1500-2000 px per side: crisp on a conference slide


# ---- token / link ----


def _workshop_deal_by_token(token: str | None):
	if not token:
		return None
	name = frappe.db.get_value(
		"CRM Deal", {FIELD_REGISTRATION_TOKEN: token, "pipeline_type": PIPELINE_WORKSHOP}
	)
	return frappe.get_doc("CRM Deal", name) if name else None


@frappe.whitelist(allow_guest=True)
def validate_registration_token(token: str | None = None):
	"""Report whether a registration token is live, without exposing anything else."""
	token = token or frappe.form_dict.get("token")
	if not token:
		frappe.throw(_("Token is required"))
	deal = _workshop_deal_by_token(token)
	if not deal:
		return {"valid": False}
	return {"valid": True, "workshop": deal.get("workshop_name") or ""}


@frappe.whitelist()
def generate_registration_link(deal: str) -> dict:
	"""Issue (or re-read) the public registration link for a Workshop deal, at any status."""
	frappe.has_permission("CRM Deal", "write", deal, throw=True)
	doc = frappe.get_doc("CRM Deal", deal)
	if doc.pipeline_type != PIPELINE_WORKSHOP:
		frappe.throw(_("Registration links are only issued for Workshop opportunities."))
	link = issue_registration_link(doc)
	# two app-managed fields; bypass the save chain (status guards etc.) on purpose
	frappe.db.set_value(
		"CRM Deal",
		doc.name,
		{FIELD_REGISTRATION_TOKEN: doc.get(FIELD_REGISTRATION_TOKEN), FIELD_REGISTRATION_LINK: link},
		update_modified=False,
	)
	return {"link": link}


@frappe.whitelist()
def registration_qr(deal: str, fmt: str = "svg", download: int = 0):
	"""The registration link as a QR code. Inline (default): the SVG markup, returned as
	JSON for the deal page to embed — resolution-independent, so it scales to any slide.
	`download=1`: the file itself, SVG or PNG (~1500+ px) for tools that cannot place SVG.
	Requires read access to the deal."""
	import pyqrcode

	frappe.has_permission("CRM Deal", "read", deal, throw=True)
	link = frappe.db.get_value("CRM Deal", deal, FIELD_REGISTRATION_LINK)
	if not link:
		frappe.throw(_("Generate the registration link first."))
	qr = pyqrcode.create(link, error="H")
	buf = io.BytesIO()
	if fmt == "png":
		_render_png(qr, buf)
	else:
		# omithw: viewBox only, so the SVG fills whatever box it is placed in
		qr.svg(buf, scale=QR_SVG_SCALE, background="white", quiet_zone=4, omithw=True)
	if not int(download or 0):
		return {"svg": buf.getvalue().decode()}
	frappe.response.filename = f"{deal}-registration.{fmt}"
	frappe.response.filecontent = buf.getvalue()
	frappe.response.type = "binary"


def _render_png(qr, buf) -> None:
	"""pyqrcode's own PNG writer needs pypng, which the bench lacks; Pillow is present.
	One pixel per module, then a NEAREST upscale keeps every module a crisp square."""
	from PIL import Image

	quiet = 4
	rows = qr.code
	side = len(rows) + 2 * quiet
	img = Image.new("1", (side, side), 1)
	img.putdata(
		[
			0
			if (quiet <= y < side - quiet and quiet <= x < side - quiet and rows[y - quiet][x - quiet])
			else 1
			for y in range(side)
			for x in range(side)
		]
	)
	img.resize((side * QR_PNG_MODULE_PX,) * 2, Image.NEAREST).save(buf, format="PNG")


# ---- submission ----


@frappe.whitelist(allow_guest=True)
def process_registration(token: str | None = None, data: str | dict | None = None):
	"""Register an attendee against a workshop deal. `data` is the page's JSON payload; the
	legacy page posted the fields flat, which `read_form_data` still accepts."""
	logger = frappe.logger("crm")
	logger.info("[process_registration] Attempting to register a workshop attendee")
	try:
		source_deal = _workshop_deal_by_token(token or frappe.form_dict.get("token"))
		if not source_deal:
			frappe.throw(_("Registracijos nuoroda nerasta arba nebegalioja"), frappe.DoesNotExistError)
		values = read_form_data(data)
		validate_required(values)
		organization = upsert_organization(values)
		contact = upsert_contact(values, organization, source_deal)
		result = {"success": True, "contact": contact.name}

		answer = values["workshop_interest"]
		if answer == WORKSHOP_INTEREST_IN:
			deal = create_registration_deal(values, organization, contact, source_deal)
			add_note(
				source_deal,
				"Workshop registration",
				lines(_person(values), f"Delivery Opportunity: {deal_link(deal.name)}", _comments(values)),
			)
			result["deal"] = deal.name
		elif answer == WORKSHOP_INTEREST_QUESTIONS:
			add_task(
				source_deal,
				"Registration question",
				lines(f"{_person(values)} registered with questions.", _comments(values)),
				assigned_to=source_deal.deal_owner,
				priority="High",
			)
		else:
			add_note(source_deal, "Workshop registration declined", lines(_person(values), _comments(values)))

		send_confirmation(values, source_deal)
		return result
	except Exception as e:
		logger.error(f"[process_registration] Failed to register a workshop attendee. {e}")
		raise


def read_form_data(data=None) -> dict:
	if isinstance(data, str):
		data = json.loads(data or "{}")
	source = data if isinstance(data, dict) else frappe.form_dict
	return {field: str(source.get(field) or "").strip() for field in FORM_FIELDS}


def validate_required(values: dict):
	for field, message in REQUIRED_FIELDS:
		if not values[field]:
			frappe.throw(_(message))
	if values["workshop_interest"] not in WORKSHOP_INTEREST_OPTIONS:
		frappe.throw(_("Prašome pasirinkti atsakymą"))


def _person(values: dict) -> str:
	bits = [
		f"{values['first_name']} {values['last_name']}",
		values["email"],
		values["phone"],
		values["company_name"],
		values["job_title"],
	]
	return ", ".join(b for b in bits if b)


def _comments(values: dict) -> str:
	return f"Comments: {values['comments']}" if values["comments"] else ""


def upsert_organization(values: dict) -> str | None:
	"""Reuse an organization by name; else create it."""
	company_name = values["company_name"]
	if not company_name:
		return None
	existing = frappe.db.get_value("CRM Organization", {"organization_name": company_name})
	if existing:
		return existing
	organization = frappe.get_doc({"doctype": "CRM Organization", "organization_name": company_name})
	organization.insert(ignore_permissions=True)
	return organization.name


def upsert_contact(values: dict, organization: str | None, source_deal):
	"""Reuse the Contact whose email/phone is already known (the CRM's own rule), else create
	one; either way record the answer and the workshop on it."""
	existing = contact_exists(frappe._dict(email=values["email"], mobile_no=values["phone"]))
	if existing:
		contact = frappe.get_doc("Contact", existing)
	else:
		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": values["first_name"],
				"last_name": values["last_name"],
				"designation": values["job_title"],
				"custom_organization_link": organization,
				"company_name": values["company_name"],
			}
		)
		if values["email"]:
			contact.append("email_ids", {"email_id": values["email"], "is_primary": 1})
		if values["phone"]:
			contact.append("phone_nos", {"phone": values["phone"], "is_primary_mobile_no": 1})
	contact.set(FIELD_CONTACT_REGISTRATION_STATUS, values["workshop_interest"])
	contact.set(FIELD_CONTACT_SOURCE_WORKSHOP, source_deal.name)
	contact.save(ignore_permissions=True) if existing else contact.insert(ignore_permissions=True)
	return contact


def create_registration_deal(values: dict, organization: str | None, contact, source_deal):
	deal = frappe.get_doc(
		{
			"doctype": "CRM Deal",
			"first_name": values["first_name"],
			"last_name": values["last_name"],
			"organization": organization,
			"pipeline_type": PIPELINE_DELIVERING_COACHING,
			"status": REGISTRATION_DEAL_STATUS,
			"custom_delivery_status": REGISTRATION_DEAL_STATUS,
			"custom_source_deal": source_deal.name,
			"source": PIPELINE_WORKSHOP,
			FIELD_REGISTRATION_LINK: source_deal.get(FIELD_REGISTRATION_LINK),
			"deal_owner": source_deal.deal_owner,
		}
	)
	deal.append("contacts", {"contact": contact.name, "is_primary": 1})
	_apply_utm_source(deal)  # ?utm_source= on the page URL, forwarded by the template
	deal.insert(ignore_permissions=True)
	if values["comments"]:
		add_note(deal, "Registration comments", values["comments"])
	return deal


def confirmation_context(values: dict, source_deal) -> dict:
	"""The render context for the confirmation email: the submitted form fields plus the
	authoritative Program Type from the source Workshop deal. The registration form never
	collects `program_type`, so without this the database template would emit the literal
	`{{ program_type }}`; a missing value is normalised to an empty string so the template's
	`{% if program_type %}` row drops out cleanly."""
	return {**values, "program_type": (source_deal.get("custom_program_type") or "").strip()}


def send_confirmation(values: dict, source_deal):
	"""Best-effort: a mail failure must not undo a completed registration."""
	try:
		template = frappe.get_doc("Email Template", CONFIRMATION_TEMPLATE)
		context = confirmation_context(values, source_deal)
		frappe.sendmail(
			recipients=[values["email"]],
			subject=frappe.render_template(template.subject, context),
			message=frappe.render_template(template.response_html, context),
			now=True,
		)
	except Exception as e:
		frappe.log_error(
			f"[send_confirmation] Failed to send registration confirmation. {e}",
			"Registration Confirmation Email Error",
		)


def registration_url_for(deal_name: str) -> str:
	"""Convenience for the page/tests: the link the deal currently carries."""
	return frappe.db.get_value("CRM Deal", deal_name, FIELD_REGISTRATION_LINK) or get_url()

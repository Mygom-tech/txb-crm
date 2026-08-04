"""Public workshop registration endpoints.

Ported from the `Process Registration` and `Validate Registration Token` Server Scripts.

These are guest-accessible and they WRITE: `process_registration` creates a Contact, an
Organization and a Deal, then emails the submitter. The registration token is the only
thing gating them.

Behaviour is preserved from the original scripts. Known weaknesses deliberately carried
over, each tracked as a follow-up in specs/server-script-migration.md:

- no rate limiting, so the endpoints can be hammered;
- no dedup, so every submission inserts a new Contact even for a repeat registrant;
- no email-format validation beyond "not empty";
- no consent capture or retention rule on personal data (GDPR).

The predictable-token weakness these endpoints depended on was fixed separately; tokens
now come from `secrets` (see crm.txb.doc_events.deal.generate_registration_token).
"""

import frappe
from frappe import _

from crm.txb.constants import (
	FIELD_REGISTRATION_LINK,
	FIELD_REGISTRATION_TOKEN,
	PIPELINE_DELIVERING_COACHING,
	PIPELINE_WORKSHOP,
)

CONFIRMATION_TEMPLATE = "Registracijos patvirtinimas"
REGISTRATION_DEAL_STATUS = "Waiting on Review"

# Plain text fields copied straight from the submitted form.
FORM_FIELDS = (
	"first_name",
	"last_name",
	"email",
	"birthdate",
	"phone",
	"address",
	"company_name",
	"company_code",
	"vat_code",
	"job_title",
	"delivery_address",
	"program_type",
)

REQUIRED_FIELDS = (
	("first_name", "Vardas yra privalomas laukas"),
	("last_name", "Pavardė yra privalomas laukas"),
	("email", "El. paštas yra privalomas laukas"),
	("program_type", "Prašome pasirinkti programą"),
)


@frappe.whitelist(allow_guest=True)
def validate_registration_token(token: str | None = None):
	"""Report whether a registration token is live, without exposing anything else."""
	token = token or frappe.form_dict.get("token")
	if not token:
		frappe.throw(_("Token is required"))

	deals = frappe.get_all(
		"CRM Deal",
		filters={FIELD_REGISTRATION_TOKEN: token, "pipeline_type": PIPELINE_WORKSHOP},
		fields=["name", "workshop_name"],
		limit=1,
	)

	if not deals:
		return {"valid": False}

	return {"valid": True, "workshop": deals[0].get("workshop_name") or ""}


@frappe.whitelist(allow_guest=True)
def process_registration():
	"""Register an attendee against a workshop deal."""
	source_deal = resolve_source_deal(frappe.form_dict.get("token"))
	data = read_form_data()
	validate_required(data)

	organization = upsert_organization(data)
	contact = create_contact(data, organization)
	deal = create_registration_deal(data, organization, contact, source_deal)

	send_confirmation(data)

	return {"success": True, "deal": deal.name, "contact": contact.name}


def resolve_source_deal(token: str | None):
	if not token:
		frappe.throw(_("Registracijos nuoroda neteisinga"), frappe.ValidationError)

	deals = frappe.get_all(
		"CRM Deal",
		filters={FIELD_REGISTRATION_TOKEN: token, "pipeline_type": PIPELINE_WORKSHOP},
		limit=1,
	)
	if not deals:
		frappe.throw(_("Registracijos nuoroda nerasta arba nebegalioja"), frappe.DoesNotExistError)

	return frappe.get_doc("CRM Deal", deals[0].name)


def read_form_data() -> dict:
	return {field: (frappe.form_dict.get(field) or "").strip() for field in FORM_FIELDS}


def validate_required(data: dict):
	for field, message in REQUIRED_FIELDS:
		if not data[field]:
			frappe.throw(_(message))


def upsert_organization(data: dict) -> str | None:
	"""Reuse an organization by name, filling in blank codes; else create it."""
	company_name = data["company_name"]
	if not company_name:
		return None

	existing = frappe.get_all(
		"CRM Organization", filters={"organization_name": company_name}, limit=1
	)

	if existing:
		organization = frappe.get_doc("CRM Organization", existing[0].name)
		if data["company_code"] and not organization.custom_company_code:
			organization.custom_company_code = data["company_code"]
		if data["vat_code"] and not organization.custom_vat_code:
			organization.custom_vat_code = data["vat_code"]
		organization.save(ignore_permissions=True)
		return organization.name

	organization = frappe.get_doc(
		{
			"doctype": "CRM Organization",
			"organization_name": company_name,
			"custom_company_code": data["company_code"],
			"custom_vat_code": data["vat_code"],
		}
	)
	organization.insert(ignore_permissions=True)
	return organization.name


def create_contact(data: dict, organization: str | None):
	contact = frappe.get_doc(
		{
			"doctype": "Contact",
			"first_name": data["first_name"],
			"last_name": data["last_name"],
			"custom_birthdate": data["birthdate"] or None,
			"custom_address": data["address"],
			"designation": data["job_title"],
			"custom_delivery_address": data["delivery_address"],
			"custom_organization_link": organization,
			"company_name": data["company_name"],
		}
	)

	if data["email"]:
		contact.append("email_ids", {"email_id": data["email"], "is_primary": 1})

	if data["phone"]:
		contact.append("phone_nos", {"phone": data["phone"], "is_primary_phone": 1})

	contact.insert(ignore_permissions=True)
	return contact


def create_registration_deal(data: dict, organization: str | None, contact, source_deal):
	deal = frappe.get_doc(
		{
			"doctype": "CRM Deal",
			"first_name": data["first_name"],
			"last_name": data["last_name"],
			"organization": organization,
			"pipeline_type": PIPELINE_DELIVERING_COACHING,
			"status": REGISTRATION_DEAL_STATUS,
			"custom_delivery_status": REGISTRATION_DEAL_STATUS,
			"custom_source_deal": source_deal.name,
			"custom_program_type": data["program_type"],
			FIELD_REGISTRATION_LINK: source_deal.get(FIELD_REGISTRATION_LINK),
			"deal_owner": source_deal.deal_owner,
		}
	)
	deal.append("contacts", {"contact": contact.name, "is_primary": 1})
	deal.insert(ignore_permissions=True)
	return deal


def send_confirmation(data: dict):
	"""Best-effort: a mail failure must not undo a completed registration."""
	try:
		template = frappe.get_doc("Email Template", CONFIRMATION_TEMPLATE)
		frappe.sendmail(
			recipients=[data["email"]],
			subject=frappe.render_template(template.subject, data),
			message=frappe.render_template(template.response_html, data),
			now=True,
		)
	except Exception as e:
		frappe.log_error(
			f"[send_confirmation] Failed to send registration confirmation. {e}",
			"Registration Confirmation Email Error",
		)

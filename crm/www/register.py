# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Public workshop registration page: /register?token=<workshop token>.

Renders the same branded template as CRM web forms (crm_form.html) with a fixed field set
and posts to `crm.txb.api.registration.process_registration`, which is what decides what the
CRM creates from the answer. An unknown or missing token is a 404.
"""

import frappe
from frappe import _

from crm.txb.api.registration import FORM_FIELDS, _workshop_deal_by_token
from crm.txb.constants import WORKSHOP_INTEREST_OPTIONS
from crm.www.crm_form import build_layout

no_cache = 1

LABELS = {
	"first_name": "Vardas",
	"last_name": "Pavardė",
	"email": "El. paštas",
	"phone": "Telefonas",
	"company_name": "Įmonė",
	"job_title": "Pareigos",
	"comments": "Komentarai",
	"workshop_interest": "Ar dalyvausite?",
}
REQUIRED = {"first_name", "last_name", "email", "workshop_interest"}
FIELDTYPES = {
	"email": ("Data", "Email"),
	"phone": ("Data", "Phone"),
	"comments": ("Small Text", ""),
	"workshop_interest": ("Select", "\n".join(WORKSHOP_INTEREST_OPTIONS)),
}
# one section, two columns
LAYOUT = (
	("first_name", "email", "company_name", "workshop_interest"),
	("last_name", "phone", "job_title", "comments"),
)


def _field(fieldname: str) -> dict:
	fieldtype, options = FIELDTYPES.get(fieldname, ("Data", ""))
	return {
		"fieldname": fieldname,
		"label": LABELS[fieldname],
		"fieldtype": fieldtype,
		"options": options,
		"reqd": int(fieldname in REQUIRED),
		"placeholder": "",
		"description": "",
	}


def _break(fieldtype: str, i: int) -> dict:
	return {
		"fieldname": f"{fieldtype.split()[0].lower()}_break_{i}",
		"label": "",
		"fieldtype": fieldtype,
		"options": "",
		"reqd": 0,
		"placeholder": "",
		"description": "",
	}


def registration_fields() -> list[dict]:
	assert set(FORM_FIELDS) == {f for col in LAYOUT for f in col}, (
		"page layout must collect exactly FORM_FIELDS"
	)
	rows = [_break("Section Break", 0)]
	for i, column in enumerate(LAYOUT):
		if i:
			rows.append(_break("Column Break", i))
		rows += [_field(f) for f in column]
	return rows


def get_context(context):
	token = frappe.form_dict.get("token")
	deal = _workshop_deal_by_token(token)
	if not deal:
		raise frappe.DoesNotExistError
	context.no_cache = 1
	try:
		context.csrf_token = frappe.sessions.get_csrf_token()
	except Exception:
		context.csrf_token = ""
	context.web_form_name = ""
	context.embed = frappe.form_dict.get("embed") in ("1", "true", "yes")
	context.draft_preview = False
	context.form_title = deal.get("workshop_name") or _("Workshop Registration")
	context.form_description = _("Užpildykite formą ir pasirinkite, ar dalyvausite.")
	context.form_route = "register"
	context.submit_label = _("Registruotis")
	context.success_message = _("Ačiū! Jūsų registracija gauta.")
	context.success_url = ""
	context.submit_endpoint = "/api/method/crm.txb.api.registration.process_registration"
	context.submit_extra = {"token": token}
	context.values_key = "data"
	context.fields = registration_fields()
	context.layout = build_layout(context.fields)
	return context

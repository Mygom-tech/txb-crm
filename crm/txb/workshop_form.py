"""TXB-15: seed the public Workshop Registration form (CRM Lead capture).

Idempotent. Called from ``crm.install.after_install`` for new sites and backfilled onto deployed
sites by ``crm.patches.v1_0.seed_workshop_registration_form``.

What gets seeded:

- the ``Workshop`` CRM Lead Source (present on the live sites by hand, never by code until now);
- a native Web Form (``/crm-form/workshop-registration``) mapped to CRM Lead, saved through
  ``crm.api.form.save_form`` so it is exactly what the Settings → Forms builder would create.
  Its hidden defaults carry ``source = Workshop``, which ``enrich_form_submission`` applies on
  submit (a ``?utm_source=`` naming a real source overrides it). Ships **unpublished**: the
  client still has to confirm the field list (ticket AC), and Publish is one toggle;
- a Frappe Notification emailing the registrant on Lead creation. It is inert until an outgoing
  Email Account exists (TXB-6); ``Notification.send`` logs the failure and never blocks the insert.
"""

import frappe
from frappe import _

from crm.api import form as form_api
from crm.txb.constants import PIPELINE_WORKSHOP

FORM_ROUTE = "workshop-registration"
FORM_TITLE = "Workshop Registration"
FORM_DOCTYPE = "CRM Lead"
FORM_DESCRIPTION = "Užpildykite formą ir mes su jumis susisieksime dėl artimiausio workshop'o."
FORM_SUCCESS_MESSAGE = "Ačiū! Jūsų registracija gauta, su jumis susisieksime artimiausiu metu."
FORM_SUBMIT_LABEL = "Registruotis"

# Visible layout: one section, two columns. `website` is the ticket's "domain". `organization`
# is omitted: on the live sites it is a Link → CRM Organization (Property Setter), which public
# forms cannot collect — the builder cannot map it either.
FORM_LAYOUT = [
	{
		"label": "Jūsų duomenys",
		"columns": [["first_name", "email", "phone"], ["last_name", "website"]],
	},
]
FORM_REQUIRED = ("first_name", "email")

NOTIFICATION_NAME = "Workshop Registration Confirmation"
NOTIFICATION_SUBJECT = "Registracija į workshop'ą gauta"
NOTIFICATION_MESSAGE = (
	"<p>Sveiki, {{ doc.first_name }},</p>"
	"<p>Gavome jūsų registraciją į workshop'ą. Su jumis susisieksime artimiausiu metu.</p>"
	"<p>TXB komanda</p>"
)


def seed_workshop_form() -> None:
	logger = frappe.logger("crm")
	logger.info(f"[seed_workshop_form] Attempting to seed the {FORM_TITLE} form")
	try:
		form_api.ensure_lead_source(PIPELINE_WORKSHOP)
		_ensure_form()
		_ensure_notification()
	except Exception as e:
		logger.error(f"[seed_workshop_form] Failed to seed the {FORM_TITLE} form. {e}")
		raise


def _ensure_form() -> None:
	if frappe.db.exists("Web Form", {"route": FORM_ROUTE, "module": form_api.FORM_MODULE}):
		return
	form_api.save_form(
		name=None,
		form={
			"title": FORM_TITLE,
			"route": FORM_ROUTE,
			"document_type": FORM_DOCTYPE,
			"description": FORM_DESCRIPTION,
			"success_message": FORM_SUCCESS_MESSAGE,
			"submit_button_label": FORM_SUBMIT_LABEL,
			"published": 0,
			"fields": form_api._seed_visible_fields(FORM_DOCTYPE, FORM_LAYOUT, FORM_REQUIRED),
			"hidden_fields": _hidden_fields(),
		},
	)


def _hidden_fields() -> list[dict]:
	"""The builder's own hidden seed (Status) plus Source = Workshop."""
	hidden = form_api._seed_hidden_fields(FORM_DOCTYPE)
	hidden.append(
		{
			"fieldname": "source",
			"label": _("Source"),
			"fieldtype": "Link",
			"options": "CRM Lead Source",
			"default": PIPELINE_WORKSHOP,
		}
	)
	return hidden


def _ensure_notification() -> None:
	if frappe.db.exists("Notification", NOTIFICATION_NAME):
		return
	frappe.get_doc(
		{
			"doctype": "Notification",
			"__newname": NOTIFICATION_NAME,
			"enabled": 1,
			"channel": "Email",
			"document_type": FORM_DOCTYPE,
			"event": "New",
			# public submissions run as Guest; a Workshop lead a rep creates by hand must not
			# get a "we received your registration" email
			"condition": f'doc.source == "{PIPELINE_WORKSHOP}" and doc.owner == "Guest"',
			"subject": NOTIFICATION_SUBJECT,
			"message": NOTIFICATION_MESSAGE,
			"recipients": [{"receiver_by_document_field": "email"}],
		}
	).insert(ignore_permissions=True)

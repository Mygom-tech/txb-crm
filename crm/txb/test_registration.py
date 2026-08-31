import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_url

from crm.txb import registration_setup
from crm.txb.api import registration as R
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
	WORKSHOP_INTEREST_QUESTIONS,
)

OWNER = "Administrator"


def workshop_deal(**kw):
	doc = frappe.get_doc(
		{
			"doctype": "CRM Deal",
			"pipeline_type": PIPELINE_WORKSHOP,
			"status": "Workshop submitted",
			"workshop_name": "Sales Workshop",
			"deal_owner": OWNER,
			**kw,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def values(answer, email="reg-e2e@test.invalid", **extra):
	return {
		"first_name": "Ona",
		"last_name": "Testė",
		"email": email,
		"phone": "+37060000001",
		"company_name": "Imone UAB",
		"job_title": "CTO",
		"comments": "Kada?",
		"workshop_interest": answer,
		**extra,
	}


class TestWorkshopRegistration(FrappeTestCase):
	def setUp(self):
		registration_setup.ensure_registration_setup()
		self.deal = workshop_deal()
		self.link = R.generate_registration_link(self.deal.name)["link"]
		self.token = frappe.db.get_value("CRM Deal", self.deal.name, FIELD_REGISTRATION_TOKEN)

	def tearDown(self):
		frappe.form_dict.pop("utm_source", None)
		frappe.db.rollback()

	# ---- link / QR ----

	def test_generate_link_mints_token_once_and_builds_site_url(self):
		self.assertEqual(self.link, f"{get_url()}/register?token={self.token}")
		self.assertEqual(frappe.db.get_value("CRM Deal", self.deal.name, FIELD_REGISTRATION_LINK), self.link)
		again = R.generate_registration_link(self.deal.name)["link"]
		self.assertEqual(again, self.link)  # regenerating never invalidates a link already printed

	def test_generate_link_refuses_other_pipelines(self):
		other = workshop_deal(pipeline_type="Individual Session", status="Submitted")
		with self.assertRaises(frappe.ValidationError):
			R.generate_registration_link(other.name)

	def test_qr_inline_svg_and_downloadable_png(self):
		import io

		from PIL import Image

		svg = R.registration_qr(self.deal.name)["svg"]
		self.assertIn("<svg", svg)
		self.assertIn("viewBox", svg)
		R.registration_qr(self.deal.name, fmt="png", download=1)
		png = frappe.response.filecontent
		self.assertTrue(png.startswith(b"\x89PNG"))
		self.assertGreaterEqual(Image.open(io.BytesIO(png)).size[0], 1500)
		self.assertEqual(frappe.response.filename, f"{self.deal.name}-registration.png")

	def test_validate_token(self):
		self.assertEqual(
			R.validate_registration_token(self.token), {"valid": True, "workshop": "Sales Workshop"}
		)
		self.assertEqual(R.validate_registration_token("nope"), {"valid": False})

	# ---- submission ----

	def test_i_am_in_creates_contact_and_delivering_coaching_deal_linked_to_workshop(self):
		result = R.process_registration(self.token, frappe.as_json(values(WORKSHOP_INTEREST_IN)))
		contact = frappe.get_doc("Contact", result["contact"])
		self.assertEqual(contact.get(FIELD_CONTACT_REGISTRATION_STATUS), WORKSHOP_INTEREST_IN)
		self.assertEqual(contact.get(FIELD_CONTACT_SOURCE_WORKSHOP), self.deal.name)
		self.assertIn("reg-e2e@test.invalid", [e.email_id for e in contact.email_ids])
		deal = frappe.get_doc("CRM Deal", result["deal"])
		self.assertEqual(deal.pipeline_type, PIPELINE_DELIVERING_COACHING)
		self.assertEqual(deal.status, REGISTRATION_DEAL_STATUS)
		self.assertEqual(deal.custom_source_deal, self.deal.name)
		self.assertEqual(deal.deal_owner, OWNER)
		self.assertEqual(deal.source, PIPELINE_WORKSHOP)
		self.assertEqual(
			frappe.db.get_value("CRM Organization", deal.organization, "organization_name"), "Imone UAB"
		)
		self.assertTrue(next(c for c in deal.contacts if c.is_primary).contact == contact.name)
		self.assertTrue(
			frappe.db.exists(
				"FCRM Note", {"reference_docname": deal.name, "title": ["like", "Registration comments%"]}
			)
		)
		self.assertTrue(
			frappe.db.exists(
				"FCRM Note",
				{"reference_docname": self.deal.name, "title": ["like", "Workshop registration%"]},
			)
		)

	def test_questions_creates_task_for_workshop_owner_and_no_deal(self):
		before = frappe.db.count("CRM Deal")
		result = R.process_registration(
			self.token, frappe.as_json(values(WORKSHOP_INTEREST_QUESTIONS, email="reg-q@test.invalid"))
		)
		self.assertNotIn("deal", result)
		self.assertEqual(frappe.db.count("CRM Deal"), before)
		task = frappe.get_doc(
			"CRM Task", {"reference_docname": self.deal.name, "title": ["like", "Registration question%"]}
		)
		self.assertEqual(task.assigned_to, OWNER)
		self.assertIn("Kada?", task.description)
		self.assertEqual(
			frappe.db.get_value("Contact", result["contact"], FIELD_CONTACT_REGISTRATION_STATUS),
			WORKSHOP_INTEREST_QUESTIONS,
		)

	def test_not_for_me_records_decline_only(self):
		before = frappe.db.count("CRM Deal")
		result = R.process_registration(
			self.token, frappe.as_json(values(WORKSHOP_INTEREST_NOT_FOR_ME, email="reg-no@test.invalid"))
		)
		self.assertEqual(frappe.db.count("CRM Deal"), before)
		self.assertFalse(frappe.db.exists("CRM Task", {"reference_docname": self.deal.name}))
		self.assertTrue(
			frappe.db.exists(
				"FCRM Note",
				{"reference_docname": self.deal.name, "title": ["like", "Workshop registration declined%"]},
			)
		)
		self.assertEqual(
			frappe.db.get_value("Contact", result["contact"], FIELD_CONTACT_REGISTRATION_STATUS),
			WORKSHOP_INTEREST_NOT_FOR_ME,
		)

	def test_repeat_registrant_reuses_contact(self):
		first = R.process_registration(
			self.token, frappe.as_json(values(WORKSHOP_INTEREST_QUESTIONS, email="reg-dup@test.invalid"))
		)
		second = R.process_registration(
			self.token, frappe.as_json(values(WORKSHOP_INTEREST_IN, email="reg-dup@test.invalid"))
		)
		self.assertEqual(first["contact"], second["contact"])
		self.assertEqual(
			frappe.db.get_value("Contact", first["contact"], FIELD_CONTACT_REGISTRATION_STATUS),
			WORKSHOP_INTEREST_IN,
		)

	def test_invalid_token_and_missing_answer_are_refused(self):
		with self.assertRaises(frappe.DoesNotExistError):
			R.process_registration("nope", frappe.as_json(values(WORKSHOP_INTEREST_IN)))
		with self.assertRaises(frappe.ValidationError):
			R.process_registration(self.token, frappe.as_json(values("", email="reg-x@test.invalid")))

	def test_utm_source_attributes_the_created_deal(self):
		if not frappe.db.exists("CRM Lead Source", "Social"):
			frappe.get_doc({"doctype": "CRM Lead Source", "source_name": "Social"}).insert()
		frappe.form_dict["utm_source"] = "social"
		result = R.process_registration(
			self.token, frappe.as_json(values(WORKSHOP_INTEREST_IN, email="reg-utm@test.invalid"))
		)
		self.assertEqual(frappe.db.get_value("CRM Deal", result["deal"], "source"), "Social")

	# ---- confirmation email ----

	def _render_confirmation(self, source_deal):
		template = frappe.get_doc("Email Template", R.CONFIRMATION_TEMPLATE)
		ctx = R.confirmation_context(values(WORKSHOP_INTEREST_IN), source_deal)
		return (
			frappe.render_template(template.subject, ctx),
			frappe.render_template(template.response_html, ctx),
		)

	def test_confirmation_email_renders_program_type_when_present(self):
		self.deal.db_set("custom_program_type", "TxB Executive")
		subject, body = self._render_confirmation(self.deal)
		self.assertIn("Programa: TxB Executive", body)
		self.assertNotIn("{{", subject + body)

	def test_confirmation_email_omits_program_row_when_absent(self):
		# self.deal carries no custom_program_type; the whole Programa row must drop out.
		subject, body = self._render_confirmation(self.deal)
		self.assertNotIn("Programa", body)
		self.assertNotIn("program_type", subject + body)
		self.assertNotIn("{{", subject + body)

	def test_setup_makes_existing_raw_program_row_conditional_and_is_idempotent(self):
		raw = "<p>Sveiki</p><table><tr><td>Programa: {{ program_type }}</td></tr></table>"
		frappe.db.set_value("Email Template", R.CONFIRMATION_TEMPLATE, "response_html", raw)
		registration_setup.ensure_confirmation_email_template()
		html = frappe.db.get_value("Email Template", R.CONFIRMATION_TEMPLATE, "response_html")
		self.assertIn("{% if program_type %}", html)
		self.assertIn("<p>Sveiki</p>", html)  # the rest of the body is preserved
		registration_setup.ensure_confirmation_email_template()  # second run changes nothing
		self.assertEqual(
			frappe.db.get_value("Email Template", R.CONFIRMATION_TEMPLATE, "response_html"), html
		)
		template = frappe.get_doc("Email Template", R.CONFIRMATION_TEMPLATE)
		self.assertIn(
			"Programa: TxB Advanced",
			frappe.render_template(template.response_html, {"program_type": "TxB Advanced"}),
		)
		self.assertNotIn(
			"Programa", frappe.render_template(template.response_html, {"program_type": ""})
		)

	# ---- public page ----

	def test_register_page_renders_for_a_live_token_and_404s_otherwise(self):
		from crm.www.register import get_context

		frappe.form_dict["token"] = self.token
		ctx = get_context(frappe._dict())
		self.assertEqual(ctx.form_title, "Sales Workshop")
		self.assertEqual(ctx.submit_endpoint, "/api/method/crm.txb.api.registration.process_registration")
		self.assertEqual(ctx.submit_extra, {"token": self.token})
		names = [
			f["fieldname"] for f in ctx.fields if f["fieldtype"] not in ("Section Break", "Column Break")
		]
		self.assertEqual(set(names), set(R.FORM_FIELDS))
		self.assertTrue(
			any(f["fieldtype"] == "Select" and WORKSHOP_INTEREST_IN in f["options"] for f in ctx.fields)
		)
		self.assertEqual(len(ctx.layout), 1)  # one section, two columns
		self.assertEqual(len(ctx.layout[0]["columns"]), 2)

		frappe.form_dict["token"] = "nope"
		with self.assertRaises(frappe.DoesNotExistError):
			get_context(frappe._dict())
		frappe.form_dict.pop("token", None)

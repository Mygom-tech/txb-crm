import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.website.doctype.web_form.web_form import accept
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from crm.install import add_web_form_custom_fields
from crm.txb import workshop_form as W
from crm.txb.constants import PIPELINE_WORKSHOP


class TestWorkshopForm(FrappeTestCase):
	def setUp(self):
		self._saved_request = getattr(frappe.local, "request", None)
		W.seed_workshop_form()

	def tearDown(self):
		frappe.flags.in_web_form = False
		frappe.form_dict.pop("web_form", None)
		frappe.form_dict.pop("utm_source", None)
		frappe.local.request = self._saved_request
		frappe.db.rollback()

	def _form_name(self):
		return frappe.db.get_value("Web Form", {"route": W.FORM_ROUTE, "module": "FCRM"})

	def test_seed_is_idempotent(self):
		W.seed_workshop_form()
		self.assertEqual(frappe.db.count("Web Form", {"route": W.FORM_ROUTE}), 1)
		self.assertEqual(frappe.db.count("Notification", {"name": W.NOTIFICATION_NAME}), 1)
		self.assertTrue(frappe.db.exists("CRM Lead Source", PIPELINE_WORKSHOP))

	def test_form_is_a_draft_lead_form_with_workshop_source_default(self):
		doc = frappe.get_doc("Web Form", self._form_name())
		self.assertEqual(doc.doc_type, "CRM Lead")
		self.assertEqual(doc.crm_published, 0)
		visible = [
			f.fieldname for f in doc.web_form_fields if f.fieldtype not in ("Section Break", "Column Break")
		]
		self.assertEqual(set(visible), {"first_name", "last_name", "email", "phone", "website"})
		hidden = {h["fieldname"]: h["default"] for h in frappe.parse_json(doc.crm_hidden_defaults)}
		self.assertEqual(hidden.get("source"), PIPELINE_WORKSHOP)
		self.assertTrue(hidden.get("status"))

	def test_submission_lands_as_workshop_lead(self):
		frappe.flags.in_web_form = True
		frappe.form_dict["web_form"] = self._form_name()
		lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Ona",
				"last_name": "Test",
				"email": "ws-test-ona@test.invalid",
			}
		).insert(ignore_permissions=True)
		self.assertEqual(lead.source, PIPELINE_WORKSHOP)
		self.assertTrue(lead.status)

	def _submit(self, utm_source=None, email="ws-e2e@test.invalid"):
		"""Drive the real public path: the form published, Frappe's own accept() doing
		the insert (which sets in_web_form), utm_source posted next to the payload."""
		name = self._form_name()
		frappe.db.set_value("Web Form", name, {"crm_published": 1, "published": 1})
		frappe.form_dict["web_form"] = name  # the page posts it; the hook reads it from form_dict
		if utm_source is not None:
			frappe.form_dict["utm_source"] = utm_source
		accept(
			name,
			frappe.as_json({"first_name": "E2E", "last_name": "Test", "email": email}),
		)
		return frappe.get_doc("CRM Lead", {"email": email})

	def test_accept_without_utm_lands_as_workshop(self):
		self.assertEqual(self._submit().source, PIPELINE_WORKSHOP)

	def test_accept_with_utm_source_sets_matching_source(self):
		for raw, expected in (
			("social", "Social"),
			("social-media", "Social Media"),
			("LINKEDIN", "LinkedIn"),
		):
			for src in ("Social", "Social Media", "LinkedIn"):
				if not frappe.db.exists("CRM Lead Source", src):
					frappe.get_doc({"doctype": "CRM Lead Source", "source_name": src}).insert()
			lead = self._submit(utm_source=raw, email=f"ws-{raw.lower()}@test.invalid")
			self.assertEqual(lead.source, expected, raw)

	def test_utm_source_falls_back_to_referer(self):
		"""Frappe's generic /<route>/new page posts no utm_source; the submitting page's
		query string (Referer) is the fallback. Exercised via the before_insert hook —
		accept() itself is rate-limited once a request object exists."""
		frappe.local.request = Request(
			EnvironBuilder(
				path="/api/method/frappe.website.doctype.web_form.web_form.accept",
				method="POST",
				headers={"Referer": "https://crm.example.com/workshop-registration/new?utm_source=social"},
			).get_environ()
		)
		frappe.flags.in_web_form = True
		frappe.form_dict["web_form"] = self._form_name()
		lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Ref",
				"last_name": "Test",
				"email": "ws-referer@test.invalid",
			}
		).insert(ignore_permissions=True)
		self.assertEqual(lead.source, "Social")

	def test_placeholder_round_trips_and_page_context_renders(self):
		"""Web Form Field has no native `placeholder`; the CRM adds it as a Custom Field on
		every migrate. Without it the public page 500s (AttributeError) and the builder's
		placeholder is silently dropped on save."""
		from crm.api.form import get_form_config, save_form
		from crm.www.crm_form import get_context

		add_web_form_custom_fields()
		name = self._form_name()
		config = get_form_config(name)
		for f in config["fields"]:
			if f["fieldname"] == "email":
				f["placeholder"] = "vardas@imone.lt"
		save_form(name, {**config, "fields": config["fields"]})
		self.assertEqual(
			next(f["placeholder"] for f in get_form_config(name)["fields"] if f["fieldname"] == "email"),
			"vardas@imone.lt",
		)

		frappe.form_dict["route"] = W.FORM_ROUTE
		context = get_context(frappe._dict())
		self.assertEqual(context.web_form_name, name)
		self.assertTrue(any(f["placeholder"] == "vardas@imone.lt" for f in context.fields))

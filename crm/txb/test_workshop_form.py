import frappe
from frappe.tests.utils import FrappeTestCase

from crm.txb import workshop_form as W
from crm.txb.constants import PIPELINE_WORKSHOP


class TestWorkshopForm(FrappeTestCase):
	def setUp(self):
		W.seed_workshop_form()

	def tearDown(self):
		frappe.flags.in_web_form = False
		frappe.form_dict.pop("web_form", None)
		frappe.form_dict.pop("utm_source", None)
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

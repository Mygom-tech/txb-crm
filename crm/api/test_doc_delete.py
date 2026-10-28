import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime

from crm.api.doc import delete_deal, get_linked_docs_of_document, remove_linked_doc_reference
from crm.fcrm.doctype.crm_lead.crm_lead import CONTACT_ORGANIZATION_LINK_FIELD
from crm.fcrm.doctype.crm_lead.test_crm_lead import (
	create_contact_organization_link_field,
	create_lead,
	ensure_conversion_result_fields,
	ensure_deal_statuses,
)
from crm.txb.constants import FIELD_CONVERTED_CONTACT, FIELD_CONVERTED_DEAL


class TestDeleteWithLinks(FrappeTestCase):
	"""A Deal deletes with everything filed on it; Unlink clears whatever field points at the
	record being deleted, so the record then deletes."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_conversion_result_fields()
		ensure_deal_statuses()
		create_contact_organization_link_field()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _converted(self, first_name, organization):
		lead = create_lead(first_name=first_name, email=f"{first_name.lower()}@ex.com", organization=organization)
		deal = lead.convert_to_deal()
		contact = frappe.db.get_value("CRM Lead", lead.name, FIELD_CONVERTED_CONTACT)
		return lead.name, deal, contact

	def _on(self, doctype, reference_doctype, reference_docname, **values):
		doc = {"doctype": doctype, "reference_doctype": reference_doctype, "reference_docname": reference_docname}
		if doctype == "FCRM Note":
			doc.update(title="n", content="x")
		elif doctype == "CRM Call Log":
			doc.update(id=frappe.generate_hash(length=10), type="Outgoing", status="Completed", to="+1", **{"from": "+2"})
		elif doctype == "CRM Task":
			doc.update(title="t", status="Todo")
		elif doctype == "Event":
			doc.update(subject="e", event_type="Private", starts_on=add_days(now_datetime(), 1))
		doc.update(values)
		return frappe.get_doc(doc).insert(ignore_permissions=True).name

	def test_delete_deal_takes_what_is_filed_on_it(self):
		lead, deal, contact = self._converted("Dora", "Dora Co")
		owned = {dt: self._on(dt, "CRM Deal", deal) for dt in ("FCRM Note", "CRM Call Log", "CRM Task", "Event")}
		contact_note = self._on("FCRM Note", "Contact", contact, opportunity=deal)

		delete_deal(deal)

		self.assertFalse(frappe.db.exists("CRM Deal", deal))
		for doctype, name in owned.items():
			self.assertFalse(frappe.db.exists(doctype, name), doctype)
		self.assertEqual(frappe.db.get_value("FCRM Note", contact_note, ["reference_docname", "opportunity"]), (contact, None))
		self.assertTrue(frappe.db.exists("Contact", contact))
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, ["status", FIELD_CONVERTED_DEAL]), ("Converted", None))

	def test_unlink_all_then_delete_organization(self):
		_, deal, contact = self._converted("Egle", "Egle Co")
		org = frappe.db.get_value("CRM Deal", deal, "organization")
		frappe.db.set_value("Contact", contact, CONTACT_ORGANIZATION_LINK_FIELD, org)
		items = [
			{"doctype": d["reference_doctype"], "docname": d["reference_docname"]}
			for d in get_linked_docs_of_document("CRM Organization", org)
		]
		self.assertTrue(items)

		remove_linked_doc_reference(items, doctype="CRM Organization", docname=org)

		self.assertEqual(get_linked_docs_of_document("CRM Organization", org), [])
		self.assertIsNone(frappe.db.get_value("CRM Deal", deal, "organization"))
		frappe.delete_doc("CRM Organization", org)

	def test_delete_linked_deal_from_organization(self):
		_, deal, _ = self._converted("Fima", "Fima Co")
		org = frappe.db.get_value("CRM Deal", deal, "organization")
		self._on("FCRM Note", "CRM Deal", deal)

		remove_linked_doc_reference(
			[{"doctype": "CRM Deal", "docname": deal}], delete=True, doctype="CRM Organization", docname=org
		)

		self.assertFalse(frappe.db.exists("CRM Deal", deal))

	def test_unlink_contact_removes_only_its_row_on_the_deal(self):
		_, deal, contact = self._converted("Gita", "Gita Co")
		other = frappe.get_doc({"doctype": "Contact", "first_name": "Other"}).insert(ignore_permissions=True).name
		frappe.get_doc({"doctype": "CRM Contacts", "parent": deal, "parenttype": "CRM Deal", "parentfield": "contacts", "contact": other}).insert(ignore_permissions=True)

		remove_linked_doc_reference([{"doctype": "CRM Deal", "docname": deal}], doctype="Contact", docname=contact)

		rows = frappe.get_all("CRM Contacts", filters={"parent": deal, "parenttype": "CRM Deal"}, pluck="contact")
		self.assertEqual(rows, [other])

# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from crm.api.exchange_rate import get_exchange_rate
from crm.fcrm.doctype.crm_organization.company_code import (
	FIELD_COMPANY_CODE,
	FIELD_COMPANY_CODE_KEY,
	find_organization_by_code,
	normalize_company_code,
)


class CRMOrganization(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		address: DF.Link | None
		annual_revenue: DF.Currency
		currency: DF.Link | None
		exchange_rate: DF.Float
		industry: DF.Link | None
		no_of_employees: DF.Literal["1-10", "11-50", "51-200", "201-500", "501-1000", "1000+"]
		organization_logo: DF.AttachImage | None
		organization_name: DF.Data | None
		territory: DF.Link | None
		website: DF.Data | None
	# end: auto-generated types

	def validate(self):
		self.validate_company_code()
		self.update_exchange_rate()

	def validate_company_code(self):
		"""Enforce the canonical Company Code contract (TXB-243).

		Require a code for every new Organization, and whenever an existing code is supplied or
		changed, but grandfather legacy rows that stay blank so an unrelated edit is never
		blocked. When a code is present (and new or changed) derive the hidden normalized key,
		reject a normalized duplicate -- naming the conflicting Organization -- and let the key's
		database uniqueness serialise concurrent writers. Guarded by ``has_field`` so a site that
		has not yet run the company-code field patch is unaffected.
		"""
		if not self.meta.has_field(FIELD_COMPANY_CODE):
			return

		code = (self.get(FIELD_COMPANY_CODE) or "").strip()
		self.set(FIELD_COMPANY_CODE, code or None)
		key = normalize_company_code(code)
		changed = self.is_new() or self.has_value_changed(FIELD_COMPANY_CODE)

		if not key:
			# Blank (or whitespace-only) code. Required on create and when an existing code is
			# being cleared; otherwise this is a grandfathered legacy row -- allow the save.
			if self.is_new():
				frappe.throw(_("Company Code is required."), title=_("Company Code Required"))
			if changed:
				frappe.throw(
					_("Company Code cannot be removed once set."),
					title=_("Company Code Required"),
				)
			if self.meta.has_field(FIELD_COMPANY_CODE_KEY):
				self.set(FIELD_COMPANY_CODE_KEY, None)
			return

		if not changed:
			# Existing row, code unchanged: leave the key exactly as migration left it. Touching
			# it here would try to claim a key an unresolved legacy collision twin also holds and
			# block an unrelated edit -- the grandfather clause.
			return

		if self.meta.has_field(FIELD_COMPANY_CODE_KEY):
			self.set(FIELD_COMPANY_CODE_KEY, key)

		match = find_organization_by_code(key, exclude=None if self.is_new() else self.name)
		if match:
			frappe.throw(
				_("Company Code already used by {0} ({1}).").format(
					match.get("organization_name") or match["name"], match["name"]
				),
				title=_("Duplicate Company Code"),
			)

	def after_insert(self):
		# Auto-enrich a new Organization from its website (best-effort, background job).
		from crm.domain_enrichment.tasks import auto_enrich_on_create

		auto_enrich_on_create(self)

	def update_exchange_rate(self):
		if self.has_value_changed("currency") or not self.exchange_rate:
			system_currency = frappe.db.get_single_value("FCRM Settings", "currency") or "USD"
			exchange_rate = 1
			if self.currency and self.currency != system_currency:
				exchange_rate = get_exchange_rate(self.currency, system_currency)

			self.db_set("exchange_rate", exchange_rate)

	@staticmethod
	def default_list_data():
		columns = [
			{
				"label": "Organization",
				"type": "Data",
				"key": "organization_name",
				"width": "16rem",
			},
			{
				"label": "Website",
				"type": "Data",
				"key": "website",
				"width": "14rem",
			},
			{
				"label": "Industry",
				"type": "Link",
				"key": "industry",
				"options": "CRM Industry",
				"width": "14rem",
			},
			{
				"label": "Annual Revenue",
				"type": "Currency",
				"key": "annual_revenue",
				"width": "14rem",
			},
			{
				"label": "Last Modified",
				"type": "Datetime",
				"key": "modified",
				"width": "8rem",
			},
		]
		rows = [
			"name",
			"organization_name",
			"organization_logo",
			"website",
			"industry",
			"currency",
			"annual_revenue",
			"modified",
		]
		return {"columns": columns, "rows": rows}

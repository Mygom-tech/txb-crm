# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""Tests for the every-migrate retirement of superseded database scripts.

The bug these guard against is the duplicate **Take Action** menu: `CRM Wizard Framework`
enabled alongside the native menu that replaced it. The one-shot patch could not hold that
closed, so the assertions below are about re-runnability, not about a single flip.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.txb.retired_scripts import (
	RETIRED_FORM_SCRIPTS,
	RETIRED_SERVER_SCRIPTS,
	retire_scripts,
)

WIZARD = "CRM Wizard Framework"


class TestRetiredScripts(FrappeTestCase):
	def test_it_disables_a_re_enabled_form_script(self):
		"""The whole point: a script switched back on must not survive the next migrate."""
		if not frappe.db.exists("CRM Form Script", WIZARD):
			self.skipTest(f"{WIZARD} is not present on this site")

		frappe.db.set_value("CRM Form Script", WIZARD, "enabled", 1)

		retire_scripts()

		self.assertEqual(frappe.db.get_value("CRM Form Script", WIZARD, "enabled"), 0)

	def test_it_is_idempotent(self):
		"""after_migrate runs it on every deploy, so a second run must be a no-op."""
		retire_scripts()
		retire_scripts()

		for name in RETIRED_FORM_SCRIPTS:
			if frappe.db.exists("CRM Form Script", name):
				self.assertEqual(frappe.db.get_value("CRM Form Script", name, "enabled"), 0)

		for name in RETIRED_SERVER_SCRIPTS:
			if frappe.db.exists("Server Script", name):
				self.assertEqual(frappe.db.get_value("Server Script", name, "disabled"), 1)

	def test_it_tolerates_names_that_do_not_exist(self):
		"""Fresh sites have none of these rows; migrate must not fail there."""
		from crm.txb.retired_scripts import _disable

		self.assertEqual(_disable("CRM Form Script", ("No Such Script",), "enabled", 0), [])

	def test_no_retired_form_script_still_serves_the_browser(self):
		"""get_form_script filters on `enabled`, so this is the user-visible guarantee."""
		from crm.fcrm.doctype.crm_form_script.crm_form_script import get_form_script

		retire_scripts()

		for doctype in ("CRM Deal", "CRM Lead", "Contact"):
			script = get_form_script(doctype) or ""
			served = script if isinstance(script, str) else "".join(script)
			for name in RETIRED_FORM_SCRIPTS:
				if not frappe.db.exists("CRM Form Script", name):
					continue
				body = frappe.db.get_value("CRM Form Script", name, "script") or ""
				self.assertNotIn(body[:200], served, f"{name} is still served for {doctype}")

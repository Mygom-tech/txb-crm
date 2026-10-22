# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""Every email this site sends is wrapped in the TXB shell, not Frappe's standard one."""

import frappe
from frappe.email.email_body import get_formatted_html
from frappe.tests.utils import FrappeTestCase


class TestEmailShell(FrappeTestCase):
	def test_frappe_emails_render_in_the_txb_shell(self):
		frappe.db.set_default("disable_standard_email_footer", 1)  # as install and the patch set it
		html = get_formatted_html(
			"Sveiki",
			"<p>Turinys</p>",
			header=["Antraštė", "blue"],
			email_account=frappe._dict(brand_logo=None),
		)

		for part in (
			"https://assets.txbconsulting.com/logo.png",
			"Ten Times Better Consulting",
			"Turinys",
			"Antraštė",
		):
			self.assertIn(part, html)
		self.assertNotIn('class="email-container"', html)  # Frappe's own wrapper
		# Frappe's footer slots survive inside the shell; its "Sent via" line does not.
		self.assertIn("<!--email_open_check-->", html)
		self.assertEqual(html.count("<!--unsubscribe link here-->"), 1)
		self.assertNotIn("default-mail-footer", html)

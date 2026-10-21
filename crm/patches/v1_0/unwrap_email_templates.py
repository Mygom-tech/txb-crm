"""Brand every email with the site shell and strip the old TXB-116 wrapper out of templates.

Every email now goes out in the site email shell (`crm/templates/emails/standard.html`), so a
template still holding its own branded wrapper would show two headers. Only the content
container (greeting to signature) is kept; a template already content-only is left as it is.
Frappe's "Sent via" footer line is switched off; its other footer parts stay in the shell.
"""

import re

import frappe
from bs4 import BeautifulSoup

from crm.txb.constants import CONFIRMATION_TEMPLATE
from crm.txb.delivery_coach_notifications import ASSIGNMENT_EMAIL_TEMPLATE, CLIENT_EMAIL_TEMPLATE

# Each Email Template's greeting line, which sat in the TXB-116 padded content container. The
# greeting text anchors it, so a placeholder used elsewhere (a table cell) is never taken for it.
TEMPLATE_GREETINGS = {
	CONFIRMATION_TEMPLATE: re.compile(r"\{\{\s*first_name\s*\}\}"),
	ASSIGNMENT_EMAIL_TEMPLATE: re.compile(r"Hi \{\{\s*intended_coach_name\b"),
	CLIENT_EMAIL_TEMPLATE: re.compile(r"Sveiki, \{\{\s*client_first_name\b"),
}


def unwrap_template_body(html: str | None, greeting: re.Pattern) -> str | None:
	"""The content of an Email Template body without its TXB-116 header and footer.

	The content is the padded container (the nearest `div` or `td`) holding the greeting,
	signature included. A body with no such container -- or one whose container holds all of
	its text -- is already content only and comes back unchanged.
	"""
	soup = BeautifulSoup(html or "", "html.parser")
	found = soup.find(string=greeting)
	container = found.find_parent(["div", "td"]) if found else None
	if container is None or container.get_text(strip=True) == soup.get_text(strip=True):
		return html
	return container.decode_contents()


def execute():
	frappe.db.set_default("disable_standard_email_footer", 1)
	for name, greeting in TEMPLATE_GREETINGS.items():
		html = frappe.db.get_value("Email Template", name, "response_html")
		body = unwrap_template_body(html, greeting)
		if body != html:
			frappe.db.set_value("Email Template", name, "response_html", body)

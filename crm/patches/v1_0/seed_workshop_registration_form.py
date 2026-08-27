"""TXB-15: backfill the Workshop Registration form onto deployed sites.

New installs get it from ``crm.install.after_install``; see ``crm.txb.workshop_form``.
"""

import frappe

from crm.txb.workshop_form import seed_workshop_form


def execute():
	seed_workshop_form()
	frappe.db.commit()

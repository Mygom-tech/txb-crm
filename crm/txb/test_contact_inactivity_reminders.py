# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-280: each due Contact inactivity cycle gets one reminder Task for its enabled owner.

Contacts are created before the pinned rollout date, so each starts with a Rollout-anchored cycle
due at DUE_AT; everything is rolled back afterwards.
"""

from datetime import date, datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime

from crm.fcrm.doctype.crm_human_contact_attribution.test_crm_human_contact_attribution import (
	make_contact,
	make_event,
)
from crm.txb import contact_inactivity as engine
from crm.txb.contact_inactivity import (
	CYCLE_DOCTYPE,
	SETTING_INTERVAL_MINUTES,
	SETTINGS_DOCTYPE,
	ensure_interval_setting,
	start_cycle,
)
from crm.txb.contact_inactivity_reminders import (
	OWNER_FIELD,
	remind,
	run_contact_inactivity_reminders,
)
from crm.txb.test_ownership import ensure_user

ROLLOUT = datetime(2026, 6, 1, 10, 0, 0)
BEFORE_ROLLOUT = "2025-01-01 09:00:00"
DUE_AT = datetime(2026, 12, 1, 10, 0, 0)
LATER = "2026-12-01 10:05:00"

OWNER = "txb280-owner@example.com"
NEW_OWNER = "txb280-new-owner@example.com"
DISABLED_OWNER = "txb280-disabled-owner@example.com"


def cycle_row(cycle):
	return frappe.db.get_value(
		CYCLE_DOCTYPE,
		cycle,
		["status", "live_key", "reminder_task", "reminded_at", "exception", "exception_at"],
		as_dict=True,
	)


def cycles(contact):
	return frappe.get_all(
		CYCLE_DOCTYPE,
		filters={"contact": contact},
		fields=["name", "cycle_no", "status", "anchor_source", "anchor_at"],
		order_by="cycle_no",
	)


def tasks(contact):
	return frappe.get_all(
		"CRM Task",
		filters={"reference_doctype": "Contact", "reference_docname": contact},
		pluck="name",
	)


def open_todos(task):
	return frappe.get_all(
		"ToDo",
		filters={"reference_type": "CRM Task", "reference_name": task, "status": "Open"},
		pluck="allocated_to",
	)


def contact_with_cycle(owner=OWNER):
	"""A pre-rollout Contact owned by `owner` (None: unowned), and its live cycle due at DUE_AT."""
	contact = make_contact("Rem", f"rem-{frappe.generate_hash(length=6)}@example.com")
	frappe.db.set_value(
		"Contact", contact, {OWNER_FIELD: owner, "creation": BEFORE_ROLLOUT}, update_modified=False
	)
	return contact, start_cycle(contact)


class TestContactInactivityReminders(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_interval_setting()
		for user in (OWNER, NEW_OWNER, DISABLED_OWNER):
			ensure_user(user, ["Sales User"])
		frappe.db.set_value("User", DISABLED_OWNER, "enabled", 0)
		frappe.db.commit()

	def setUp(self):
		patcher = patch.object(engine, "rollout_date", return_value=ROLLOUT)
		patcher.start()
		self.addCleanup(patcher.stop)
		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_INTERVAL_MINUTES, 0)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	# -- ac-1 -----------------------------------------------------------------------------------

	def test_a_due_cycle_gets_one_task_and_todo_for_its_owner(self):
		contact, cycle = contact_with_cycle()

		run_contact_inactivity_reminders(DUE_AT)

		[task] = tasks(contact)
		row = cycle_row(cycle)
		self.assertEqual((row.status, row.live_key, row.reminder_task), ("Reminded", contact, task))
		self.assertTrue(row.reminded_at)
		doc = frappe.get_doc("CRM Task", task)
		self.assertEqual((doc.reference_doctype, doc.reference_docname), ("Contact", contact))
		self.assertEqual(doc.assigned_to, OWNER)
		self.assertEqual(get_datetime(doc.due_date).date(), date(2026, 12, 1))
		self.assertEqual(open_todos(task), [OWNER])

	def test_a_cycle_not_yet_due_gets_nothing(self):
		contact, cycle = contact_with_cycle()

		run_contact_inactivity_reminders("2026-12-01 09:59:59")

		self.assertEqual(tasks(contact), [])
		self.assertEqual(cycle_row(cycle).status, "Open")

	# -- ac-2 -----------------------------------------------------------------------------------

	def test_repeated_and_overlapping_runs_leave_one_task_and_one_todo(self):
		contact, cycle = contact_with_cycle()

		run_contact_inactivity_reminders(DUE_AT)
		run_contact_inactivity_reminders(LATER)
		# An overlapping run that listed the cycle while it was still Due reaches it afterwards.
		self.assertIsNone(remind(cycle))

		[task] = tasks(contact)
		self.assertEqual(cycle_row(cycle).reminder_task, task)
		self.assertEqual(open_todos(task), [OWNER])

	# -- ac-3 -----------------------------------------------------------------------------------

	def test_a_deleted_reminder_is_never_replaced(self):
		contact, cycle = contact_with_cycle()
		run_contact_inactivity_reminders(DUE_AT)
		[task] = tasks(contact)

		frappe.delete_doc("CRM Task", task, ignore_permissions=True)
		run_contact_inactivity_reminders(LATER)

		self.assertEqual(tasks(contact), [])
		row = cycle_row(cycle)
		self.assertEqual((row.status, row.reminder_task), ("Reminded", task))

	# -- ac-4 -----------------------------------------------------------------------------------

	def test_an_unowned_or_disabled_owner_contact_records_an_exception_instead(self):
		unowned, unowned_cycle = contact_with_cycle(None)
		disabled, disabled_cycle = contact_with_cycle(DISABLED_OWNER)
		modified = {c: frappe.db.get_value("Contact", c, "modified") for c in (unowned, disabled)}

		run_contact_inactivity_reminders(DUE_AT)

		for contact, cycle, exception, owner in (
			(unowned, unowned_cycle, "Unowned", None),
			(disabled, disabled_cycle, "Disabled Owner", DISABLED_OWNER),
		):
			self.assertEqual(tasks(contact), [])
			row = cycle_row(cycle)
			self.assertEqual((row.status, row.reminder_task, row.exception), ("Due", None, exception))
			self.assertTrue(row.exception_at)
			# The reminder never touches the Contact: its owner stays as it was.
			self.assertEqual(frappe.db.get_value("Contact", contact, OWNER_FIELD), owner)
			self.assertEqual(frappe.db.get_value("Contact", contact, "modified"), modified[contact])

	def test_an_exception_is_kept_until_an_enabled_owner_is_reminded(self):
		contact, cycle = contact_with_cycle(None)
		run_contact_inactivity_reminders(DUE_AT)
		exception_at = cycle_row(cycle).exception_at

		run_contact_inactivity_reminders(LATER)
		self.assertEqual(cycle_row(cycle).exception_at, exception_at)

		frappe.db.set_value("Contact", contact, OWNER_FIELD, OWNER, update_modified=False)
		run_contact_inactivity_reminders(LATER)

		[task] = tasks(contact)
		self.assertEqual((cycle_row(cycle).status, cycle_row(cycle).reminder_task), ("Reminded", task))
		self.assertEqual(open_todos(task), [OWNER])

	# -- ac-5 -----------------------------------------------------------------------------------

	def test_an_owner_change_moves_the_open_reminder_and_its_todo(self):
		contact, _cycle = contact_with_cycle()
		run_contact_inactivity_reminders(DUE_AT)
		[task] = tasks(contact)

		doc = frappe.get_doc("Contact", contact)
		doc.set(OWNER_FIELD, NEW_OWNER)
		doc.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("CRM Task", task, "assigned_to"), NEW_OWNER)
		self.assertEqual(open_todos(task), [NEW_OWNER])
		self.assertEqual(frappe.db.get_value("Contact", contact, OWNER_FIELD), NEW_OWNER)

	def test_a_disabled_new_owner_leaves_the_reminder_where_it_is(self):
		contact, _cycle = contact_with_cycle()
		run_contact_inactivity_reminders(DUE_AT)
		[task] = tasks(contact)

		doc = frappe.get_doc("Contact", contact)
		doc.set(OWNER_FIELD, DISABLED_OWNER)
		doc.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("CRM Task", task, "assigned_to"), OWNER)
		self.assertEqual(open_todos(task), [OWNER])
		self.assertEqual(frappe.db.get_value("Contact", contact, OWNER_FIELD), DISABLED_OWNER)

	# -- ac-6 -----------------------------------------------------------------------------------

	def test_done_or_canceled_settles_the_cycle(self):
		for status in ("Done", "Canceled"):
			with self.subTest(status=status):
				contact, cycle = contact_with_cycle()
				run_contact_inactivity_reminders(DUE_AT)
				[task] = tasks(contact)

				doc = frappe.get_doc("CRM Task", task)
				doc.status = status
				doc.save(ignore_permissions=True)

				row = cycle_row(cycle)
				self.assertEqual((row.status, row.live_key, row.reminder_task), ("Settled", None, task))

				# Human contact after that opens the next cycle; the settled one stays settled.
				make_event("Contact", contact, "Email", [], "2026-12-02 09:00:00")
				settled, opened = cycles(contact)
				self.assertEqual((settled.status, opened.cycle_no, opened.status), ("Settled", 2, "Open"))

	def test_human_contact_cancels_the_open_reminder_and_opens_the_next_cycle(self):
		contact, cycle = contact_with_cycle()
		run_contact_inactivity_reminders(DUE_AT)
		[task] = tasks(contact)

		make_event("Contact", contact, "Email", [], "2026-12-02 09:00:00")

		self.assertEqual(frappe.db.get_value("CRM Task", task, "status"), "Canceled")
		closed, opened = cycles(contact)
		self.assertEqual((closed.name, closed.status), (cycle, "Closed"))
		self.assertEqual((opened.cycle_no, opened.status, opened.anchor_source), (2, "Open", "History"))
		self.assertEqual(str(opened.anchor_at), "2026-12-02 09:00:00")

	def test_older_human_contact_leaves_the_reminder_open(self):
		contact, cycle = contact_with_cycle()
		run_contact_inactivity_reminders(DUE_AT)
		[task] = tasks(contact)

		make_event("Contact", contact, "Email", [], "2026-05-01 09:00:00")

		self.assertEqual(frappe.db.get_value("CRM Task", task, "status"), "Backlog")
		self.assertEqual([row.name for row in cycles(contact)], [cycle])
		self.assertEqual(cycle_row(cycle).status, "Reminded")

# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-278: every Contact has one durable, live inactivity cycle due after the site interval.

The rollout date is pinned to ROLLOUT so anchors are deterministic; each test's Contacts are
created fresh and everything is rolled back afterwards.
"""

from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.fcrm.doctype.crm_human_contact_attribution.test_crm_human_contact_attribution import (
	make_contact,
	make_event,
)
from crm.patches.v1_0 import add_contact_inactivity_cycles
from crm.txb import contact_inactivity as engine
from crm.txb.contact_attribution import attribute_event
from crm.txb.contact_inactivity import (
	CYCLE_DOCTYPE,
	SETTING_INTERVAL_MINUTES,
	SETTINGS_DOCTYPE,
	compute_due_at,
	ensure_interval_setting,
	evaluate_due,
	interval_minutes,
	mark_reminded,
	on_qualifying_contact,
	recompute_unreminded,
	settle,
	start_cycle,
)
from crm.txb.test_ownership import ensure_user

ROLLOUT = datetime(2026, 6, 1, 10, 0, 0)
BEFORE_ROLLOUT = "2025-01-01 09:00:00"
SALES_MANAGER = "txb278-sales-manager@example.com"
SYSTEM_MANAGER = "txb278-system-manager@example.com"


def cycles(contact):
	return frappe.get_all(
		CYCLE_DOCTYPE,
		filters={"contact": contact},
		fields=["name", "cycle_no", "status", "live_key", "anchor_at", "anchor_source", "due_at"],
		order_by="cycle_no",
	)


def live_cycles(contact):
	return [row for row in cycles(contact) if row.live_key]


def new_contact(created=None):
	suffix = frappe.generate_hash(length=6)
	contact = make_contact("Cy", f"cy-{suffix}@example.com")
	if created:
		frappe.db.set_value("Contact", contact, "creation", created, update_modified=False)
	return contact


def record_human_contact(contact, occurred_at):
	attribute_event(make_event("Contact", contact, "Email", [], occurred_at))


def save_interval(value):
	settings = frappe.get_single(SETTINGS_DOCTYPE)
	settings.set(SETTING_INTERVAL_MINUTES, value)
	settings.save(ignore_permissions=True)


class TestContactInactivity(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_interval_setting()
		ensure_user(SALES_MANAGER, ["Sales Manager"])
		ensure_user(SYSTEM_MANAGER, ["System Manager"])
		frappe.db.commit()

	def setUp(self):
		for target in (engine, add_contact_inactivity_cycles):
			patcher = patch.object(target, "rollout_date", return_value=ROLLOUT)
			patcher.start()
			self.addCleanup(patcher.stop)
		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_INTERVAL_MINUTES, 0)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	# -- ac-1 -----------------------------------------------------------------------------------

	def test_backfill_anchors_on_history_else_rollout(self):
		with_history = new_contact(BEFORE_ROLLOUT)
		without_history = new_contact(BEFORE_ROLLOUT)
		record_human_contact(with_history, "2026-03-15 14:30:00")

		add_contact_inactivity_cycles.execute()

		[history] = cycles(with_history)
		self.assertEqual((history.anchor_source, str(history.anchor_at)), ("History", "2026-03-15 14:30:00"))
		[rollout] = cycles(without_history)
		self.assertEqual((rollout.anchor_source, rollout.anchor_at), ("Rollout", ROLLOUT))
		self.assertEqual((rollout.status, rollout.live_key, rollout.cycle_no), ("Open", without_history, 1))
		self.assertEqual(rollout.due_at, datetime(2026, 12, 1, 10, 0, 0))

	def test_a_contact_created_after_rollout_anchors_on_creation(self):
		contact = new_contact()

		start_cycle(contact)

		[cycle] = cycles(contact)
		self.assertEqual(cycle.anchor_source, "Creation")
		self.assertEqual(cycle.anchor_at, frappe.db.get_value("Contact", contact, "creation"))

	# -- ac-2 -----------------------------------------------------------------------------------

	def test_zero_or_blank_interval_adds_six_calendar_months(self):
		expected = datetime(2027, 2, 28, 9, 0, 0)
		self.assertEqual(compute_due_at("2026-08-31 09:00:00", 0), expected)
		self.assertEqual(compute_due_at("2026-08-31 09:00:00", ""), expected)

		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_INTERVAL_MINUTES, None)
		self.assertEqual(compute_due_at("2026-08-31 09:00:00"), expected)

	def test_a_minute_interval_adds_exactly_those_minutes(self):
		self.assertEqual(compute_due_at("2026-08-31 09:00:00", 90), datetime(2026, 8, 31, 10, 30, 0))

		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_INTERVAL_MINUTES, 90)
		self.assertEqual(compute_due_at("2026-08-31 23:00:00"), datetime(2026, 9, 1, 0, 30, 0))

	# -- ac-3 -----------------------------------------------------------------------------------

	def test_out_of_range_or_fractional_interval_is_rejected(self):
		for value in (-1, 525601, 1.5, "1.5", "ninety"):
			with self.subTest(value=value), self.assertRaises(frappe.ValidationError):
				save_interval(value)

	def test_only_a_system_manager_may_change_the_interval(self):
		frappe.set_user(SALES_MANAGER)
		with self.assertRaises(frappe.PermissionError):
			save_interval(90)
		# Saving other settings without touching the interval stays allowed.
		save_interval(0)

		frappe.set_user(SYSTEM_MANAGER)
		save_interval(525600)
		self.assertEqual(interval_minutes(), 525600)

	def test_a_saved_interval_is_read_back_at_once(self):
		self.assertEqual(interval_minutes(), 0)
		save_interval(90)
		self.assertEqual(interval_minutes(), 90)

	# -- ac-4 -----------------------------------------------------------------------------------

	def test_newer_contact_closes_the_live_cycle_and_opens_the_next(self):
		contact = new_contact(BEFORE_ROLLOUT)
		first = start_cycle(contact)

		second = on_qualifying_contact(contact, "2026-09-01 12:00:00")

		closed, opened = cycles(contact)
		self.assertEqual((closed.name, closed.status, closed.live_key), (first, "Closed", None))
		self.assertEqual(opened.name, second)
		self.assertEqual((opened.cycle_no, opened.status, opened.live_key), (2, "Open", contact))
		self.assertEqual((opened.anchor_source, str(opened.anchor_at)), ("History", "2026-09-01 12:00:00"))

	def test_equal_or_older_contact_changes_nothing(self):
		contact = new_contact(BEFORE_ROLLOUT)
		start_cycle(contact)
		on_qualifying_contact(contact, "2026-09-01 12:00:00")
		before = cycles(contact)

		self.assertIsNone(on_qualifying_contact(contact, "2026-09-01 12:00:00"))
		self.assertIsNone(on_qualifying_contact(contact, "2026-08-01 12:00:00"))
		self.assertEqual(cycles(contact), before)

	# -- ac-5 -----------------------------------------------------------------------------------

	def test_start_cycle_and_backfill_are_idempotent(self):
		contact = new_contact(BEFORE_ROLLOUT)

		first = start_cycle(contact)
		self.assertEqual(start_cycle(contact), first)
		add_contact_inactivity_cycles.execute()
		add_contact_inactivity_cycles.execute()

		self.assertEqual([row.name for row in cycles(contact)], [first])
		self.assertEqual(len(live_cycles(contact)), 1)

	def test_a_duplicate_insert_is_absorbed_and_returns_the_live_cycle(self):
		contact = new_contact(BEFORE_ROLLOUT)
		first = start_cycle(contact)

		# A racing writer that read before the first cycle committed tries to open it again.
		with patch.object(engine, "_latest_cycle", return_value=None):
			self.assertEqual(start_cycle(contact), first)
		self.assertEqual([row.name for row in cycles(contact)], [first])

		# live_key is unique in the database, not only in the engine's bookkeeping.
		frappe.db.savepoint("txb278_duplicate")
		with self.assertRaises(frappe.UniqueValidationError):
			frappe.get_doc(
				{
					"doctype": CYCLE_DOCTYPE,
					"contact": contact,
					"cycle_no": 2,
					"live_key": contact,
					"anchor_at": ROLLOUT,
					"anchor_source": "Rollout",
					"due_at": ROLLOUT,
				}
			).insert(ignore_permissions=True)
		frappe.db.rollback(save_point="txb278_duplicate")

	# -- ac-6 -----------------------------------------------------------------------------------

	def test_recompute_changes_only_open_cycles(self):
		open_, due, reminded = (new_contact(BEFORE_ROLLOUT) for _ in range(3))
		names = {contact: start_cycle(contact) for contact in (open_, due, reminded)}
		frappe.db.set_value(CYCLE_DOCTYPE, names[due], "status", "Due")
		frappe.db.set_value(CYCLE_DOCTYPE, names[reminded], "status", "Reminded")

		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_INTERVAL_MINUTES, 90)
		recompute_unreminded()

		self.assertEqual(cycles(open_)[0].due_at, datetime(2026, 6, 1, 11, 30, 0))
		for contact in (due, reminded):
			self.assertEqual(cycles(contact)[0].due_at, datetime(2026, 12, 1, 10, 0, 0))

	def test_evaluate_due_marks_only_overdue_open_cycles(self):
		overdue, pending = new_contact(BEFORE_ROLLOUT), new_contact(BEFORE_ROLLOUT)
		overdue_cycle, pending_cycle = start_cycle(overdue), start_cycle(pending)
		frappe.db.set_value(CYCLE_DOCTYPE, pending_cycle, "due_at", "2026-12-02 10:00:00")

		due = evaluate_due("2026-12-01 10:00:00")

		self.assertIn(overdue_cycle, due)
		self.assertNotIn(pending_cycle, due)
		self.assertEqual(cycles(overdue)[0].status, "Due")
		self.assertEqual(cycles(pending)[0].status, "Open")
		self.assertNotIn(overdue_cycle, evaluate_due("2026-12-01 10:00:00"))

	def test_settle_frees_the_live_key_and_mark_reminded_keeps_it(self):
		settled, reminded = new_contact(BEFORE_ROLLOUT), new_contact(BEFORE_ROLLOUT)
		settled_cycle, reminded_cycle = start_cycle(settled), start_cycle(reminded)

		self.assertTrue(settle(settled_cycle))
		self.assertEqual((cycles(settled)[0].status, cycles(settled)[0].live_key), ("Settled", None))
		self.assertFalse(settle(settled_cycle))

		self.assertFalse(mark_reminded(reminded_cycle))
		evaluate_due("2026-12-01 10:00:00")
		self.assertTrue(mark_reminded(reminded_cycle))
		self.assertEqual(
			(cycles(reminded)[0].status, cycles(reminded)[0].live_key), ("Reminded", reminded)
		)

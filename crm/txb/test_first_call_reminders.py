# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-265: an Opportunity created straight into Active is an activation too.

The handover flow inserts the Delivering Coaching Opportunity already Active, so the cycle
that makes it eligible for the first-coaching-call reminder has to be minted by the insert
itself -- there is no later transition to hang it on. These tests pin that, and pin the three
things it must not disturb: a non-Active or off-pipeline insert still gets no cycle, a real
transition into Active (and every leave/re-entry after it) still reminds exactly once, and
the rows that were already Active when the migration ran are still left alone.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from crm.patches.v1_0.add_first_call_reminder_fields import execute as install_reminder_fields
from crm.txb.admin_assignment import ADMIN_TASK_ASSIGNEE_FIELD, SETTINGS_DOCTYPE
from crm.txb.constants import (
	ADMIN_ROLE,
	FIELD_ACTIVATION_CYCLE,
	FIELD_ACTIVATION_STARTED_ON,
	FIELD_REMINDER_CYCLE,
	PIPELINE_DELIVERING_COACHING,
	PIPELINE_WORKSHOP,
	STATUS_ACTIVE,
)
from crm.txb.first_call_reminders import (
	DEAL_DOCTYPE,
	TASK_DOCTYPE,
	reminder_delay_minutes,
	run_first_call_reminders,
)

ADMIN = "txb-reminder-admin@example.com"

# The six delivery-readiness conditions (TXB-251), so a test may move a deal into Active the
# way a user does rather than around the gate that guards it.
READY_FIELDS = {
	"custom_delivery_coach": "Administrator",
	"custom_contract_signed": "Yes",
	"custom_payment_confirmed": "Yes",
	"custom_test_completed": "Yes",
	"custom_delivery_notes": "Kick-off agreed.",
}


class TestFirstCallActivationCycles(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("User", ADMIN):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": ADMIN,
					"first_name": "Reminder",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)
		frappe.get_doc("User", ADMIN).add_roles("Sales User", ADMIN_ROLE)
		frappe.db.commit()  # nosemgrep -- the assignee must outlive per-test rollback

	def setUp(self):
		# The reminder metadata this feature is entirely made of; installing it here exercises
		# the flow against the schema a migrated site actually has.
		install_reminder_fields()
		frappe.db.set_single_value(SETTINGS_DOCTYPE, ADMIN_TASK_ASSIGNEE_FIELD, ADMIN)

	def tearDown(self):
		frappe.db.rollback()

	# -- helpers ---------------------------------------------------------------------------

	def make_deal(self, status=STATUS_ACTIVE, pipeline=PIPELINE_DELIVERING_COACHING, **fields):
		return frappe.get_doc(
			{
				"doctype": DEAL_DOCTYPE,
				"pipeline_type": pipeline,
				"status": status,
				**fields,
			}
		).insert(ignore_permissions=True)

	def cycle_of(self, deal_name):
		return frappe.db.get_value(
			DEAL_DOCTYPE,
			deal_name,
			[FIELD_ACTIVATION_CYCLE, FIELD_ACTIVATION_STARTED_ON],
			as_dict=True,
		)

	def make_overdue(self, deal_name):
		"""Age the deal's activation past the configured delay without touching anything else."""
		started = add_to_date(now_datetime(), minutes=-(reminder_delay_minutes() + 1))
		frappe.db.set_value(
			DEAL_DOCTYPE, deal_name, FIELD_ACTIVATION_STARTED_ON, started, update_modified=False
		)

	def reminders_for(self, cycle):
		return frappe.get_all(TASK_DOCTYPE, filters={FIELD_REMINDER_CYCLE: cycle}, pluck="name")

	def reminders_on(self, deal_name):
		return frappe.get_all(
			TASK_DOCTYPE,
			filters={"reference_docname": deal_name, FIELD_REMINDER_CYCLE: ("is", "set")},
			pluck="name",
		)

	# -- ac-1: the insert straight into Active ---------------------------------------------

	def test_a_deal_inserted_active_opens_a_cycle(self):
		deal = self.make_deal()

		stored = self.cycle_of(deal.name)
		self.assertTrue(stored.get(FIELD_ACTIVATION_CYCLE))
		self.assertTrue(stored.get(FIELD_ACTIVATION_STARTED_ON))

	def test_a_deal_inserted_active_is_reminded_exactly_once_after_the_delay(self):
		deal = self.make_deal()
		cycle = self.cycle_of(deal.name).get(FIELD_ACTIVATION_CYCLE)

		run_first_call_reminders()
		self.assertEqual(self.reminders_for(cycle), [], "reminded before the delay elapsed")

		self.make_overdue(deal.name)
		run_first_call_reminders()
		run_first_call_reminders()
		self.assertEqual(len(self.reminders_for(cycle)), 1)

	# -- ac-2: everything the insert case must not disturb ----------------------------------

	def test_a_deal_inserted_below_active_gets_no_cycle(self):
		deal = self.make_deal(status="Contract Cleared")

		self.assertFalse(self.cycle_of(deal.name).get(FIELD_ACTIVATION_CYCLE))

	def test_an_active_deal_on_another_pipeline_gets_no_cycle(self):
		deal = self.make_deal(pipeline=PIPELINE_WORKSHOP)

		self.assertFalse(self.cycle_of(deal.name).get(FIELD_ACTIVATION_CYCLE))

	def test_a_later_transition_into_active_still_opens_a_cycle(self):
		deal = self.make_deal(status="Contract Cleared", **READY_FIELDS)
		self.assertFalse(self.cycle_of(deal.name).get(FIELD_ACTIVATION_CYCLE))

		deal.status = STATUS_ACTIVE
		deal.save(ignore_permissions=True)

		stored = self.cycle_of(deal.name)
		self.assertTrue(stored.get(FIELD_ACTIVATION_CYCLE))
		self.assertTrue(stored.get(FIELD_ACTIVATION_STARTED_ON))

	def test_an_unrelated_save_keeps_the_cycle_the_activation_opened(self):
		deal = self.make_deal(**READY_FIELDS)
		first = self.cycle_of(deal.name)

		deal.reload()
		deal.custom_delivery_notes = "Coach briefed."
		deal.save(ignore_permissions=True)

		self.assertEqual(self.cycle_of(deal.name), first)

	def test_leaving_and_re_entering_active_reminds_once_per_cycle(self):
		deal = self.make_deal(**READY_FIELDS)
		first_cycle = self.cycle_of(deal.name).get(FIELD_ACTIVATION_CYCLE)
		self.make_overdue(deal.name)
		run_first_call_reminders()
		self.assertEqual(len(self.reminders_for(first_cycle)), 1)

		deal.reload()
		deal.status = "On Hold"
		deal.save(ignore_permissions=True)
		self.assertFalse(self.cycle_of(deal.name).get(FIELD_ACTIVATION_CYCLE))

		deal.reload()
		deal.status = STATUS_ACTIVE
		deal.save(ignore_permissions=True)
		second_cycle = self.cycle_of(deal.name).get(FIELD_ACTIVATION_CYCLE)
		self.assertTrue(second_cycle)
		self.assertNotEqual(second_cycle, first_cycle)

		self.make_overdue(deal.name)
		run_first_call_reminders()
		self.assertEqual(len(self.reminders_for(second_cycle)), 1)
		self.assertEqual(len(self.reminders_for(first_cycle)), 1, "the old cycle was reminded again")

	# -- ac-3: the rows the migration left alone -------------------------------------------

	def test_the_migration_neither_backfills_nor_reminds_a_pre_existing_active_deal(self):
		deal = self.make_deal(**READY_FIELDS)
		# A deal that was already Active before the migration: this app never observed its
		# activation, so its cycle fields are empty however long it has been sitting there.
		frappe.db.set_value(
			DEAL_DOCTYPE,
			deal.name,
			{FIELD_ACTIVATION_CYCLE: None, FIELD_ACTIVATION_STARTED_ON: None},
			update_modified=False,
		)

		install_reminder_fields()

		stored = self.cycle_of(deal.name)
		self.assertIsNone(stored.get(FIELD_ACTIVATION_CYCLE))
		self.assertIsNone(stored.get(FIELD_ACTIVATION_STARTED_ON))

		run_first_call_reminders()
		self.assertEqual(self.reminders_on(deal.name), [])

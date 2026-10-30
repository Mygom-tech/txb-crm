# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-270, TXB-289: Delivery Coach notifications on activation and later reassignment.

Pins the delivery flow on top of the TXB-269 foundation. Activating coaching records one event
per deal -- the coach's email with the client's details and the client's email, both promising
contact within 48 hours -- gives the coach a CRM Task due in 48 hours and tells the sales owner in
the CRM. A coach assigned to an already Active deal is emailed once; the client is not. Each
mode routes both channels correctly; the channels succeed, fail and retry independently and a
Sent channel is never resent; every channel is rechecked against the current assignment
generation, pipeline and Disabled stop. SMTP is mocked at its boundary.
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime, now_datetime

from crm.patches.v1_0.add_delivery_coach_notification_foundation import execute as migrate
from crm.patches.v1_0.coach_activation_notifications import execute as migrate_activation
from crm.txb.constants import (
	ADMIN_ROLE,
	FIELD_DELIVERY_COACH,
	FIELD_SALES_SOURCE_DEAL,
	PIPELINE_DELIVERING_COACHING,
	PIPELINE_INDIVIDUAL_SESSION,
)
from crm.txb.delivery_coach_assignment import (
	LEFT_PIPELINE,
	LOG_TITLE,
	NOT_ACTIVATION,
	REACH_OUT_HOURS,
	STOPPED,
	SUPERSEDED,
	process_assignment_notification,
	record_activation,
)
from crm.txb.delivery_coach_notifications import (
	LEDGER_DOCTYPE,
	MODE_DISABLED,
	MODE_LIVE,
	MODE_TEST_REDIRECT,
	SETTING_MODE,
	SETTING_TEST_USER,
	SETTINGS_DOCTYPE,
	STATUS_FAILED,
	STATUS_PENDING,
	STATUS_SENT,
	STATUS_SKIPPED,
)
from crm.txb.api.actions import run_action
from crm.txb.pipelines.actions import find_action

COACH_A = "txb-assign-coach-a@example.com"
COACH_B = "txb-assign-coach-b@example.com"
TESTER = "txb-assign-tester@example.com"
ASSIGNER = "txb-assign-admin@example.com"
SELLER = "txb-assign-seller@example.com"
CLIENT_EMAIL = "grace.client@example.com"
ORGANIZATION = "TxB Assignment Notify Co"
WORKER = "crm.txb.delivery_coach_assignment.process_assignment_notification"
# Everything the delivery-readiness gate (TXB-251) asks of a deal entering Active.
READY = {
	"custom_contract_signed": "Yes",
	"custom_payment_confirmed": "Yes",
	"custom_test_completed": "Yes",
	"custom_delivery_notes": "Ready",
}


class TestDeliveryCoachAssignmentNotifications(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		users = (
			(COACH_A, "Coach A"),
			(COACH_B, "Coach B"),
			(TESTER, "Vainius"),
			(ASSIGNER, "Ada"),
			(SELLER, "Sam"),
		)
		for email, first_name in users:
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": first_name,
						"send_welcome_email": 0,
					}
				).insert(ignore_permissions=True)
		# Only an Admin may set the Delivery Coach, and a new Delivering Coaching deal raises an
		# Admin task (TXB-208).
		frappe.get_doc("User", ASSIGNER).add_roles("Sales User", ADMIN_ROLE)
		frappe.get_doc("User", SELLER).add_roles("Sales User")
		if not frappe.db.exists("CRM Organization", ORGANIZATION):
			frappe.get_doc(
				{
					"doctype": "CRM Organization",
					"organization_name": ORGANIZATION,
					"custom_company_code": ORGANIZATION,
				}
			).insert(ignore_permissions=True)
		frappe.db.commit()  # nosemgrep -- the users must outlive per-test rollback

	def setUp(self):
		migrate()
		migrate_activation()
		self.set_mode(MODE_LIVE)

		self.enqueued = []
		self.sendmail = MagicMock()
		for patcher in (
			patch("frappe.enqueue", side_effect=self.fake_enqueue),
			patch("frappe.sendmail", self.sendmail),
			# The worker commits each channel; keep every test inside its rollback.
			patch.object(frappe.db, "commit"),
		):
			patcher.start()
			self.addCleanup(patcher.stop)
		frappe.set_user(ASSIGNER)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	# -- helpers ---------------------------------------------------------------------------

	def fake_enqueue(self, method, **kwargs):
		if method == WORKER:
			self.enqueued.append(kwargs)

	def set_mode(self, mode, test_user=None):
		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_TEST_USER, test_user)
		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_MODE, mode)

	def make_contact(self, email=CLIENT_EMAIL):
		contact = frappe.new_doc("Contact")
		contact.first_name, contact.last_name = "Grace", "Client"
		if email:
			contact.append("email_ids", {"email_id": email, "is_primary": 1})
		contact.append("phone_nos", {"phone": "+37060000001", "is_primary_mobile_no": 1})
		return contact.insert(ignore_permissions=True).name

	def make_deal(self, status="Contract Cleared", email=CLIENT_EMAIL, **fields):
		return frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"pipeline_type": PIPELINE_DELIVERING_COACHING,
				"status": status,
				"first_name": "Grace",
				"last_name": "Client",
				"organization": ORGANIZATION,
				"custom_program_type": "Lyderystė",
				"contacts": [{"contact": self.make_contact(email), "is_primary": 1}],
				**fields,
			}
		).insert(ignore_permissions=True)

	def activated(self, coach=COACH_A, **fields):
		"""A deal whose coaching was just activated, and its one activation event."""
		deal = self.make_deal(status="Active", **{FIELD_DELIVERY_COACH: coach, **fields})
		record_activation(deal)
		return deal, f"{deal.name}-activation"

	def assign(self, deal, coach):
		deal.reload()
		deal.set(FIELD_DELIVERY_COACH, coach)
		deal.save(ignore_permissions=True)
		return deal

	def events(self, deal):
		return frappe.get_all(
			LEDGER_DOCTYPE, filters={"deal": deal.name}, pluck="name", order_by="assigned_at asc"
		)

	def event(self, name):
		return frappe.get_doc(LEDGER_DOCTYPE, name)

	def run_worker(self, event_key):
		process_assignment_notification(event_key)
		return self.event(event_key)

	def statuses(self, event):
		return event.email_status, event.client_email_status

	def email_recipients(self):
		return [c.kwargs["recipients"] for c in self.sendmail.call_args_list]

	def error_logs(self):
		return frappe.get_all("Error Log", filters={"method": LOG_TITLE}, pluck="error")

	# -- activation ------------------------------------------------------------------------

	def test_activation_records_one_event_queued_after_commit(self):
		_deal, name = self.activated()

		event = self.event(name)
		self.assertEqual((event.intended_user, event.effective_user), (COACH_A, COACH_A))
		self.assertEqual((event.assigned_by, event.mode), (ASSIGNER, MODE_LIVE))
		self.assertEqual(self.statuses(event), (STATUS_PENDING, STATUS_PENDING))

		[job] = self.enqueued
		self.assertTrue(job["enqueue_after_commit"])
		self.assertTrue(job["deduplicate"])
		self.assertEqual(job["event_key"], name)
		# Nothing is delivered inside the Deal's transaction.
		self.sendmail.assert_not_called()

	def test_activation_gives_the_coach_a_reach_out_task_due_in_48_hours(self):
		deal, _name = self.activated()

		[task] = frappe.get_all(
			"CRM Task",
			filters={"reference_docname": deal.name, "title": ["like", "Reach out to the client%"]},
			fields=["assigned_to", "priority", "due_date"],
		)
		self.assertEqual((task.assigned_to, task.priority), (COACH_A, "High"))
		hours = (get_datetime(task.due_date) - now_datetime()).total_seconds() / 3600
		self.assertAlmostEqual(hours, REACH_OUT_HOURS, delta=0.1)

	def test_activation_tells_the_sales_owner_in_the_crm(self):
		sales = frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"pipeline_type": PIPELINE_INDIVIDUAL_SESSION,
				"status": "Submitted",
				"deal_owner": SELLER,
			}
		).insert(ignore_permissions=True)
		deal, _name = self.activated(**{FIELD_SALES_SOURCE_DEAL: sales.name})

		[note] = frappe.get_all(
			"CRM Notification",
			filters={"to_user": SELLER, "reference_name": deal.name},
			fields=["type", "message"],
		)
		self.assertEqual(note.type, "Assignment")
		self.assertIn("Grace Client", note.message)
		self.assertIn("Coach A", note.message)

	def test_activation_happens_once_per_deal(self):
		deal, name = self.activated()
		record_activation(deal)

		self.assertEqual(self.events(deal), [name])
		self.assertEqual(len(self.enqueued), 1)
		tasks = frappe.get_all("CRM Task", filters={"reference_docname": deal.name, "assigned_to": COACH_A})
		self.assertEqual(len(tasks), 1)

	def ready_deal(self):
		return self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A, **READY})

	def test_set_first_call_date_activates_coaching(self):
		deal = self.ready_deal()
		run_action(deal, find_action(PIPELINE_DELIVERING_COACHING, "set_first_call_date"), {"first_call_date": "2031-03-10 10:00:00"})

		self.assertEqual(self.events(deal), [f"{deal.name}-activation"])

	def test_a_direct_status_change_to_active_activates_coaching(self):
		# The status control and Kanban write the status bare; an Admin may (TXB-110).
		deal = self.ready_deal()
		deal.status = "Active"
		deal.save()

		self.assertEqual(self.events(deal), [f"{deal.name}-activation"])
		self.assertEqual(len(self.enqueued), 1)

	def test_reactivating_a_deal_activated_before_the_ledger_sends_nothing(self):
		# Activated by Kanban before activation was recorded: no ledger row to dedupe on.
		deal = self.make_deal(status="Active", **{FIELD_DELIVERY_COACH: COACH_A, **READY})
		for status in ("On Hold", "Active"):
			deal.status = status
			deal.save()

		self.assertEqual(self.events(deal), [])
		self.assertEqual(self.enqueued, [])

	def test_reactivation_does_not_notify_again(self):
		deal = self.ready_deal()
		for status in ("Active", "On Hold", "Active"):
			deal.status = status
			deal.save()

		self.assertEqual(self.events(deal), [f"{deal.name}-activation"])
		self.assertEqual(len(self.enqueued), 1)

	def test_activation_without_a_coach_does_nothing(self):
		deal = self.make_deal()
		record_activation(deal)
		self.assertEqual(self.events(deal), [])

	def test_activation_failure_never_blocks_the_activation(self):
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})
		with patch(
			"crm.txb.delivery_coach_assignment.get_notification_config",
			side_effect=RuntimeError("settings unavailable"),
		):
			record_activation(deal)

		self.assertEqual(self.events(deal), [])
		self.assertEqual(
			frappe.get_all("CRM Task", filters={"reference_docname": deal.name, "assigned_to": COACH_A}), []
		)
		self.assertTrue(self.error_logs())

	# -- coach changes after activation ----------------------------------------------------

	def test_reassigning_an_active_deal_emails_only_the_new_coach(self):
		deal = self.make_deal(status="Active", **{FIELD_DELIVERY_COACH: COACH_A})
		self.assertEqual(self.events(deal), [])

		self.assign(deal, COACH_B)
		[name] = self.events(deal)
		event = self.run_worker(name)

		self.assertEqual(event.intended_user, COACH_B)
		self.assertEqual(self.statuses(event), (STATUS_SENT, STATUS_SKIPPED))
		self.assertEqual(event.client_email_error, NOT_ACTIVATION)
		self.assertEqual(self.email_recipients(), [[COACH_B]])

	def test_a_coach_change_before_activation_sends_nothing(self):
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})
		self.assign(deal, COACH_B)
		self.assertEqual(self.events(deal), [])
		self.assertEqual(self.enqueued, [])

	def test_unchanged_cleared_and_self_assignments_create_no_event(self):
		deal = self.make_deal(status="Active", **{FIELD_DELIVERY_COACH: COACH_A})
		deal.reload()
		deal.last_name = "Renamed"
		deal.save(ignore_permissions=True)
		self.assign(deal, COACH_A)
		self.assign(deal, None)
		self.assign(deal, ASSIGNER)

		self.assertEqual(self.events(deal), [])

	def test_other_pipelines_and_imports_create_no_event(self):
		other = self.make_deal(
			status="Submitted", pipeline_type=PIPELINE_INDIVIDUAL_SESSION, **{FIELD_DELIVERY_COACH: COACH_A}
		)
		self.assign(other, COACH_B)
		deal = self.make_deal(status="Active", **{FIELD_DELIVERY_COACH: COACH_A})
		frappe.flags.in_import = True
		try:
			self.assign(deal, COACH_B)
		finally:
			frappe.flags.in_import = False

		self.assertEqual(self.events(other) + self.events(deal), [])

	def test_queue_failure_never_blocks_the_deal_save(self):
		deal = self.make_deal(status="Active", **{FIELD_DELIVERY_COACH: COACH_A})
		with patch(
			"crm.txb.delivery_coach_assignment.get_notification_config",
			side_effect=RuntimeError("settings unavailable"),
		):
			self.assign(deal, COACH_B)

		self.assertEqual(frappe.db.get_value("CRM Deal", deal.name, FIELD_DELIVERY_COACH), COACH_B)
		self.assertTrue(self.error_logs())

	# -- delivery --------------------------------------------------------------------------

	def test_live_emails_the_coach_the_client_details_and_the_client_in_lithuanian(self):
		_deal, name = self.activated()

		event = self.run_worker(name)
		self.assertEqual(self.statuses(event), (STATUS_SENT, STATUS_SENT))
		coach_mail, client_mail = (c.kwargs for c in self.sendmail.call_args_list)

		self.assertEqual(coach_mail["recipients"], [COACH_A])
		self.assertEqual(coach_mail["subject"], "New client assigned: Grace Client")
		for detail in ("Grace Client", ORGANIZATION, CLIENT_EMAIL, "+37060000001", "Lyderystė", "48 hours"):
			self.assertIn(detail, coach_mail["message"])

		self.assertEqual(client_mail["recipients"], [CLIENT_EMAIL])
		self.assertEqual(client_mail["subject"], "Jums paskirtas koučeris")
		for detail in ("Sveiki, Grace", "Coach A", "48 valandas", "Lyderystė"):
			self.assertIn(detail, client_mail["message"])

	def test_test_redirect_sends_both_emails_to_the_test_user(self):
		self.set_mode(MODE_TEST_REDIRECT, TESTER)
		_deal, name = self.activated()

		event = self.run_worker(name)
		self.assertEqual(event.effective_user, TESTER)
		self.assertEqual(self.email_recipients(), [[TESTER], [TESTER]])
		subjects = [c.kwargs["subject"] for c in self.sendmail.call_args_list]
		self.assertEqual(
			subjects,
			[
				"[TEST for Coach A] New client assigned: Grace Client",
				"[TEST for Grace Client] Jums paskirtas koučeris",
			],
		)

	def test_disabled_sends_nothing_but_still_gives_the_coach_the_task(self):
		self.set_mode(MODE_DISABLED)
		deal, name = self.activated()

		self.assertEqual(self.enqueued, [])
		event = self.run_worker(name)
		self.assertEqual(self.statuses(event), (STATUS_SKIPPED, STATUS_SKIPPED))
		self.sendmail.assert_not_called()
		self.assertTrue(
			frappe.get_all("CRM Task", filters={"reference_docname": deal.name, "assigned_to": COACH_A})
		)

	def test_a_client_without_a_primary_email_fails_only_the_client_email(self):
		_deal, name = self.activated(email=None)

		event = self.run_worker(name)
		self.assertEqual(self.statuses(event), (STATUS_SENT, STATUS_FAILED))
		self.assertIn("client_has_no_primary_email", event.client_email_error)
		self.assertEqual(self.email_recipients(), [[COACH_A]])
		self.assertTrue(self.error_logs())

	def test_retry_only_resends_the_failed_channel(self):
		_deal, name = self.activated()
		self.sendmail.side_effect = [None, RuntimeError("smtp down")]

		event = self.run_worker(name)
		self.assertEqual(self.statuses(event), (STATUS_SENT, STATUS_FAILED))

		self.sendmail.side_effect = None
		event = self.run_worker(name)
		self.assertEqual(self.statuses(event), (STATUS_SENT, STATUS_SENT))
		self.assertEqual(self.email_recipients(), [[COACH_A], [CLIENT_EMAIL], [CLIENT_EMAIL]])

		event = self.run_worker(name)
		self.assertEqual((event.email_attempts, event.client_email_attempts), (1, 2))
		self.assertEqual(self.sendmail.call_count, 3)

	def test_a_superseded_coach_is_skipped(self):
		deal, name = self.activated()
		self.assign(deal, COACH_B)

		stale = self.run_worker(name)
		self.assertEqual(self.statuses(stale), (STATUS_SKIPPED, STATUS_SKIPPED))
		self.assertEqual((stale.email_error, stale.client_email_error), (SUPERSEDED, SUPERSEDED))
		self.assertEqual(self.email_recipients(), [])

		[current] = [e for e in self.events(deal) if e != name]
		self.run_worker(current)
		self.assertEqual(self.email_recipients(), [[COACH_B]])

	def test_reassignment_between_channels_skips_the_unsent_channel(self):
		deal, name = self.activated()

		def reassign_once(**kwargs):
			self.sendmail.side_effect = None
			self.assign(deal, COACH_B)

		self.sendmail.side_effect = reassign_once
		event = self.run_worker(name)

		self.assertEqual(self.statuses(event), (STATUS_SENT, STATUS_SKIPPED))
		self.assertEqual(event.client_email_error, SUPERSEDED)
		self.assertEqual(self.email_recipients(), [[COACH_A]])

	def test_leaving_delivering_coaching_skips_unsent_channels(self):
		deal, name = self.activated()
		frappe.db.set_value("CRM Deal", deal.name, "pipeline_type", PIPELINE_INDIVIDUAL_SESSION)

		event = self.run_worker(name)
		self.assertEqual(self.statuses(event), (STATUS_SKIPPED, STATUS_SKIPPED))
		self.assertEqual((event.email_error, event.client_email_error), (LEFT_PIPELINE, LEFT_PIPELINE))
		self.sendmail.assert_not_called()

	def test_current_disabled_stops_a_queued_event(self):
		_deal, name = self.activated()
		self.set_mode(MODE_DISABLED)

		event = self.run_worker(name)
		self.assertEqual(self.statuses(event), (STATUS_SKIPPED, STATUS_SKIPPED))
		self.assertEqual((event.email_error, event.client_email_error), (STOPPED, STOPPED))
		self.sendmail.assert_not_called()

	def test_queued_events_keep_their_captured_routing(self):
		self.set_mode(MODE_TEST_REDIRECT, TESTER)
		_deal, redirected = self.activated()
		self.set_mode(MODE_LIVE)
		_deal, live = self.activated(coach=COACH_B)

		self.set_mode(MODE_TEST_REDIRECT, TESTER)
		self.run_worker(live)
		self.set_mode(MODE_LIVE)
		self.run_worker(redirected)

		self.assertEqual(self.email_recipients(), [[COACH_B], [CLIENT_EMAIL], [TESTER], [TESTER]])

	def test_deleted_deal_is_skipped(self):
		deal, name = self.activated()
		frappe.db.delete("CRM Task", {"reference_docname": deal.name})
		frappe.delete_doc("CRM Deal", deal.name, ignore_permissions=True, force=True)

		event = self.run_worker(name)
		self.assertEqual(self.statuses(event), (STATUS_SKIPPED, STATUS_SKIPPED))
		self.sendmail.assert_not_called()

	def test_a_channel_failure_never_stops_the_other(self):
		_deal, name = self.activated()
		broken = MagicMock(side_effect=RuntimeError("template broken"))
		with patch.dict("crm.txb.delivery_coach_assignment.SENDERS", {"email": broken}):
			event = self.run_worker(name)

		self.assertEqual(self.statuses(event), (STATUS_FAILED, STATUS_SENT))
		self.assertEqual(self.email_recipients(), [[CLIENT_EMAIL]])

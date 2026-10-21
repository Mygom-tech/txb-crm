# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-290: recovery of stranded Delivery Coach notification jobs and the operator retry.

Pins the scheduled sweep (only stale Pending channels, once under concurrent sweeps, bounded)
and the System Manager retry of one definite Failed channel (every other state and every other
user refused without a provider call), both through the hardened worker with the captured
routing and generation. SMTP and the queue are mocked at their boundaries.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from crm.patches.v1_0.add_delivery_coach_notification_foundation import execute as migrate
from crm.txb import delivery_coach_notification_recovery as recovery
from crm.txb.constants import ADMIN_ROLE, FIELD_DELIVERY_COACH, PIPELINE_DELIVERING_COACHING
from crm.txb.delivery_coach_assignment import (
	SUPERSEDED,
	process_assignment_notification,
	record_activation,
)
from crm.txb.delivery_coach_notification_recovery import (
	STALE_AFTER_MINUTES,
	get_assignment_notification_status,
	retry_assignment_notification_channel,
	sweep_stale_assignment_notifications,
)
from crm.txb.delivery_coach_notifications import (
	LEDGER_DOCTYPE,
	MODE_LIVE,
	MODE_TEST_REDIRECT,
	SETTING_MODE,
	SETTING_TEST_USER,
	SETTINGS_DOCTYPE,
	STATUS_FAILED,
	STATUS_PENDING,
	STATUS_SENT,
	STATUS_SKIPPED,
	STATUS_UNCERTAIN,
)

COACH_A = "txb-recover-coach-a@example.com"
COACH_B = "txb-recover-coach-b@example.com"
TESTER = "txb-recover-tester@example.com"
ASSIGNER = "txb-recover-admin@example.com"
OPERATOR = "txb-recover-operator@example.com"
CLIENT_EMAIL = "txb-recover-client@example.com"
WORKER = "crm.txb.delivery_coach_assignment.process_assignment_notification"


class WorkerCrash(BaseException):
	"""The worker process dying mid-run: nothing in the worker catches it."""


class TestDeliveryCoachNotificationRecovery(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		users = (
			(COACH_A, "Coach A"),
			(COACH_B, "Coach B"),
			(TESTER, "Tester"),
			(ASSIGNER, "Ada"),
			(OPERATOR, "Opal"),
		)
		for email, first_name in users:
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{"doctype": "User", "email": email, "first_name": first_name, "send_welcome_email": 0}
				).insert(ignore_permissions=True)
		# Only an Admin may set the Delivery Coach (TXB-208); an Admin is not a System Manager.
		frappe.get_doc("User", ASSIGNER).add_roles("Sales User", ADMIN_ROLE)
		frappe.get_doc("User", OPERATOR).add_roles("System Manager")
		frappe.db.commit()  # nosemgrep -- the users must outlive per-test rollback

	def setUp(self):
		migrate()
		self.set_mode(MODE_LIVE)

		self.enqueued = []
		self.enqueue_error = None
		self.sendmail = MagicMock()
		for patcher in (
			patch("frappe.enqueue", side_effect=self.fake_enqueue),
			patch("frappe.sendmail", self.sendmail),
			# The sweep and worker commit; keep every test inside its rollback.
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
		if self.enqueue_error:
			error, self.enqueue_error = self.enqueue_error, None
			raise error
		if method == WORKER:
			self.enqueued.append(kwargs)

	def set_mode(self, mode, test_user=None):
		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_TEST_USER, test_user)
		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_MODE, mode)

	def record_assignment(self, coach=COACH_A):
		"""A Deal whose coaching was activated with `coach`, and its one ledger row."""
		contact = frappe.new_doc("Contact")
		contact.first_name, contact.last_name = "Grace", "Client"
		contact.append("email_ids", {"email_id": CLIENT_EMAIL, "is_primary": 1})
		contact.insert(ignore_permissions=True)
		deal = frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"pipeline_type": PIPELINE_DELIVERING_COACHING,
				"status": "Active",
				"first_name": "Grace",
				"last_name": "Client",
				"contacts": [{"contact": contact.name, "is_primary": 1}],
				FIELD_DELIVERY_COACH: coach,
			}
		).insert(ignore_permissions=True)
		record_activation(deal)
		[name] = frappe.get_all(LEDGER_DOCTYPE, filters={"deal": deal.name}, pluck="name")
		return deal, name

	def event(self, name):
		return frappe.get_doc(LEDGER_DOCTYPE, name)

	def set_channels(self, name, **values):
		frappe.db.set_value(LEDGER_DOCTYPE, name, values, update_modified=False)

	@contextmanager
	def later(self, minutes=STALE_AFTER_MINUTES + 1):
		"""The recovery module's clock, `minutes` from now."""
		later = add_to_date(now_datetime(), minutes=minutes)
		with patch.object(recovery, "now_datetime", return_value=later):
			yield

	def sweep(self, name):
		"""Run the sweep; the (event, channel) pairs it recovered for `name`."""
		return sorted(pair for pair in sweep_stale_assignment_notifications() if pair[0] == name)

	def recipients(self):
		return [c.kwargs["recipients"] for c in self.sendmail.call_args_list]

	def jobs(self, name):
		return [job for job in self.enqueued if job["event_key"] == name]

	def run_jobs(self, name):
		jobs = self.jobs(name)
		self.enqueued = [job for job in self.enqueued if job not in jobs]
		for job in jobs:
			process_assignment_notification(job["event_key"], job["channel"])
		return self.event(name)

	def as_user(self, user, fn, *args):
		frappe.set_user(user)
		try:
			return fn(*args)
		finally:
			frappe.set_user(ASSIGNER)

	# -- sweep -----------------------------------------------------------------------------

	def test_lost_enqueue_is_recovered_once_after_it_goes_stale(self):
		self.enqueue_error = ConnectionError("redis down")
		_deal, name = self.record_assignment()
		self.assertEqual(self.jobs(name), [])

		# Fresh Pending work is left for its own job.
		self.assertEqual(self.sweep(name), [])
		with self.later():
			self.assertEqual(self.sweep(name), [(name, "client_email"), (name, "email")])

		jobs = self.jobs(name)
		self.assertEqual(sorted(job["channel"] for job in jobs), ["client_email", "email"])
		self.assertEqual(
			sorted(job["job_id"] for job in jobs),
			[f"delivery-coach-assignment-{name}-email", f"delivery-coach-assignment-{name}-client_email"],
		)
		self.assertTrue(all(job["deduplicate"] and job["enqueue_after_commit"] for job in jobs))
		# The sweep itself contacts no provider.
		self.sendmail.assert_not_called()

		event = self.run_jobs(name)
		self.assertEqual((event.email_status, event.client_email_status), (STATUS_SENT, STATUS_SENT))
		self.assertEqual((event.email_attempts, event.client_email_attempts), (1, 1))
		self.assertTrue(event.email_recovered_at and event.client_email_recovered_at)
		self.assertEqual([c.kwargs["recipients"] for c in self.sendmail.call_args_list], [[COACH_A]])

	def test_worker_crash_before_channel_claim_is_recovered(self):
		_deal, name = self.record_assignment()
		frappe.db.savepoint("before_crash")
		with patch("crm.txb.delivery_coach_assignment._skip_reason", side_effect=WorkerCrash):
			with self.assertRaises(WorkerCrash):
				self.run_jobs(name)
		frappe.db.rollback(save_point="before_crash")

		event = self.event(name)
		self.assertEqual(
			(event.email_status, event.email_attempts, event.email_claimed_at), (STATUS_PENDING, 0, None)
		)
		with self.later():
			self.assertEqual(self.sweep(name), [(name, "client_email"), (name, "email")])
		event = self.run_jobs(name)
		self.assertEqual((event.email_status, event.client_email_status), (STATUS_SENT, STATUS_SENT))
		self.assertEqual(self.recipients(), [[CLIENT_EMAIL], [COACH_A]])

	def test_worker_crash_after_claim_is_recovered_once_the_claim_goes_stale(self):
		_deal, name = self.record_assignment()
		self.enqueued.clear()
		# The worker claimed email five minutes in and died before recording an outcome.
		self.set_channels(name, email_claimed_at=add_to_date(now_datetime(), minutes=5), email_attempts=1)

		# A claim within the window may still be running; the unclaimed channel is recovered.
		with self.later():
			self.assertEqual(self.sweep(name), [(name, "client_email")])
		with self.later(minutes=STALE_AFTER_MINUTES * 2):
			self.assertEqual(self.sweep(name), [(name, "email")])
		self.assertEqual(sorted(job["channel"] for job in self.jobs(name)), ["client_email", "email"])

	def test_concurrent_sweeps_queue_each_stale_channel_once(self):
		_deal, name = self.record_assignment()
		self.enqueued.clear()

		with self.later():
			cutoff = add_to_date(recovery.now_datetime(), minutes=-STALE_AFTER_MINUTES)
			# Sweep A reads its batch, sweep B recovers it first, then A takes the row locks.
			seen_by_a = [pair for pair in recovery._stale_channels(cutoff) if pair[0] == name]
			by_b = self.sweep(name)
			by_a = [pair for pair in seen_by_a if recovery._recover(*pair, cutoff)]
			again = self.sweep(name)

		self.assertEqual(seen_by_a, [(name, "client_email"), (name, "email")])
		self.assertEqual(by_b, [(name, "client_email"), (name, "email")])
		self.assertEqual((by_a, again), ([], []))
		self.assertEqual(len(self.jobs(name)), 2)

		event = self.run_jobs(name)
		self.assertEqual((event.email_status, event.client_email_status), (STATUS_SENT, STATUS_SENT))
		self.assertEqual(self.sendmail.call_count, 2)

	def test_sweep_leaves_sent_skipped_failed_uncertain_and_fresh_channels_untouched(self):
		_d1, settled = self.record_assignment()
		_d2, held = self.record_assignment(COACH_B)
		_d3, in_flight = self.record_assignment()
		self.enqueued.clear()
		self.set_channels(settled, email_status=STATUS_SENT, client_email_status=STATUS_SKIPPED)
		self.set_channels(held, email_status=STATUS_FAILED, client_email_status=STATUS_UNCERTAIN)
		future = add_to_date(now_datetime(), minutes=STALE_AFTER_MINUTES + 1)
		self.set_channels(in_flight, email_claimed_at=future, client_email_recovered_at=future)

		with self.later():
			for name in (settled, held, in_flight):
				self.assertEqual(self.sweep(name), [])
		for name in (settled, held, in_flight):
			self.assertEqual(self.jobs(name), [])
		for name in (settled, held):
			event = self.event(name)
			self.assertEqual((event.email_recovered_at, event.client_email_recovered_at), (None, None))
		self.sendmail.assert_not_called()

	def test_sweep_queues_only_the_stale_channel_and_never_resends_a_sent_one(self):
		_deal, name = self.record_assignment()
		self.enqueued.clear()
		self.set_channels(name, email_status=STATUS_SENT)

		with self.later():
			self.assertEqual(self.sweep(name), [(name, "client_email")])
		[job] = self.jobs(name)
		self.assertEqual(job["channel"], "client_email")
		event = self.run_jobs(name)
		self.assertEqual((event.email_status, event.client_email_status), (STATUS_SENT, STATUS_SENT))
		self.assertEqual(self.recipients(), [[CLIENT_EMAIL]])

		# A rerun of the same job delivers nothing more.
		process_assignment_notification(name, "client_email")
		process_assignment_notification(name)
		self.assertEqual(self.recipients(), [[CLIENT_EMAIL]])

	def test_sweep_processes_a_bounded_batch(self):
		self.record_assignment()
		self.record_assignment(COACH_B)
		with patch.object(recovery, "SWEEP_BATCH_SIZE", 1), self.later():
			self.assertEqual(len(sweep_stale_assignment_notifications()), 1)

	def test_sweep_leaves_the_channel_stale_when_the_queue_is_down(self):
		_deal, name = self.record_assignment()
		self.enqueued.clear()
		with self.later():
			# The client email's job could not be queued: its stamp is undone and the next sweep
			# retries it.
			self.enqueue_error = ConnectionError("redis down")
			self.assertEqual(self.sweep(name), [(name, "email")])
			self.assertIsNone(self.event(name).client_email_recovered_at)
			self.assertEqual(self.sweep(name), [(name, "client_email")])
			self.assertEqual(self.sweep(name), [])
		self.assertEqual(sorted(job["channel"] for job in self.jobs(name)), ["client_email", "email"])

	# -- retry -----------------------------------------------------------------------------

	def test_system_manager_retries_one_failed_channel(self):
		_deal, name = self.record_assignment()
		self.sendmail.side_effect = Exception("smtp down")
		event = self.run_jobs(name)
		self.assertEqual((event.email_status, event.client_email_status), (STATUS_FAILED, STATUS_FAILED))
		self.sendmail.reset_mock(side_effect=True)

		result = self.as_user(OPERATOR, retry_assignment_notification_channel, name, "email")
		self.assertEqual(result["channels"]["email"]["status"], STATUS_PENDING)
		self.assertEqual(result["channels"]["client_email"]["status"], STATUS_FAILED)
		[job] = self.jobs(name)
		self.assertEqual(job["channel"], "email")
		comment = {"reference_doctype": LEDGER_DOCTYPE, "reference_name": name, "comment_type": "Info"}
		self.assertTrue(frappe.db.exists("Comment", comment))

		event = self.run_jobs(name)
		self.assertEqual((event.email_status, event.client_email_status), (STATUS_SENT, STATUS_FAILED))
		self.assertEqual(event.email_attempts, 2)
		# The other Failed channel is not retried along with it.
		self.assertEqual(self.recipients(), [[COACH_A]])

	def test_administrator_can_retry_and_a_second_retry_is_refused(self):
		_deal, name = self.record_assignment()
		self.enqueued.clear()
		self.set_channels(name, client_email_status=STATUS_FAILED)

		self.as_user("Administrator", retry_assignment_notification_channel, name, "client_email")
		with self.assertRaises(frappe.ValidationError):
			self.as_user("Administrator", retry_assignment_notification_channel, name, "client_email")
		self.assertEqual(len(self.jobs(name)), 1)

	def test_retry_keeps_the_captured_routing_and_generation(self):
		self.set_mode(MODE_TEST_REDIRECT, TESTER)
		_deal, name = self.record_assignment()
		self.sendmail.side_effect = Exception("smtp down")
		self.run_jobs(name)
		before = self.event(name)
		self.sendmail.reset_mock(side_effect=True)

		# The operator switching to Live does not redirect a captured Test redirect event.
		self.set_mode(MODE_LIVE)
		self.as_user(OPERATOR, retry_assignment_notification_channel, name, "email")
		event = self.run_jobs(name)

		self.assertEqual(event.email_status, STATUS_SENT)
		self.assertEqual([c.kwargs["recipients"] for c in self.sendmail.call_args_list], [[TESTER]])
		for field in ("mode", "intended_user", "effective_user", "assignment_generation", "assigned_at"):
			self.assertEqual(event.get(field), before.get(field), field)
		self.assertEqual(
			(event.mode, event.intended_user, event.effective_user), (MODE_TEST_REDIRECT, COACH_A, TESTER)
		)

	def test_retry_of_a_superseded_assignment_is_skipped_by_the_worker(self):
		deal, name = self.record_assignment()
		self.enqueued.clear()
		self.set_channels(name, email_status=STATUS_FAILED)
		deal.reload()
		deal.set(FIELD_DELIVERY_COACH, COACH_B)
		deal.save(ignore_permissions=True)

		self.as_user(OPERATOR, retry_assignment_notification_channel, name, "email")
		event = self.run_jobs(name)
		self.assertEqual((event.email_status, event.email_error), (STATUS_SKIPPED, SUPERSEDED))
		self.sendmail.assert_not_called()

	def test_retry_refuses_every_state_but_failed_without_contacting_a_provider(self):
		_deal, name = self.record_assignment()
		self.enqueued.clear()
		for status in (STATUS_SENT, STATUS_SKIPPED, STATUS_PENDING, STATUS_UNCERTAIN):
			self.set_channels(name, email_status=status, client_email_status=status)
			for channel in ("email", "client_email"):
				with self.assertRaises(frappe.ValidationError):
					self.as_user(OPERATOR, retry_assignment_notification_channel, name, channel)
			event = self.event(name)
			self.assertEqual((event.email_status, event.client_email_status), (status, status))
		with self.assertRaises(frappe.ValidationError):
			self.as_user(OPERATOR, retry_assignment_notification_channel, name, "sms")

		self.assertEqual(self.jobs(name), [])
		self.sendmail.assert_not_called()

	def test_retry_and_status_refuse_users_who_are_not_system_managers(self):
		_deal, name = self.record_assignment()
		self.enqueued.clear()
		self.set_channels(name, email_status=STATUS_FAILED)

		for user in (ASSIGNER, COACH_A):
			with self.assertRaises(frappe.PermissionError):
				self.as_user(user, retry_assignment_notification_channel, name, "email")
			with self.assertRaises(frappe.PermissionError):
				self.as_user(user, get_assignment_notification_status, name)
		self.assertEqual(self.event(name).email_status, STATUS_FAILED)
		self.assertEqual(self.jobs(name), [])
		self.sendmail.assert_not_called()

	def test_status_names_the_deal_users_and_channel_without_secrets(self):
		deal, name = self.record_assignment()
		self.sendmail.side_effect = [Exception("auth rejected: Authorization: Bearer abc123"), None]
		self.run_jobs(name)

		status = self.as_user(OPERATOR, get_assignment_notification_status, name)
		self.assertEqual(
			(status["deal"], status["intended_user"], status["effective_user"], status["mode"]),
			(deal.name, COACH_A, COACH_A, MODE_LIVE),
		)
		email = status["channels"]["email"]
		self.assertEqual((email["status"], email["attempts"], email["can_retry"]), (STATUS_FAILED, 1, True))
		self.assertIn("[redacted]", email["error"])
		self.assertEqual(status["channels"]["client_email"]["can_retry"], False)
		self.assertNotIn("abc123", frappe.as_json(status))

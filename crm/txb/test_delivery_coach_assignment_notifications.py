# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-270: Delivery Coach assignment email and Slack DM.

Pins the delivery flow on top of the TXB-269 foundation: only a real assignment of a Delivering
Coaching deal creates one event, queued after commit; each mode routes both channels correctly;
email and Slack succeed, fail and retry independently and a Sent channel is never resent; the
Slack DM goes through lookupByEmail, conversations.open and chat.postMessage. SMTP and the
Slack Web API are mocked at their boundaries.
"""

from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase
from frappe.utils import format_datetime, get_url

from crm.patches.v1_0.add_delivery_coach_notification_foundation import execute as migrate
from crm.txb.constants import (
	ADMIN_ROLE,
	FIELD_DELIVERY_COACH,
	PIPELINE_DELIVERING_COACHING,
	PIPELINE_INDIVIDUAL_SESSION,
)
from crm.txb.delivery_coach_assignment import (
	LOG_TITLE,
	SUPERSEDED,
	build_slack_message,
	process_assignment_notification,
)
from crm.txb.delivery_coach_notifications import (
	LEDGER_DOCTYPE,
	MODE_DISABLED,
	MODE_LIVE,
	MODE_TEST_REDIRECT,
	SETTING_MODE,
	SETTING_SLACK_BOT_TOKEN,
	SETTING_TEST_USER,
	SETTINGS_DOCTYPE,
	STATUS_FAILED,
	STATUS_PENDING,
	STATUS_SENT,
	STATUS_SKIPPED,
)

COACH_A = "txb-assign-coach-a@example.com"
COACH_B = "txb-assign-coach-b@example.com"
TESTER = "txb-assign-tester@example.com"
ASSIGNER = "txb-assign-admin@example.com"
ORGANIZATION = "TxB Assignment Notify Co"
WORKER = "crm.txb.delivery_coach_assignment.process_assignment_notification"
# Built at runtime so GitHub push protection does not flag a fake Slack token.
TOKEN = "-".join(("xoxb", "123456789012", "abcdefghijklmnopQRSTUVWX"))


def slack_response(payload):
	response = MagicMock()
	response.json.return_value = payload
	return response


class TestDeliveryCoachAssignmentNotifications(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		users = ((COACH_A, "Coach A"), (COACH_B, "Coach B"), (TESTER, "Vainius"), (ASSIGNER, "Ada"))
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
		self.set_mode(MODE_LIVE)
		settings = frappe.get_single(SETTINGS_DOCTYPE)
		settings.set(SETTING_SLACK_BOT_TOKEN, TOKEN)
		settings.save(ignore_permissions=True)

		self.enqueued = []
		self.sendmail = MagicMock()
		self.slack = {
			"users.lookupByEmail": {"ok": True, "user": {"id": "U123"}},
			"conversations.open": {"ok": True, "channel": {"id": "D123"}},
			"chat.postMessage": {"ok": True},
		}
		self.slack_post = MagicMock(side_effect=self.fake_slack_post)
		for patcher in (
			patch("frappe.enqueue", side_effect=self.fake_enqueue),
			patch("frappe.sendmail", self.sendmail),
			patch("crm.txb.slack.requests.post", self.slack_post),
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

	def fake_slack_post(self, url, headers=None, data=None, timeout=None):
		return slack_response(self.slack[url.rsplit("/", 1)[-1]])

	def set_mode(self, mode, test_user=None):
		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_TEST_USER, test_user)
		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_MODE, mode)

	def make_deal(self, **fields):
		return frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"pipeline_type": PIPELINE_DELIVERING_COACHING,
				"status": "Submitted",
				"first_name": "Grace",
				"last_name": "Client",
				"organization": ORGANIZATION,
				**fields,
			}
		).insert(ignore_permissions=True)

	def assign(self, deal, coach):
		deal.reload()
		deal.set(FIELD_DELIVERY_COACH, coach)
		deal.save(ignore_permissions=True)
		return deal

	def events(self, deal):
		return frappe.get_all(
			LEDGER_DOCTYPE, filters={"deal": deal.name}, pluck="name", order_by="creation asc"
		)

	def event(self, name):
		return frappe.get_doc(LEDGER_DOCTYPE, name)

	def run_worker(self, event_key):
		process_assignment_notification(event_key)
		return self.event(event_key)

	def slack_calls(self):
		return [
			(c.args[0].rsplit("/", 1)[-1], c.kwargs["data"], c.kwargs["headers"])
			for c in self.slack_post.call_args_list
		]

	def email_recipients(self):
		return [c.kwargs["recipients"] for c in self.sendmail.call_args_list]

	def error_logs(self):
		return frappe.get_all("Error Log", filters={"method": LOG_TITLE}, pluck="error")

	# -- event creation --------------------------------------------------------------------

	def test_initial_assignment_creates_one_event_queued_after_commit(self):
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})

		[name] = self.events(deal)
		event = self.event(name)
		self.assertEqual(event.intended_user, COACH_A)
		self.assertEqual(event.effective_user, COACH_A)
		self.assertEqual(event.assigned_by, ASSIGNER)
		self.assertEqual(event.mode, MODE_LIVE)
		self.assertEqual((event.email_status, event.slack_status), (STATUS_PENDING, STATUS_PENDING))
		self.assertTrue(event.assigned_at)

		[job] = self.enqueued
		self.assertTrue(job["enqueue_after_commit"])
		self.assertTrue(job["deduplicate"])
		self.assertEqual(job["event_key"], name)
		self.assertIn(name, job["job_id"])
		# Nothing is delivered inside the Deal's transaction.
		self.sendmail.assert_not_called()
		self.slack_post.assert_not_called()

	def test_assignment_on_existing_deal_creates_one_event(self):
		deal = self.make_deal()
		self.assertEqual(self.events(deal), [])

		self.assign(deal, COACH_A)
		self.assertEqual(len(self.events(deal)), 1)
		self.assertEqual(len(self.enqueued), 1)

	def test_reassignment_notifies_only_the_new_coach(self):
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})
		self.run_worker(self.events(deal)[0])
		self.sendmail.reset_mock()
		self.slack_post.reset_mock()

		self.assign(deal, COACH_B)
		events = self.events(deal)
		self.assertEqual(len(events), 2)
		event = self.run_worker(events[1])

		self.assertEqual(event.intended_user, COACH_B)
		self.assertEqual(self.email_recipients(), [[COACH_B]])
		self.assertEqual(self.slack_calls()[0][1], {"email": COACH_B})
		self.assertEqual((event.email_status, event.slack_status), (STATUS_SENT, STATUS_SENT))

	def test_unchanged_save_creates_no_event(self):
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})
		deal.reload()
		deal.last_name = "Renamed"
		deal.save(ignore_permissions=True)
		self.assign(deal, COACH_A)

		self.assertEqual(len(self.events(deal)), 1)
		self.assertEqual(len(self.enqueued), 1)

	def test_clearing_the_coach_creates_no_event(self):
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})
		self.assign(deal, None)

		self.assertEqual(len(self.events(deal)), 1)

	def test_self_assignment_creates_no_event(self):
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: ASSIGNER})
		self.assertEqual(self.events(deal), [])
		self.assertEqual(self.enqueued, [])

	def test_other_pipelines_create_no_event(self):
		deal = self.make_deal(
			pipeline_type=PIPELINE_INDIVIDUAL_SESSION, **{FIELD_DELIVERY_COACH: COACH_A}
		)
		self.assertEqual(self.events(deal), [])

	def test_import_does_not_backfill(self):
		frappe.flags.in_import = True
		try:
			deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})
		finally:
			frappe.flags.in_import = False
		self.assertEqual(self.events(deal), [])

	def test_queue_failure_never_blocks_the_deal_save(self):
		with patch(
			"crm.txb.delivery_coach_assignment.get_notification_config",
			side_effect=RuntimeError("settings unavailable"),
		):
			deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})

		self.assertTrue(frappe.db.exists("CRM Deal", deal.name))
		self.assertTrue(self.error_logs())

	# -- rapid changes and retries ---------------------------------------------------------

	def test_rapid_reassignment_skips_the_superseded_coach(self):
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})
		self.assign(deal, COACH_B)
		first, second = self.events(deal)

		stale = self.run_worker(first)
		self.assertEqual((stale.email_status, stale.slack_status), (STATUS_SKIPPED, STATUS_SKIPPED))
		self.assertEqual(stale.email_error, SUPERSEDED)
		self.sendmail.assert_not_called()
		self.slack_post.assert_not_called()

		current = self.run_worker(second)
		self.assertEqual((current.email_status, current.slack_status), (STATUS_SENT, STATUS_SENT))
		self.assertEqual(self.email_recipients(), [[COACH_B]])

	def test_deleted_deal_is_skipped_and_stays_deletable(self):
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})
		[name] = self.events(deal)
		frappe.delete_doc("CRM Deal", deal.name, ignore_permissions=True)

		event = self.run_worker(name)
		self.assertEqual((event.email_status, event.slack_status), (STATUS_SKIPPED, STATUS_SKIPPED))
		self.sendmail.assert_not_called()

	def test_retry_only_resends_failed_channels(self):
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})
		[name] = self.events(deal)
		self.slack["chat.postMessage"] = {"ok": False, "error": "ratelimited"}

		event = self.run_worker(name)
		self.assertEqual((event.email_status, event.slack_status), (STATUS_SENT, STATUS_FAILED))
		self.assertIn("ratelimited", event.slack_error)
		self.assertEqual(self.sendmail.call_count, 1)

		self.slack["chat.postMessage"] = {"ok": True}
		event = self.run_worker(name)
		self.assertEqual((event.email_status, event.slack_status), (STATUS_SENT, STATUS_SENT))
		self.assertEqual(self.sendmail.call_count, 1)

		self.slack_post.reset_mock()
		event = self.run_worker(name)
		self.assertEqual(self.sendmail.call_count, 1)
		self.slack_post.assert_not_called()

	# -- modes -----------------------------------------------------------------------------

	def test_disabled_sends_nothing(self):
		self.set_mode(MODE_DISABLED)
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})
		[name] = self.events(deal)

		self.assertEqual(self.enqueued, [])
		event = self.run_worker(name)
		self.assertEqual((event.email_status, event.slack_status), (STATUS_SKIPPED, STATUS_SKIPPED))
		self.sendmail.assert_not_called()
		self.slack_post.assert_not_called()

	def test_test_redirect_routes_both_channels_to_the_test_user(self):
		self.set_mode(MODE_TEST_REDIRECT, TESTER)
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})
		[name] = self.events(deal)

		event = self.run_worker(name)
		self.assertEqual((event.intended_user, event.effective_user), (COACH_A, TESTER))
		self.assertEqual(self.email_recipients(), [[TESTER]])
		self.assertIn("[TEST for Coach A]", self.sendmail.call_args.kwargs["subject"])
		self.assertIn("intended for <strong>Coach A</strong>", self.sendmail.call_args.kwargs["message"])

		lookup, _open, post = self.slack_calls()
		self.assertEqual(lookup[1], {"email": TESTER})
		self.assertIn("Test notification", post[1]["text"])
		self.assertIn("*Coach A*", post[1]["text"])

	def test_live_routes_to_the_assigned_coach(self):
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})
		self.run_worker(self.events(deal)[0])

		self.assertEqual(self.email_recipients(), [[COACH_A]])
		self.assertEqual(self.slack_calls()[0][1], {"email": COACH_A})
		self.assertNotIn("TEST", self.sendmail.call_args.kwargs["subject"])

	# -- content ---------------------------------------------------------------------------

	def test_email_and_slack_carry_the_assignment_details(self):
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})
		event = self.run_worker(self.events(deal)[0])
		deal.reload()
		url = get_url(f"/crm/deals/{deal.name}")
		assigned_at = format_datetime(event.assigned_at)
		details = ("Grace Client", ORGANIZATION, deal.status, "Ada", assigned_at, "Coach A", url)

		subject = self.sendmail.call_args.kwargs["subject"]
		body = self.sendmail.call_args.kwargs["message"]
		text = self.slack_calls()[2][1]["text"]
		self.assertIn("Grace Client", subject)
		for content in (body, text):
			for expected in details:
				self.assertIn(expected, content)

	def test_slack_message_includes_program_type_and_escapes_markup(self):
		text = build_slack_message(
			{
				"client_name": "A <b>&",
				"organization": "",
				"program_type": "Mindset",
				"status": "Submitted",
				"assigned_by_name": "Ada",
				"assigned_at": "28-09-2026 10:00",
				"opportunity_url": "https://crm.example/crm/deals/D-1",
				"intended_coach_name": "Coach A",
				"is_test_redirect": False,
			}
		)
		self.assertIn("*Program:* Mindset", text)
		self.assertIn("A &lt;b&gt;&amp;", text)
		self.assertNotIn("Organization", text)
		self.assertIn("<https://crm.example/crm/deals/D-1|Open the Opportunity in CRM>", text)

	# -- Slack -----------------------------------------------------------------------------

	def test_slack_dm_uses_lookup_open_and_post(self):
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})
		self.run_worker(self.events(deal)[0])

		calls = self.slack_calls()
		self.assertEqual(
			[method for method, _data, _headers in calls],
			["users.lookupByEmail", "conversations.open", "chat.postMessage"],
		)
		self.assertEqual(calls[1][1], {"users": "U123"})
		self.assertEqual(calls[2][1]["channel"], "D123")
		for _method, _data, headers in calls:
			self.assertEqual(headers, {"Authorization": f"Bearer {TOKEN}"})

	def test_missing_slack_user_fails_slack_only(self):
		self.slack["users.lookupByEmail"] = {"ok": False, "error": "users_not_found"}
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})
		event = self.run_worker(self.events(deal)[0])

		self.assertEqual((event.email_status, event.slack_status), (STATUS_SENT, STATUS_FAILED))
		self.assertEqual(event.slack_error, "users.lookupByEmail: users_not_found")
		self.assertEqual([c[0] for c in self.slack_calls()], ["users.lookupByEmail"])
		[log] = self.error_logs()
		for expected in (deal.name, COACH_A, MODE_LIVE, "channel=slack", "users_not_found"):
			self.assertIn(expected, log)
		self.assertNotIn(TOKEN, log)

	def test_email_failure_does_not_suppress_slack(self):
		self.sendmail.side_effect = frappe.OutgoingEmailError("SMTP refused")
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})
		event = self.run_worker(self.events(deal)[0])

		self.assertEqual((event.email_status, event.slack_status), (STATUS_FAILED, STATUS_SENT))
		self.assertIn("SMTP refused", event.email_error)
		[log] = self.error_logs()
		self.assertIn("channel=email", log)

	def test_slack_transport_error_is_sanitized(self):
		self.slack_post.side_effect = requests.ConnectionError(f"Bearer {TOKEN}")
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})
		event = self.run_worker(self.events(deal)[0])

		self.assertEqual((event.email_status, event.slack_status), (STATUS_SENT, STATUS_FAILED))
		self.assertEqual(event.slack_error, "users.lookupByEmail: ConnectionError")
		self.assertNotIn(TOKEN, " ".join(self.error_logs()))

	def test_missing_bot_token_fails_slack_only(self):
		frappe.db.delete("__Auth", {"doctype": SETTINGS_DOCTYPE, "fieldname": SETTING_SLACK_BOT_TOKEN})
		deal = self.make_deal(**{FIELD_DELIVERY_COACH: COACH_A})
		event = self.run_worker(self.events(deal)[0])

		self.assertEqual((event.email_status, event.slack_status), (STATUS_SENT, STATUS_FAILED))
		self.assertEqual(event.slack_error, "missing_bot_token")
		self.slack_post.assert_not_called()

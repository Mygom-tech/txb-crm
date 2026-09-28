# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-269: the Delivery Coach assignment notification foundation.

Pins what the notification consumer builds on: the migration installs fail-closed, Disabled
settings and is safe to re-run without replaying history; the Slack token stays encrypted and
out of the ledger; one ledger row per assignment event; each channel moves independently and a
Sent channel can never be sent again; the editable template renders the documented context.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_url, now_datetime

from crm.patches.v1_0.add_delivery_coach_notification_foundation import execute as migrate
from crm.txb.constants import ADMIN_ROLE, FIELD_DELIVERY_COACH, PIPELINE_DELIVERING_COACHING
from crm.txb.delivery_coach_notifications import (
	ASSIGNMENT_EMAIL_TEMPLATE,
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
	build_assignment_email_context,
	get_notification_config,
	get_slack_bot_token,
	render_assignment_email,
	sanitize_error,
)

COACH = "txb-notify-coach@example.com"
TESTER = "txb-notify-tester@example.com"
# Built at runtime so GitHub push protection does not flag a fake Slack token.
TOKEN = "-".join(("xoxb", "123456789012", "abcdefghijklmnopQRSTUVWX"))

CONTEXT_KEYS = {
	"client_name",
	"organization",
	"program_type",
	"status",
	"assigned_by_name",
	"assigned_at",
	"opportunity_url",
	"intended_coach_name",
	"is_test_redirect",
}


class TestDeliveryCoachNotificationFoundation(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		for email, first_name in ((COACH, "Coach B"), (TESTER, "Vainius")):
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": first_name,
						"send_welcome_email": 0,
					}
				).insert(ignore_permissions=True)
		# A new Delivering Coaching deal raises an Admin task (TXB-208), so one Admin must exist.
		frappe.get_doc("User", TESTER).add_roles("Sales User", ADMIN_ROLE)
		frappe.db.commit()  # nosemgrep -- the users must outlive per-test rollback

	def setUp(self):
		migrate()

	def tearDown(self):
		frappe.db.rollback()

	# -- helpers ---------------------------------------------------------------------------

	def make_deal(self, **fields):
		return frappe.get_doc(
			{
				"doctype": "CRM Deal",
				"pipeline_type": PIPELINE_DELIVERING_COACHING,
				"status": "Submitted",
				**fields,
			}
		).insert(ignore_permissions=True)

	def make_event(self, event_key="deal-1:assign-1", **fields):
		deal = fields.pop("deal", None) or self.make_deal().name
		return frappe.get_doc(
			{
				"doctype": LEDGER_DOCTYPE,
				"event_key": event_key,
				"deal": deal,
				"intended_user": COACH,
				"effective_user": TESTER,
				"assigned_by": "Administrator",
				"assigned_at": now_datetime(),
				"mode": MODE_TEST_REDIRECT,
				**fields,
			}
		).insert(ignore_permissions=True)

	# -- migration -------------------------------------------------------------------------

	def test_migration_installs_settings_defaulting_to_disabled(self):
		frappe.db.delete("Singles", {"doctype": SETTINGS_DOCTYPE, "field": SETTING_MODE})
		migrate()

		meta = frappe.get_meta(SETTINGS_DOCTYPE)
		mode = meta.get_field(SETTING_MODE)
		self.assertEqual(mode.fieldtype, "Select")
		self.assertEqual(mode.options.split("\n"), [MODE_DISABLED, MODE_TEST_REDIRECT, MODE_LIVE])
		self.assertEqual(mode.default, MODE_DISABLED)
		test_user = meta.get_field(SETTING_TEST_USER)
		self.assertEqual((test_user.fieldtype, test_user.options), ("Link", "User"))
		self.assertEqual(meta.get_field(SETTING_SLACK_BOT_TOKEN).fieldtype, "Password")

		self.assertEqual(frappe.db.get_single_value(SETTINGS_DOCTYPE, SETTING_MODE), MODE_DISABLED)
		self.assertEqual(get_notification_config()["mode"], MODE_DISABLED)

	def test_ledger_schema_has_unique_event_key_and_independent_channels(self):
		meta = frappe.get_meta(LEDGER_DOCTYPE)
		self.assertEqual(meta.autoname, "field:event_key")
		self.assertTrue(meta.get_field("event_key").unique)
		for channel in ("email", "slack"):
			status = meta.get_field(f"{channel}_status")
			self.assertEqual(status.options.split("\n"), ["Pending", "Sent", "Failed", "Skipped"])
			self.assertEqual(status.default, STATUS_PENDING)
			self.assertTrue(meta.has_field(f"{channel}_sent_at"))
			self.assertTrue(meta.has_field(f"{channel}_error"))

	def test_rerun_is_safe_and_keeps_operator_choices(self):
		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_MODE, MODE_LIVE)
		frappe.db.set_value("Email Template", ASSIGNMENT_EMAIL_TEMPLATE, "response_html", "<p>edited</p>")

		migrate()
		migrate()

		self.assertEqual(frappe.db.get_single_value(SETTINGS_DOCTYPE, SETTING_MODE), MODE_LIVE)
		self.assertEqual(
			frappe.db.get_value("Email Template", ASSIGNMENT_EMAIL_TEMPLATE, "response_html"),
			"<p>edited</p>",
		)
		for fieldname in (SETTING_MODE, SETTING_TEST_USER, SETTING_SLACK_BOT_TOKEN):
			self.assertEqual(
				frappe.db.count("Custom Field", {"dt": SETTINGS_DOCTYPE, "fieldname": fieldname}), 1
			)
		self.assertEqual(frappe.db.count("Email Template", {"name": ASSIGNMENT_EMAIL_TEMPLATE}), 1)

	def test_migration_generates_no_historical_events(self):
		self.make_deal(**{FIELD_DELIVERY_COACH: COACH})
		before = frappe.db.count(LEDGER_DOCTYPE)
		migrate()
		self.assertEqual(frappe.db.count(LEDGER_DOCTYPE), before)

	# -- configuration ---------------------------------------------------------------------

	def test_config_fails_closed(self):
		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_TEST_USER, None)
		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_MODE, MODE_TEST_REDIRECT)
		self.assertEqual(get_notification_config()["mode"], MODE_DISABLED)

		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_MODE, "Everyone")
		self.assertEqual(get_notification_config()["mode"], MODE_DISABLED)

		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_MODE, MODE_TEST_REDIRECT)
		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_TEST_USER, TESTER)
		self.assertEqual(
			get_notification_config(), {"mode": MODE_TEST_REDIRECT, "test_user": TESTER}
		)

	def test_slack_token_is_encrypted_and_never_exposed(self):
		settings = frappe.get_single(SETTINGS_DOCTYPE)
		settings.set(SETTING_SLACK_BOT_TOKEN, TOKEN)
		settings.save(ignore_permissions=True)

		stored = frappe.db.get_value(
			"Singles", {"doctype": SETTINGS_DOCTYPE, "field": SETTING_SLACK_BOT_TOKEN}, "value"
		)
		self.assertNotEqual(stored, TOKEN)
		self.assertNotIn(TOKEN, frappe.get_single(SETTINGS_DOCTYPE).as_json())
		self.assertEqual(get_slack_bot_token(), TOKEN)

		self.assertNotIn(TOKEN, sanitize_error(f"invalid_auth using {TOKEN}"))
		self.assertNotIn("xoxp-999", sanitize_error("Authorization: Bearer xoxp-999-abc"))
		self.assertLessEqual(len(sanitize_error("x" * 5000)), 500)

		event = self.make_event(slack_status=STATUS_FAILED, slack_error=f"token {TOKEN} rejected")
		self.assertNotIn(TOKEN, frappe.db.get_value(LEDGER_DOCTYPE, event.name, "slack_error"))
		ledger_types = {df.fieldtype for df in frappe.get_meta(LEDGER_DOCTYPE).fields}
		self.assertNotIn("Password", ledger_types)

	# -- ledger ----------------------------------------------------------------------------

	def test_event_key_is_unique(self):
		event = self.make_event("deal-1:assign-dup")
		with self.assertRaises(frappe.DuplicateEntryError):
			self.make_event("deal-1:assign-dup", deal=event.deal)

	def test_channels_move_independently_and_sent_is_final(self):
		event = self.make_event()
		self.assertEqual((event.email_status, event.slack_status), (STATUS_PENDING, STATUS_PENDING))

		event.mark_channel("slack", STATUS_FAILED, f"channel_not_found {TOKEN}")
		self.assertEqual(event.email_status, STATUS_PENDING)
		self.assertNotIn(TOKEN, event.slack_error)
		self.assertTrue(event.is_channel_pending("slack"))

		event.mark_channel("email", STATUS_SENT)
		self.assertIsNotNone(event.email_sent_at)
		self.assertFalse(event.is_channel_pending("email"))

		event.mark_channel("slack", STATUS_SENT)
		self.assertIsNotNone(event.slack_sent_at)

		for status in (STATUS_PENDING, STATUS_FAILED, STATUS_SKIPPED):
			event.reload()
			with self.assertRaises(frappe.ValidationError):
				event.mark_channel("email", status)

	def test_skipped_channel_is_final(self):
		event = self.make_event()
		event.mark_channel("slack", STATUS_SKIPPED, "No Slack user for coach")
		self.assertFalse(event.is_channel_pending("slack"))
		with self.assertRaises(frappe.ValidationError):
			event.mark_channel("slack", STATUS_SENT)

	# -- email template --------------------------------------------------------------------

	def test_template_renders_documented_context(self):
		deal = frappe._dict(
			name="CRM-DEAL-2026-00042",
			first_name="Ona",
			last_name="Jonaitė",
			organization="Acme UAB",
			custom_program_type="TxB Executive",
			status="Submitted",
		)
		context = build_assignment_email_context(
			deal, COACH, "Administrator", now_datetime(), is_test_redirect=True
		)
		self.assertEqual(set(context), CONTEXT_KEYS)
		self.assertEqual(context["opportunity_url"], get_url("/crm/deals/CRM-DEAL-2026-00042"))
		self.assertEqual(context["client_name"], "Ona Jonaitė")
		self.assertEqual(context["intended_coach_name"], "Coach B")

		subject, body = render_assignment_email(context)
		self.assertIn("[TEST for Coach B]", subject)
		self.assertIn("intended for <strong>Coach B</strong>", body)
		self.assertIn("#002d5b", body)
		self.assertIn("TxB team", body)
		for value in ("Ona Jonaitė", "Acme UAB", "TxB Executive", "Submitted", context["opportunity_url"]):
			self.assertIn(value, body)

		subject, body = render_assignment_email({**context, "is_test_redirect": False})
		self.assertNotIn("TEST", subject)
		self.assertNotIn("intended for", body)

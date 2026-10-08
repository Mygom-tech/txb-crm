# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-269 / TXB-288: the Delivery Coach assignment notification foundation.

Pins what the notification consumer builds on: the migration installs fail-closed, Disabled
settings and is safe to re-run without replaying history or touching existing ledger rows; only
a System Manager may change the settings and the Slack token stays encrypted and out of the
ledger; one ledger row per assignment event with immutable routing and generation; each channel
moves independently, a Sent channel can never be sent again and an Uncertain one is never
resent automatically; the editable template wraps the documented context in the TXB-116 wrapper
and signature and reads "Not provided" for a blank organization or program type.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_url, now_datetime

from crm.patches.v1_0.add_delivery_coach_notification_foundation import execute as migrate
from crm.txb.constants import (
	ADMIN_ROLE,
	CONFIRMATION_TEMPLATE,
	FIELD_DELIVERY_COACH,
	PIPELINE_DELIVERING_COACHING,
)
from crm.txb.delivery_coach_assignment import build_slack_message
from crm.txb.delivery_coach_notifications import (
	ASSIGNMENT_EMAIL_TEMPLATE,
	LEDGER_DOCTYPE,
	MODE_DISABLED,
	MODE_LIVE,
	MODE_TEST_REDIRECT,
	NOT_PROVIDED,
	SETTING_MODE,
	SETTING_SLACK_BOT_TOKEN,
	SETTING_TEST_USER,
	SETTINGS_DOCTYPE,
	STATUS_FAILED,
	STATUS_PENDING,
	STATUS_SENT,
	STATUS_SKIPPED,
	STATUS_UNCERTAIN,
	build_assignment_email_context,
	get_notification_config,
	get_slack_bot_token,
	render_assignment_email,
	sanitize_error,
)
from crm.txb.test_registration import FULL_CONFIRMATION_HTML

COACH = "txb-notify-coach@example.com"
TESTER = "txb-notify-tester@example.com"
SALES_MANAGER = "txb-notify-sales-manager@example.com"
SYSTEM_MANAGER = "txb-notify-system-manager@example.com"
# Built at runtime so GitHub push protection does not flag a fake Slack token.
TOKEN = "-".join(("xoxb", "123456789012", "abcdefghijklmnopQRSTUVWX"))
OTHER_TOKEN = "-".join(("xoxb", "210987654321", "XWVUTSRQPONMLKJIhgfedcba"))

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
IDENTITY_FIELDS = (
	"event_key",
	"deal",
	"mode",
	"assignment_generation",
	"intended_user",
	"effective_user",
	"assigned_by",
	"assigned_at",
)

# The body TXB-269 seeded, verbatim: an unedited copy of it is what the migration re-seeds.
TXB269_SEED_BODY = (
	'<div style="max-width: 600px; margin: 0 auto; font-family: Arial, sans-serif;">'
	'<div style="background: #002d5b; padding: 20px; text-align: center; color: #ffffff;'
	' font-size: 22px; font-weight: bold;">TxB</div>'
	'<div style="padding: 30px; color: #333;">'
	"{% if is_test_redirect %}"
	'<p style="background: #fff4e5; padding: 10px; border: 1px solid #f5a623;">'
	"<strong>Test notification.</strong> This assignment is intended for "
	"<strong>{{ intended_coach_name }}</strong>.</p>"
	"{% endif %}"
	"<p>Hi {{ intended_coach_name }},</p>"
	"<p>{{ assigned_by_name }} assigned a client to you as Delivery Coach on {{ assigned_at }}.</p>"
	"<table>"
	"<tr><td>Client:</td><td>{{ client_name }}</td></tr>"
	"{% if organization %}<tr><td>Organization:</td><td>{{ organization }}</td></tr>{% endif %}"
	"{% if program_type %}<tr><td>Program:</td><td>{{ program_type }}</td></tr>{% endif %}"
	"<tr><td>Status:</td><td>{{ status }}</td></tr>"
	"</table>"
	'<p><a href="{{ opportunity_url }}">Open the Opportunity in CRM</a></p>'
	"</div>"
	'<div style="background: #f4f4f4; padding: 20px; color: #666; font-size: 12px;">'
	"<p>Best regards,<br>TxB team</p>"
	"</div>"
	"</div>"
)


class TestDeliveryCoachNotificationFoundation(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		users = (
			(COACH, "Coach B", ()),
			# A new Delivering Coaching deal raises an Admin task (TXB-208), so one Admin must exist.
			(TESTER, "Vainius", ("Sales User", ADMIN_ROLE)),
			(SALES_MANAGER, "Sales Manager", ("Sales User", "Sales Manager")),
			(SYSTEM_MANAGER, "System Manager", ("System Manager",)),
		)
		for email, first_name, roles in users:
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": first_name,
						"send_welcome_email": 0,
					}
				).insert(ignore_permissions=True)
			if roles:
				frappe.get_doc("User", email).add_roles(*roles)
		frappe.db.commit()  # nosemgrep -- the users must outlive per-test rollback

	def setUp(self):
		migrate()

	def tearDown(self):
		frappe.set_user("Administrator")
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

	def save_settings_as(self, user, **values):
		frappe.set_user(user)
		settings = frappe.get_single(SETTINGS_DOCTYPE)
		settings.update(values)
		# Ordinary FCRM Settings access is granted; only the field guard may refuse.
		settings.save(ignore_permissions=True)

	def set_confirmation_body(self, html):
		if frappe.db.exists("Email Template", CONFIRMATION_TEMPLATE):
			frappe.db.set_value("Email Template", CONFIRMATION_TEMPLATE, "response_html", html)
		else:
			frappe.get_doc(
				{
					"doctype": "Email Template",
					"name": CONFIRMATION_TEMPLATE,
					"subject": "Registracija sėkmingai gauta",
					"use_html": 1,
					"response_html": html,
				}
			).insert(ignore_permissions=True)

	def assignment_body(self):
		return frappe.db.get_value("Email Template", ASSIGNMENT_EMAIL_TEMPLATE, "response_html")

	def context(self, is_test_redirect=False, **deal_fields):
		deal = frappe._dict(
			{
				"name": "CRM-DEAL-2026-00042",
				"first_name": "Ona",
				"last_name": "Jonaitė",
				"organization": "Acme UAB",
				"custom_program_type": "TxB Executive",
				"status": "Submitted",
				**deal_fields,
			}
		)
		return build_assignment_email_context(
			deal, COACH, "Administrator", now_datetime(), is_test_redirect=is_test_redirect
		)

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

	def test_ledger_schema_has_uncertain_attempts_claims_and_immutable_identity(self):
		meta = frappe.get_meta(LEDGER_DOCTYPE)
		self.assertEqual(meta.autoname, "field:event_key")
		self.assertTrue(meta.get_field("event_key").unique)
		for channel in ("email", "slack"):
			status = meta.get_field(f"{channel}_status")
			self.assertEqual(
				status.options.split("\n"), ["Pending", "Sent", "Failed", "Skipped", "Uncertain"]
			)
			self.assertEqual(status.default, STATUS_PENDING)
			self.assertTrue(meta.has_field(f"{channel}_sent_at"))
			self.assertTrue(meta.has_field(f"{channel}_error"))
			attempts = meta.get_field(f"{channel}_attempts")
			self.assertEqual((attempts.fieldtype, attempts.default), ("Int", "0"))
			for suffix in ("claimed_at", "recovered_at"):
				self.assertEqual(meta.get_field(f"{channel}_{suffix}").fieldtype, "Datetime")
		for fieldname in IDENTITY_FIELDS:
			self.assertTrue(meta.get_field(fieldname).set_only_once, fieldname)
		self.assertFalse(meta.get_field("assignment_generation").reqd)

	def test_rerun_is_safe_and_keeps_operator_choices(self):
		frappe.db.set_single_value(SETTINGS_DOCTYPE, SETTING_MODE, MODE_LIVE)
		frappe.db.set_value("Email Template", ASSIGNMENT_EMAIL_TEMPLATE, "response_html", "<p>edited</p>")

		migrate()
		migrate()

		self.assertEqual(frappe.db.get_single_value(SETTINGS_DOCTYPE, SETTING_MODE), MODE_LIVE)
		self.assertEqual(self.assignment_body(), "<p>edited</p>")
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

	def test_migration_preserves_existing_rows_without_backfill(self):
		event = self.make_event("deal-1:legacy")
		# A row recorded before TXB-288: no generation, never claimed, Slack already Failed.
		frappe.db.set_value(
			LEDGER_DOCTYPE,
			event.name,
			{"assignment_generation": None, "slack_status": STATUS_FAILED},
			update_modified=False,
		)
		before = frappe.db.get_value(LEDGER_DOCTYPE, event.name, "*", as_dict=True)
		count = frappe.db.count(LEDGER_DOCTYPE)

		migrate()

		self.assertEqual(frappe.db.count(LEDGER_DOCTYPE), count)
		after = frappe.db.get_value(LEDGER_DOCTYPE, event.name, "*", as_dict=True)
		self.assertEqual(after, before)
		self.assertIsNone(after.assignment_generation)
		self.assertEqual((after.email_attempts, after.slack_attempts), (0, 0))
		self.assertIsNone(after.slack_claimed_at)

		# The legacy row still works and its blank generation is never filled in.
		legacy = frappe.get_doc(LEDGER_DOCTYPE, event.name)
		legacy.mark_channel("slack", STATUS_SENT)
		self.assertIsNone(frappe.db.get_value(LEDGER_DOCTYPE, event.name, "assignment_generation"))

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

	# -- settings authorization ------------------------------------------------------------

	def test_system_manager_and_administrator_can_change_notification_settings(self):
		self.save_settings_as(
			"Administrator",
			**{SETTING_MODE: MODE_LIVE, SETTING_TEST_USER: TESTER, SETTING_SLACK_BOT_TOKEN: TOKEN},
		)
		self.assertEqual(frappe.db.get_single_value(SETTINGS_DOCTYPE, SETTING_MODE), MODE_LIVE)
		self.assertEqual(get_slack_bot_token(), TOKEN)

		self.save_settings_as(
			SYSTEM_MANAGER,
			**{SETTING_MODE: MODE_TEST_REDIRECT, SETTING_SLACK_BOT_TOKEN: OTHER_TOKEN},
		)
		frappe.set_user("Administrator")
		self.assertEqual(
			get_notification_config(), {"mode": MODE_TEST_REDIRECT, "test_user": TESTER}
		)
		self.assertEqual(get_slack_bot_token(), OTHER_TOKEN)

	def test_other_roles_cannot_change_notification_settings(self):
		self.save_settings_as(
			"Administrator",
			**{SETTING_MODE: MODE_DISABLED, SETTING_TEST_USER: TESTER, SETTING_SLACK_BOT_TOKEN: TOKEN},
		)
		for user in (SALES_MANAGER, TESTER):
			for change in (
				{SETTING_MODE: MODE_LIVE},
				{SETTING_TEST_USER: COACH},
				{SETTING_TEST_USER: None},
				{SETTING_SLACK_BOT_TOKEN: OTHER_TOKEN},
				{SETTING_SLACK_BOT_TOKEN: ""},
			):
				with self.subTest(user=user, change=change):
					with self.assertRaises(frappe.PermissionError) as raised:
						self.save_settings_as(user, **change)
					self.assertNotIn(TOKEN, str(raised.exception))
					self.assertNotIn(OTHER_TOKEN, str(raised.exception))

		frappe.set_user("Administrator")
		self.assertEqual(
			get_notification_config(), {"mode": MODE_DISABLED, "test_user": TESTER}
		)
		self.assertEqual(get_slack_bot_token(), TOKEN)

	def test_unchanged_masked_token_is_not_a_change(self):
		self.save_settings_as("Administrator", **{SETTING_SLACK_BOT_TOKEN: TOKEN})

		# What the settings form posts back for an untouched token: asterisks, of any length.
		self.save_settings_as(SALES_MANAGER)
		self.save_settings_as(SALES_MANAGER, **{SETTING_SLACK_BOT_TOKEN: "*****"})

		frappe.set_user("Administrator")
		self.assertEqual(get_slack_bot_token(), TOKEN)
		self.assertNotIn(TOKEN, frappe.get_single(SETTINGS_DOCTYPE).as_json())

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

		for status in (STATUS_PENDING, STATUS_FAILED, STATUS_SKIPPED, STATUS_UNCERTAIN):
			event.reload()
			with self.assertRaises(frappe.ValidationError):
				event.mark_channel("email", status)

	def test_skipped_channel_is_final(self):
		event = self.make_event()
		event.mark_channel("slack", STATUS_SKIPPED, "No Slack user for coach")
		self.assertFalse(event.is_channel_pending("slack"))
		with self.assertRaises(frappe.ValidationError):
			event.mark_channel("slack", STATUS_SENT)

	def test_uncertain_channel_is_held_for_reconciliation(self):
		event = self.make_event()
		event.mark_channel("slack", STATUS_UNCERTAIN, "timeout after chat.postMessage")
		self.assertEqual(event.email_status, STATUS_PENDING)
		self.assertFalse(event.is_channel_pending("slack"))
		self.assertFalse(event.claim_channel("slack"))

		event.reload()
		with self.assertRaises(frappe.ValidationError):
			event.mark_channel("slack", STATUS_PENDING)

		# Reconciliation settles it either way; confirmed-not-delivered becomes retryable.
		event.reload()
		event.mark_channel("slack", STATUS_FAILED, "not delivered")
		self.assertTrue(event.is_channel_pending("slack"))

		other = self.make_event("deal-1:assign-2")
		other.mark_channel("email", STATUS_UNCERTAIN)
		other.mark_channel("email", STATUS_SENT)
		self.assertIsNotNone(other.email_sent_at)

	def test_claims_count_attempts_and_stamp_recovery(self):
		event = self.make_event()
		self.assertEqual((event.email_attempts, event.slack_attempts), (0, 0))

		self.assertTrue(event.claim_channel("slack"))
		self.assertEqual(event.slack_attempts, 1)
		self.assertIsNotNone(event.slack_claimed_at)
		self.assertIsNone(event.slack_recovered_at)
		self.assertEqual((event.email_attempts, event.email_claimed_at), (0, None))

		event.mark_channel("slack", STATUS_FAILED, "ratelimited")
		self.assertTrue(event.claim_channel("slack", recovery=True))
		self.assertEqual(event.slack_attempts, 2)
		self.assertIsNotNone(event.slack_recovered_at)

		event.mark_channel("slack", STATUS_SENT)
		self.assertFalse(event.claim_channel("slack"))
		self.assertEqual(frappe.db.get_value(LEDGER_DOCTYPE, event.name, "slack_attempts"), 2)

		event.reload()
		event.slack_attempts = 0
		with self.assertRaises(frappe.ValidationError):
			event.save(ignore_permissions=True)

	def test_captured_routing_and_generation_are_immutable(self):
		event = self.make_event()
		self.assertEqual(event.assignment_generation, event.event_key)
		explicit = self.make_event("deal-1:assign-3", assignment_generation="deal-1:generation-7")
		self.assertEqual(explicit.assignment_generation, "deal-1:generation-7")

		for fieldname, value in (
			("mode", MODE_LIVE),
			("intended_user", TESTER),
			("effective_user", COACH),
			("assigned_by", TESTER),
			("assignment_generation", "deal-1:generation-8"),
		):
			with self.subTest(fieldname=fieldname):
				event.reload()
				event.set(fieldname, value)
				with self.assertRaises(frappe.ValidationError):
					event.save(ignore_permissions=True)

	# -- email template --------------------------------------------------------------------

	def test_template_renders_documented_context(self):
		context = self.context(is_test_redirect=True)
		self.assertEqual(set(context), CONTEXT_KEYS)
		self.assertEqual(context["opportunity_url"], get_url("/crm/deals/CRM-DEAL-2026-00042"))
		self.assertEqual(context["client_name"], "Ona Jonaitė")
		self.assertEqual(context["intended_coach_name"], "Coach B")
		self.assertEqual(context["assigned_by_name"], "Administrator")

		subject, body = render_assignment_email(context)
		self.assertIn("[TEST for Coach B]", subject)
		self.assertIn("intended for <strong>Coach B</strong>", body)
		for value in (
			"Ona Jonaitė",
			"Acme UAB",
			"TxB Executive",
			"Submitted",
			"Administrator",
			context["assigned_at"],
			context["opportunity_url"],
		):
			self.assertIn(value, body)

		subject, body = render_assignment_email({**context, "is_test_redirect": False})
		self.assertNotIn("TEST", subject)
		self.assertNotIn("intended for", body)

	def test_blank_organization_and_program_type_read_not_provided(self):
		context = self.context(organization="", custom_program_type="   ")
		self.assertEqual((context["organization"], context["program_type"]), (NOT_PROVIDED,) * 2)

		_subject, body = render_assignment_email(context)
		self.assertIn(f"<td>Organization:</td><td>{NOT_PROVIDED}</td>", body)
		self.assertIn(f"<td>Program:</td><td>{NOT_PROVIDED}</td>", body)

		text = build_slack_message(context)
		self.assertIn(f"*Organization:* {NOT_PROVIDED}", text)
		self.assertIn(f"*Program:* {NOT_PROVIDED}", text)

	def test_template_escapes_client_supplied_values(self):
		context = self.context(first_name="<script>x</script>", last_name="", organization="A & B")
		_subject, body = render_assignment_email(context)
		self.assertNotIn("<script>", body)
		self.assertIn("&lt;script&gt;x&lt;/script&gt;", body)
		self.assertIn("A &amp; B", body)

	def test_template_reuses_txb116_wrapper_and_signature(self):
		self.set_confirmation_body(FULL_CONFIRMATION_HTML)
		frappe.delete_doc("Email Template", ASSIGNMENT_EMAIL_TEMPLATE, ignore_permissions=True)

		migrate()

		body = self.assignment_body()
		# TXB-116's header, signature and footer, as the confirmation template has them.
		for wrapper in ("#002d5b", "https://txb.example/logo.png", "Pagarbiai, TxB komanda", "© TxB"):
			self.assertIn(wrapper, body)
		# Its registration content is replaced, and no second brand is invented.
		for registration in ("Jūsų registracija", "{{ first_name }}", "{{ workshop_name }}"):
			self.assertNotIn(registration, body)
		self.assertNotIn("Best regards", body)

		_subject, rendered = render_assignment_email(self.context())
		for value in ("Ona Jonaitė", "Acme UAB", "TxB Executive", "Pagarbiai, TxB komanda"):
			self.assertIn(value, rendered)
		self.assertLess(rendered.index("Ona Jonaitė"), rendered.index("Pagarbiai, TxB komanda"))
		self.assertLess(rendered.index("Pagarbiai, TxB komanda"), rendered.index("© TxB"))

	def test_unedited_seed_is_reconciled_and_operator_edits_survive(self):
		self.set_confirmation_body(FULL_CONFIRMATION_HTML)
		frappe.db.set_value(
			"Email Template", ASSIGNMENT_EMAIL_TEMPLATE, "response_html", TXB269_SEED_BODY
		)

		migrate()

		reseeded = self.assignment_body()
		self.assertIn("Pagarbiai, TxB komanda", reseeded)
		self.assertNotIn("Best regards", reseeded)
		migrate()
		self.assertEqual(self.assignment_body(), reseeded)

		edited = reseeded.replace("Open the Opportunity in CRM", "Open it")
		frappe.db.set_value("Email Template", ASSIGNMENT_EMAIL_TEMPLATE, "response_html", edited)
		migrate()
		migrate()
		self.assertEqual(self.assignment_body(), edited)

	def test_site_without_txb116_wrapper_gets_bare_content_until_it_has_one(self):
		self.set_confirmation_body("<p>Sveiki, {{ first_name }},</p>")
		frappe.delete_doc("Email Template", ASSIGNMENT_EMAIL_TEMPLATE, ignore_permissions=True)

		migrate()

		bare = self.assignment_body()
		self.assertTrue(bare.startswith("{% if is_test_redirect %}"))
		self.assertNotIn("#002d5b", bare)

		self.set_confirmation_body(FULL_CONFIRMATION_HTML)
		migrate()
		self.assertIn("Pagarbiai, TxB komanda", self.assignment_body())

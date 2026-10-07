# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-286: admin-only Team Activity summary, members and records over a metric registry.

Drives the whitelisted endpoints over real Call Logs, Human Contact Event rows and meeting Events
in a period no other data reaches (March 2031). Runs under `bench run-tests` and under a plain
`python -m pytest`: `setUpModule` connects to the local site only when no connection exists.
"""

import os
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import frappe
from frappe.query_builder import Order
from frappe.query_builder.functions import Count

from crm.txb import team_activity_metrics
from crm.txb.api import team_activity as api
from crm.txb.constants import FIELD_MEETING_KEY, OWNER_FIELDS
from crm.txb.human_contact import EVENT_DOCTYPE, source_key
from crm.txb.meetings import cancel_meeting_event, meeting_key
from crm.txb.team_activity import build_scope
from crm.txb.team_activity_metrics import MetricDescriptor, in_period, register

FROM, TO = "2031-03-10", "2031-03-12"
CALL_LOG_DOCTYPE = "CRM Call Log"
CONTACT_DOCTYPE = "Contact"
MEETING_DOCTYPE = "Event"
BUILT_INS = ["completed_calls", "reached_contacts", "agreed_meetings"]

_connected_here = False


def _sites_path() -> str:
	"""The bench `sites` directory: FRAPPE_SITES_PATH, else the first one above this file."""
	if os.environ.get("FRAPPE_SITES_PATH"):
		return os.environ["FRAPPE_SITES_PATH"]
	for parent in Path(__file__).resolve().parents:
		if (parent / "sites" / "apps.txt").exists():
			return str(parent / "sites")
	return "."


def setUpModule():
	global _connected_here
	if not getattr(frappe.local, "db", None):
		frappe.init(site=os.environ.get("FRAPPE_SITE", "localhost"), sites_path=_sites_path())
		frappe.connect()
		_connected_here = True


def tearDownModule():
	if _connected_here:
		frappe.db.rollback()
		frappe.destroy()


def make_user(prefix, roles=("Sales User",), enabled=1):
	email = f"{prefix}-{frappe.generate_hash(length=6)}@example.com"
	user = frappe.get_doc(
		{"doctype": "User", "email": email, "first_name": prefix, "send_welcome_email": 0}
	).insert(ignore_permissions=True)
	if roles:
		user.add_roles(*roles)
	if not enabled:
		frappe.db.set_value("User", email, "enabled", 0)
	return email


def values(summary):
	return {metric["key"]: metric["value"] for metric in summary["metrics"]}


class TeamActivityTestCase(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.caller = make_user("caller")
		self.coach = make_user("coach")
		self.owner = make_user("owner")
		self.contact_a = self.make_contact("Ann")
		self.contact_b = self.make_contact("Bea")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def make_contact(self, first_name):
		doc = frappe.new_doc(CONTACT_DOCTYPE)
		doc.first_name = first_name
		doc.last_name = "TXB286"
		if doc.meta.has_field(OWNER_FIELDS[CONTACT_DOCTYPE]):
			doc.set(OWNER_FIELDS[CONTACT_DOCTYPE], self.owner)
		return doc.insert(ignore_permissions=True).name

	def call(self, contact, at, caller=None, **fields):
		"""A Call Log logged on `contact`; its doc events record and attribute the Call event."""
		doc = {
			"doctype": CALL_LOG_DOCTYPE,
			"type": "Outgoing",
			"status": "Completed",
			"caller": caller or self.caller,
			"from": "+370 600 11111",
			"to": "+370 600 00000",
			"start_time": at,
			"end_time": at,
			"reference_doctype": CONTACT_DOCTYPE,
			"reference_docname": contact,
		}
		doc.update(fields)
		return frappe.get_doc(doc).insert(ignore_permissions=True)

	def ledger_event(self, actor, at, contact=None, channel="Coaching Call", source_doctype="FCRM Note"):
		"""The ledger row `sync_source` writes for a qualifying source; the source itself is not
		needed here. Logged on `contact`, its on_update attributes it to that Contact."""
		source_name = frappe.generate_hash(length=10)
		doc = frappe.get_doc(
			{
				"doctype": EVENT_DOCTYPE,
				"source_key": source_key(source_doctype, source_name),
				"source_doctype": source_doctype,
				"source_name": source_name,
				"channel": channel,
				"provenance": "Human",
				"actor": actor,
				"occurred_at": at,
				"reference_doctype": CONTACT_DOCTYPE if contact else None,
				"reference_name": contact,
				"recipients": "[]",
				"voided": 0,
			}
		)
		doc.flags.ignore_links = True
		return doc.insert(ignore_permissions=True)

	def meeting(self, booked_by, booked_at, contact, flow):
		event = frappe.get_doc(
			{
				"doctype": MEETING_DOCTYPE,
				"subject": "TXB286 meeting",
				"starts_on": "2031-04-01 10:00:00",
				"event_type": "Private",
				"event_category": "Meeting",
				"reference_doctype": CONTACT_DOCTYPE,
				"reference_docname": contact,
				FIELD_MEETING_KEY: meeting_key(CONTACT_DOCTYPE, contact, flow),
			}
		).insert(ignore_permissions=True)
		# What a scheduling action run by `booked_by` at `booked_at` stores.
		frappe.db.set_value(
			MEETING_DOCTYPE, event.name, {"owner": booked_by, "creation": booked_at}, update_modified=False
		)
		return event.name

	def seed(self):
		"""caller: 3 calls (2 to A, 1 to B) and a meeting later cancelled; coach: 1 coaching call
		to B and a meeting."""
		self.call(self.contact_a, "2031-03-10 09:00:00")
		self.call(self.contact_a, "2031-03-11 09:00:00")
		self.call(self.contact_b, "2031-03-11 10:00:00")
		self.ledger_event(self.coach, "2031-03-12 15:00:00", self.contact_b)
		self.meeting(self.caller, "2031-03-10 12:00:00", self.contact_a, "txb286")
		cancel_meeting_event(CONTACT_DOCTYPE, self.contact_a, "txb286")
		self.meeting(self.coach, "2031-03-11 12:00:00", self.contact_b, "txb286")


class TestAccess(TeamActivityTestCase):
	# -- ac-1 -----------------------------------------------------------------------------------

	def test_sales_roles_are_refused_before_any_metric_runs(self):
		spy = MetricDescriptor(
			key="spy",
			label="Spy",
			order=99,
			columns=[],
			predicate=MagicMock(),
			total=MagicMock(return_value=0),
			rows=MagicMock(return_value=[]),
		)
		sales_manager = make_user("manager", roles=("Sales Manager", "Sales User"))
		sales_user = make_user("seller")

		with patch.dict(team_activity_metrics._REGISTRY):
			register(spy)
			for user in (sales_manager, sales_user):
				frappe.set_user(user)
				for call in (
					lambda: api.summary(FROM, TO),
					lambda: api.summary(FROM, TO, self.caller),
					lambda: api.members(FROM, TO),
					lambda: api.records("spy", FROM, TO, None, 1),
				):
					with self.assertRaises(frappe.PermissionError):
						call()

		spy.predicate.assert_not_called()
		spy.total.assert_not_called()
		spy.rows.assert_not_called()

	def test_admins_are_admitted(self):
		for user in ("Administrator", make_user("admin", roles=("System Manager",))):
			frappe.set_user(user)
			self.assertEqual([m["key"] for m in api.summary(FROM, TO)["metrics"]], BUILT_INS)


class TestReconciliation(TeamActivityTestCase):
	# -- ac-2 -----------------------------------------------------------------------------------

	def test_every_summary_value_equals_its_records_total(self):
		self.seed()
		for member in (None, self.caller, self.coach):
			for metric in api.summary(FROM, TO, member)["metrics"]:
				records = api.records(metric["key"], FROM, TO, member, 1)
				self.assertEqual(metric["value"], records["total"], (metric["key"], member))

		self.assertEqual(
			values(api.summary(FROM, TO)),
			{"completed_calls": 4, "reached_contacts": 3, "agreed_meetings": 2},
		)
		self.assertEqual(
			values(api.summary(FROM, TO, self.caller)),
			{"completed_calls": 3, "reached_contacts": 2, "agreed_meetings": 1},
		)

	def test_per_member_values_sum_to_the_team(self):
		self.seed()
		team = values(api.summary(FROM, TO))
		breakdown = api.members(FROM, TO)
		self.assertEqual([m["key"] for m in breakdown["metrics"]], BUILT_INS)
		by_user = {member["user"]: member["values"] for member in breakdown["members"]}
		self.assertEqual(by_user[self.caller]["completed_calls"], 3)
		self.assertEqual(by_user[self.coach]["reached_contacts"], 1)
		for key in ("completed_calls", "reached_contacts"):
			self.assertEqual(sum(member[key] for member in by_user.values()), team[key], key)


class TestValidation(TeamActivityTestCase):
	# -- ac-3 -----------------------------------------------------------------------------------

	def test_bad_periods_metrics_pages_and_members_are_rejected(self):
		disabled = make_user("disabled", enabled=0)
		outsider = make_user("outsider", roles=())
		rejected = [
			lambda: api.summary("2031-02-30", TO),
			lambda: api.summary(FROM, "12/03/2031"),
			lambda: api.summary("", TO),
			lambda: api.summary(TO, FROM),
			lambda: api.records("no_such_metric", FROM, TO),
			lambda: api.records("completed_calls", FROM, TO, None, 0),
			lambda: api.records("completed_calls", FROM, TO, None, -1),
			lambda: api.summary(FROM, TO, disabled),
			lambda: api.summary(FROM, TO, outsider),
			lambda: api.records("completed_calls", FROM, TO, outsider, 1),
		]
		for index, call in enumerate(rejected):
			with self.assertRaises(frappe.ValidationError, msg=index):
				call()

	def test_an_empty_period_is_zero_everywhere(self):
		self.seed()
		self.assertEqual(values(api.summary("2031-01-01", "2031-01-01")), dict.fromkeys(BUILT_INS, 0))
		for key in BUILT_INS:
			records = api.records(key, "2031-01-01", "2031-01-01", self.caller, 1)
			self.assertEqual((records["total"], records["rows"]), (0, []))


class TestQualification(TeamActivityTestCase):
	# -- ac-4 -----------------------------------------------------------------------------------

	def test_voided_incoming_and_unfinished_calls_do_not_count(self):
		self.call(self.contact_a, "2031-03-10 09:00:00", type="Incoming", receiver=self.caller, caller=None)
		self.call(self.contact_a, "2031-03-10 10:00:00", status="No Answer")
		voided = self.call(self.contact_a, "2031-03-10 11:00:00")
		voided.status = "Failed"
		voided.save(ignore_permissions=True)

		self.assertEqual(
			values(api.summary(FROM, TO, self.caller)),
			{"completed_calls": 0, "reached_contacts": 0, "agreed_meetings": 0},
		)

	def test_coaching_rows_are_labelled_coaching(self):
		self.call(self.contact_a, "2031-03-10 09:00:00")
		coaching = self.ledger_event(self.caller, "2031-03-11 09:00:00", self.contact_a)

		rows = api.records("completed_calls", FROM, TO, self.caller, 1)["rows"]
		self.assertEqual([row["type"] for row in rows], ["Coaching", "Call"])
		self.assertEqual(rows[0]["source_link"], {"doctype": "FCRM Note", "name": coaching.source_name})

	def test_a_cancelled_booking_still_counts(self):
		event = self.meeting(self.caller, "2031-03-10 12:00:00", self.contact_a, "txb286")
		cancel_meeting_event(CONTACT_DOCTYPE, self.contact_a, "txb286")
		self.assertEqual(frappe.db.get_value(MEETING_DOCTYPE, event, "status"), "Cancelled")

		records = api.records("agreed_meetings", FROM, TO, self.caller, 1)
		self.assertEqual(records["total"], 1)
		row = records["rows"][0]
		self.assertEqual((row["name"], row["actor"], row["status"]), (event, self.caller, "Cancelled"))
		self.assertEqual(row["source_link"], {"doctype": MEETING_DOCTYPE, "name": event})


class TestReached(TeamActivityTestCase):
	# -- ac-5 -----------------------------------------------------------------------------------

	def test_one_row_per_actor_and_contact_at_its_latest_activity(self):
		first = self.call(self.contact_a, "2031-03-10 09:00:00")
		self.call(self.contact_a, "2031-03-10 11:00:00")
		self.call(self.contact_a, "2031-03-10 10:00:00", caller=self.coach)
		# Re-saving a source keeps its one ledger row: no extra call or activity.
		first.save(ignore_permissions=True)

		records = api.records("reached_contacts", FROM, TO, None, 1)
		self.assertEqual(records["total"], 2)
		self.assertEqual(
			[(r["actor"], r["contact"], str(r["occurred_at"]), r["activity_count"]) for r in records["rows"]],
			[
				(self.caller, self.contact_a, "2031-03-10 11:00:00", 2),
				(self.coach, self.contact_a, "2031-03-10 10:00:00", 1),
			],
		)
		self.assertEqual(
			records["rows"][0]["source_link"], {"doctype": CONTACT_DOCTYPE, "name": self.contact_a}
		)
		self.assertEqual(values(api.summary(FROM, TO, self.caller))["completed_calls"], 2)

	def test_reach_is_credited_to_the_actor_not_the_contact_owner(self):
		self.call(self.contact_a, "2031-03-10 09:00:00")
		self.assertEqual(values(api.summary(FROM, TO, self.caller))["reached_contacts"], 1)
		self.assertEqual(values(api.summary(FROM, TO, self.owner))["reached_contacts"], 0)


class TestPeriod(TeamActivityTestCase):
	# -- ac-6 -----------------------------------------------------------------------------------

	@patch("crm.txb.team_activity.get_system_timezone", return_value="America/New_York")
	def test_the_period_is_inclusive_in_the_site_timezone(self, _timezone):
		utc = ZoneInfo("UTC")
		scope = build_scope(FROM, TO)
		self.assertEqual(scope.from_utc, datetime(2031, 3, 10, 4, tzinfo=utc))
		self.assertEqual(scope.to_utc_exclusive, datetime(2031, 3, 13, 4, tzinfo=utc))

		# Stored Datetimes are site-local.
		self.call(self.contact_a, "2031-03-09 23:59:00")
		self.call(self.contact_a, "2031-03-10 00:00:00")
		self.call(self.contact_b, "2031-03-12 23:59:00")
		self.call(self.contact_b, "2031-03-13 00:00:00")

		summary = api.summary(FROM, TO, self.caller)
		self.assertEqual((summary["from_date"], summary["to_date"]), (FROM, TO))
		self.assertEqual(values(summary)["completed_calls"], 2)
		rows = api.records("completed_calls", FROM, TO, self.caller, 1)["rows"]
		self.assertEqual(
			[str(row["occurred_at"]) for row in rows], ["2031-03-12 23:59:00", "2031-03-10 00:00:00"]
		)


class TestPaging(TeamActivityTestCase):
	# -- ac-7 -----------------------------------------------------------------------------------

	def test_pages_hold_twenty_newest_first_under_the_full_total(self):
		for minute in range(23):
			self.ledger_event(self.caller, f"2031-03-11 09:{minute:02d}:00", channel="Call")

		first = api.records("completed_calls", FROM, TO, self.caller, 1)
		second = api.records("completed_calls", FROM, TO, self.caller, 2)
		self.assertEqual((first["total"], len(first["rows"]), first["page_length"]), (23, 20, 20))
		self.assertEqual((second["total"], len(second["rows"])), (23, 3))

		times = [str(row["occurred_at"]) for row in first["rows"] + second["rows"]]
		self.assertEqual(times, sorted(times, reverse=True))
		self.assertEqual(times[0], "2031-03-11 09:22:00")
		self.assertEqual(len(set(times)), 23)


class TestRegistry(TeamActivityTestCase):
	# -- ac-8 -----------------------------------------------------------------------------------

	def test_a_registered_metric_joins_the_summary_and_drill_down(self):
		def predicate(scope):
			event = frappe.qb.DocType(EVENT_DOCTYPE)
			return (
				(event.voided == 0)
				& (event.channel == "Email")
				& in_period(event.occurred_at, event.actor, scope)
			)

		def total(scope):
			event = frappe.qb.DocType(EVENT_DOCTYPE)
			return frappe.qb.from_(event).select(Count("*")).where(predicate(scope)).run()[0][0]

		def rows(scope, page, page_length=20):
			event = frappe.qb.DocType(EVENT_DOCTYPE)
			return (
				frappe.qb.from_(event)
				.select(event.name, event.occurred_at, event.source_name)
				.where(predicate(scope))
				.orderby(event.occurred_at, order=Order.desc)
				.limit(page_length)
				.offset((page - 1) * page_length)
			).run(as_dict=True)

		columns = [{"key": "occurred_at", "label": "Sent", "type": "datetime"}]
		emails = MetricDescriptor(
			key="test_emails",
			label="Emails",
			order=40,
			columns=columns,
			predicate=predicate,
			total=total,
			rows=rows,
			source_link=lambda row: {"doctype": "Communication", "name": row["source_name"]},
		)
		sent = self.ledger_event(
			self.caller, "2031-03-11 09:00:00", channel="Email", source_doctype="Communication"
		)

		with patch.dict(team_activity_metrics._REGISTRY):
			register(emails)
			with self.assertRaises(ValueError):
				register(emails)

			metrics = api.summary(FROM, TO)["metrics"]
			self.assertEqual([m["key"] for m in metrics], [*BUILT_INS, "test_emails"])
			self.assertEqual((metrics[3]["value"], metrics[3]["record_columns"]), (1, columns))

			records = api.records("test_emails", FROM, TO, self.caller, 1)
			self.assertEqual(records["total"], 1)
			self.assertEqual(
				records["rows"][0]["source_link"], {"doctype": "Communication", "name": sent.source_name}
			)

		with self.assertRaises(frappe.ValidationError):
			api.records("test_emails", FROM, TO)

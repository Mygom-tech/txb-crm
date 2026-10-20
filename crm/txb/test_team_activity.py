# Copyright (c) 2026, Mygom and Contributors
# See license.txt

"""TXB-286: admin-only Team Activity summary, members and records over a metric registry.

Drives the whitelisted endpoints over real Call Logs, Notes and meeting Events in a period no
other data reaches (March 2031). Runs under `bench run-tests` and under a plain
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
from crm.txb.api.actions import DISCOVERY_MEETING_FLOW
from crm.txb.constants import (
	FIELD_COACHING_CALL_DELIVERY_DATE,
	FIELD_COACHING_CALL_STATUS,
	FIELD_MEETING_KEY,
	OWNER_FIELDS,
	REACH_NOTE_TITLE,
)
from crm.txb.human_contact import EVENT_DOCTYPE, source_key
from crm.txb.meetings import LEAD_FOLLOW_UP_FLOW, cancel_meeting_event, meeting_key
from crm.txb.pipelines import individual_session, selling_training, workshop
from crm.txb.team_activity import build_scope
from crm.txb.team_activity_metrics import FLOW_STREAMS, MetricDescriptor, in_period, register

FROM, TO = "2031-03-10", "2031-03-12"
CALL_LOG_DOCTYPE = "CRM Call Log"
CONTACT_DOCTYPE = "Contact"
DEAL_DOCTYPE = "CRM Deal"
LEAD_DOCTYPE = "CRM Lead"
MEETING_DOCTYPE = "Event"
NOTE_DOCTYPE = "FCRM Note"
BUILT_INS = ["dials", "reaches", "meetings_set", "meetings_ahead"]
BAP = individual_session.MEETING_FLOW_BAP
VCS = workshop.MEETING_FLOW_VCS

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


def stamp(doctype, name, owner, creation):
	"""What an action run by `owner` at `creation` stores."""
	frappe.db.set_value(doctype, name, {"owner": owner, "creation": creation}, update_modified=False)


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
		"""An outgoing Call Log logged on `contact`."""
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

	def coaching_call(self, coach, delivered, status="Completed"):
		"""A Coaching Call Note row as Log Coaching Call writes it. Stored directly, skipping the
		Deal reconciliation hooks: the TXB Deal custom fields exist only on provisioned sites."""
		note = frappe.get_doc(
			{
				"doctype": NOTE_DOCTYPE,
				"title": "Coaching Call #1 - TXB286",
				"content": "notes",
				"reference_doctype": DEAL_DOCTYPE,
				"reference_docname": f"TXB286-{frappe.generate_hash(length=6)}",
				FIELD_COACHING_CALL_STATUS: status,
				FIELD_COACHING_CALL_DELIVERY_DATE: delivered,
			}
		)
		note.set_new_name()
		note.db_insert()
		stamp(NOTE_DOCTYPE, note.name, coach, f"{delivered} 18:00:00")
		return note

	def reach(self, actor, at, first_name="Rita"):
		"""The Note a Log a reach writes on a Lead."""
		lead = frappe.get_doc(
			{
				"doctype": LEAD_DOCTYPE,
				"first_name": first_name,
				"last_name": "TXB286",
				"email": f"{frappe.generate_hash(length=8)}@example.com",
				"status": "New",
			}
		).insert(ignore_permissions=True)
		note = frappe.get_doc(
			{
				"doctype": NOTE_DOCTYPE,
				"title": REACH_NOTE_TITLE,
				"content": "reached",
				"reference_doctype": LEAD_DOCTYPE,
				"reference_docname": lead.name,
			}
		).insert(ignore_permissions=True)
		stamp(NOTE_DOCTYPE, note.name, actor, at)
		return note

	def meeting(self, booked_by, booked_at, contact, flow, starts_on="2031-04-01 10:00:00"):
		event = frappe.get_doc(
			{
				"doctype": MEETING_DOCTYPE,
				"subject": "Discovery call — Ann TXB286",
				"starts_on": starts_on,
				"event_type": "Private",
				"event_category": "Meeting",
				"reference_doctype": CONTACT_DOCTYPE,
				"reference_docname": contact,
				FIELD_MEETING_KEY: meeting_key(CONTACT_DOCTYPE, contact, flow),
			}
		).insert(ignore_permissions=True)
		stamp(MEETING_DOCTYPE, event.name, booked_by, booked_at)
		return event.name

	def seed(self):
		"""caller: 2 completed calls and 1 unanswered, 1 logged reach, a discovery booked and a BAP
		booked then cancelled; coach: 1 completed and 1 missed coaching call, a VCS call and a
		follow-up booked."""
		self.call(self.contact_a, "2031-03-10 09:00:00")
		self.call(self.contact_a, "2031-03-11 09:00:00", status="No Answer")
		self.call(self.contact_b, "2031-03-11 10:00:00")
		self.reach(self.caller, "2031-03-12 11:00:00")
		self.coaching_call(self.coach, "2031-03-12")
		self.coaching_call(self.coach, "2031-03-11", status="Missed")
		self.meeting(self.caller, "2031-03-10 12:00:00", self.contact_a, DISCOVERY_MEETING_FLOW)
		self.meeting(self.caller, "2031-03-10 13:00:00", self.contact_b, BAP)
		cancel_meeting_event(CONTACT_DOCTYPE, self.contact_b, BAP)
		self.meeting(self.coach, "2031-03-11 12:00:00", self.contact_b, VCS, "2031-04-02 09:00:00")
		self.meeting(self.coach, "2031-03-11 13:00:00", self.contact_a, LEAD_FOLLOW_UP_FLOW)


class TestAccess(TeamActivityTestCase):
	def test_sales_roles_are_refused_before_any_metric_runs(self):
		spy = MetricDescriptor(
			key="spy",
			label="Spy",
			order=99,
			columns=[],
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

		spy.total.assert_not_called()
		spy.rows.assert_not_called()

	def test_admins_are_admitted(self):
		for user in ("Administrator", make_user("admin", roles=("System Manager",))):
			frappe.set_user(user)
			self.assertEqual([m["key"] for m in api.summary(FROM, TO)["metrics"]], BUILT_INS)


class TestReconciliation(TeamActivityTestCase):
	def test_every_summary_value_and_group_equals_its_records_total(self):
		self.seed()
		for member in (None, self.caller, self.coach):
			for metric in api.summary(FROM, TO, member)["metrics"]:
				records = api.records(metric["key"], FROM, TO, member, 1)
				self.assertEqual(metric["value"], records["total"], (metric["key"], member))
				for group in metric["groups"]:
					grouped = api.records(metric["key"], FROM, TO, member, 1, group["key"])
					self.assertEqual(group["value"], grouped["total"], (metric["key"], group["key"]))

		self.assertEqual(
			values(api.summary(FROM, TO)),
			{"dials": 5, "reaches": 4, "meetings_set": 2, "meetings_ahead": 2},
		)
		self.assertEqual(
			values(api.summary(FROM, TO, self.caller)),
			{"dials": 3, "reaches": 3, "meetings_set": 1, "meetings_ahead": 1},
		)

	def test_per_member_values_sum_to_the_team(self):
		self.seed()
		team = values(api.summary(FROM, TO))
		breakdown = api.members(FROM, TO)
		self.assertEqual([m["key"] for m in breakdown["metrics"]], BUILT_INS)
		by_user = {member["user"]: member["values"] for member in breakdown["members"]}
		self.assertEqual(by_user[self.coach]["dials"], 2)
		for key in BUILT_INS:
			self.assertEqual(sum(member[key] for member in by_user.values()), team[key], key)


class TestValidation(TeamActivityTestCase):
	def test_bad_periods_metrics_groups_pages_and_members_are_rejected(self):
		disabled = make_user("disabled", enabled=0)
		outsider = make_user("outsider", roles=())
		rejected = [
			lambda: api.summary("2031-02-30", TO),
			lambda: api.summary(FROM, "12/03/2031"),
			lambda: api.summary("", TO),
			lambda: api.summary(TO, FROM),
			lambda: api.records("no_such_metric", FROM, TO),
			lambda: api.records("dials", FROM, TO, None, 0),
			lambda: api.records("dials", FROM, TO, None, -1),
			lambda: api.records("dials", FROM, TO, None, 1, "discovery"),
			lambda: api.records("meetings_set", FROM, TO, None, 1, "no_such_group"),
			lambda: api.summary(FROM, TO, disabled),
			lambda: api.summary(FROM, TO, outsider),
			lambda: api.records("dials", FROM, TO, outsider, 1),
		]
		for index, call in enumerate(rejected):
			with self.assertRaises(frappe.ValidationError, msg=index):
				call()

	def test_an_empty_period_is_zero_except_meetings_ahead(self):
		self.seed()
		empty = values(api.summary("2031-01-01", "2031-01-01"))
		self.assertEqual(empty, {"dials": 0, "reaches": 0, "meetings_set": 0, "meetings_ahead": 2})
		for key in ("dials", "reaches", "meetings_set"):
			records = api.records(key, "2031-01-01", "2031-01-01", self.caller, 1)
			self.assertEqual((records["total"], records["rows"]), (0, []))


class TestCalls(TeamActivityTestCase):
	def test_every_outgoing_attempt_is_a_dial_and_only_completed_ones_reach(self):
		self.call(self.contact_a, "2031-03-10 09:00:00", type="Incoming", receiver=self.caller, caller=None)
		self.call(self.contact_a, "2031-03-10 10:00:00", status="No Answer")
		self.call(self.contact_a, "2031-03-10 11:00:00", status="Busy")
		self.call(self.contact_a, "2031-03-10 12:00:00")

		self.assertEqual(
			values(api.summary(FROM, TO, self.caller)),
			{"dials": 3, "reaches": 1, "meetings_set": 0, "meetings_ahead": 0},
		)

	def test_rows_name_the_customer_and_label_their_type(self):
		self.call(self.contact_a, "2031-03-10 09:00:00", status="No Answer")
		self.reach(self.caller, "2031-03-11 09:00:00", first_name="Rita")

		rows = api.records("reaches", FROM, TO, self.caller, 1)["rows"]
		self.assertEqual([(r["type"], r["person"]) for r in rows], [("Reach", "Rita TXB286")])
		dial = api.records("dials", FROM, TO, self.caller, 1)["rows"][0]
		self.assertEqual((dial["type"], dial["outcome"], dial["person"]), ("Call", "No Answer", "Ann TXB286"))
		self.assertEqual(dial["source_link"]["doctype"], CALL_LOG_DOCTYPE)

	def test_coaching_calls_are_dials_whatever_their_status_credited_to_the_coach(self):
		completed = self.coaching_call(self.coach, "2031-03-11")
		self.coaching_call(self.coach, "2031-03-12", status="No charge")

		self.assertEqual(values(api.summary(FROM, TO, self.coach))["dials"], 2)
		rows = api.records("reaches", FROM, TO, self.coach, 1)["rows"]
		self.assertEqual([(r["type"], r["source_name"]) for r in rows], [("Coaching", completed.name)])
		self.assertEqual(values(api.summary(FROM, TO, self.owner))["dials"], 0)


class TestMeetings(TeamActivityTestCase):
	def test_cancelled_meetings_and_follow_ups_are_left_out(self):
		self.meeting(self.caller, "2031-03-10 12:00:00", self.contact_a, BAP)
		cancel_meeting_event(CONTACT_DOCTYPE, self.contact_a, BAP)
		self.meeting(self.caller, "2031-03-10 13:00:00", self.contact_a, LEAD_FOLLOW_UP_FLOW)
		self.meeting(self.caller, "2031-03-10 14:00:00", self.contact_a, "not_a_stream")

		summary = values(api.summary(FROM, TO, self.caller))
		self.assertEqual((summary["meetings_set"], summary["meetings_ahead"]), (0, 0))

	def test_meetings_set_counts_the_booking_day_and_groups_by_stream(self):
		event = self.meeting(self.caller, "2031-03-10 12:00:00", self.contact_a, BAP, "2031-05-01 10:00:00")
		self.meeting(self.caller, "2031-03-11 12:00:00", self.contact_b, VCS)

		metric = next(m for m in api.summary(FROM, TO)["metrics"] if m["key"] == "meetings_set")
		self.assertEqual(metric["value"], 2)
		self.assertEqual(
			{g["key"]: g["value"] for g in metric["groups"]},
			{"discovery": 0, "individual_sessions": 1, "vcs_workshops": 1},
		)
		self.assertTrue(all(g["description"] for g in metric["groups"]))

		row = api.records("meetings_set", FROM, TO, None, 1, "individual_sessions")["rows"][0]
		self.assertEqual(
			(row["name"], row["person"], row["subject"], row["stream"], row["actor"]),
			(event, "Ann TXB286", "Discovery call", "Individual sessions", self.caller),
		)

	def test_meetings_ahead_are_upcoming_open_meetings_soonest_first(self):
		later = self.meeting(self.caller, "2031-03-10 12:00:00", self.contact_a, BAP, "2031-04-09 10:00:00")
		sooner = self.meeting(self.coach, "2020-01-01 12:00:00", self.contact_b, VCS, "2031-04-08 10:00:00")
		self.meeting(
			self.caller, "2031-03-10 12:00:00", self.contact_b, DISCOVERY_MEETING_FLOW, "2020-01-01 10:00:00"
		)

		rows = api.records("meetings_ahead", "2031-01-01", "2031-01-01", None, 1)["rows"]
		self.assertEqual([r["name"] for r in rows], [sooner, later])
		self.assertEqual(values(api.summary(FROM, TO, self.coach))["meetings_ahead"], 1)

	def test_every_meeting_flow_but_follow_ups_has_a_stream(self):
		flows = {
			DISCOVERY_MEETING_FLOW,
			individual_session.MEETING_FLOW_BAP,
			workshop.MEETING_FLOW_VCS,
			workshop.MEETING_FLOW_WORKSHOP,
			selling_training.MEETING_FLOW_DISCOVERY,
			selling_training.MEETING_FLOW_PROPOSAL,
			selling_training.MEETING_FLOW_TRAINING,
		}
		self.assertEqual(set(FLOW_STREAMS), flows)
		self.assertNotIn(LEAD_FOLLOW_UP_FLOW, FLOW_STREAMS)


class TestPeriod(TeamActivityTestCase):
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
		self.assertEqual(values(summary)["dials"], 2)
		rows = api.records("dials", FROM, TO, self.caller, 1)["rows"]
		self.assertEqual(
			[str(row["occurred_at"]) for row in rows], ["2031-03-12 23:59:00", "2031-03-10 00:00:00"]
		)


class TestPaging(TeamActivityTestCase):
	def test_pages_hold_twenty_newest_first_under_the_full_total(self):
		for minute in range(21):
			self.call(self.contact_a, f"2031-03-11 09:{minute:02d}:00")
		self.coaching_call(self.caller, "2031-03-10")
		self.reach(self.caller, "2031-03-12 09:00:00")

		first = api.records("reaches", FROM, TO, self.caller, 1)
		second = api.records("reaches", FROM, TO, self.caller, 2)
		self.assertEqual((first["total"], len(first["rows"]), first["page_length"]), (23, 20, 20))
		self.assertEqual((second["total"], len(second["rows"])), (23, 3))

		times = [str(row["occurred_at"]) for row in first["rows"] + second["rows"]]
		self.assertEqual(times, sorted(times, reverse=True))
		self.assertEqual(first["rows"][0]["type"], "Reach")
		self.assertEqual(second["rows"][-1]["type"], "Coaching")


class TestRegistry(TeamActivityTestCase):
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
			order=50,
			columns=columns,
			total=total,
			rows=rows,
			source_link=lambda row: {"doctype": "Communication", "name": row["source_name"]},
		)
		source_name = frappe.generate_hash(length=10)
		sent = frappe.get_doc(
			{
				"doctype": EVENT_DOCTYPE,
				"source_key": source_key("Communication", source_name),
				"source_doctype": "Communication",
				"source_name": source_name,
				"channel": "Email",
				"provenance": "Human",
				"actor": self.caller,
				"occurred_at": "2031-03-11 09:00:00",
				"recipients": "[]",
				"voided": 0,
			}
		)
		sent.flags.ignore_links = True
		sent.insert(ignore_permissions=True)

		with patch.dict(team_activity_metrics._REGISTRY):
			register(emails)
			with self.assertRaises(ValueError):
				register(emails)

			metrics = api.summary(FROM, TO)["metrics"]
			self.assertEqual([m["key"] for m in metrics], [*BUILT_INS, "test_emails"])
			self.assertEqual((metrics[4]["value"], metrics[4]["record_columns"]), (1, columns))

			records = api.records("test_emails", FROM, TO, self.caller, 1)
			self.assertEqual(records["total"], 1)
			self.assertEqual(
				records["rows"][0]["source_link"], {"doctype": "Communication", "name": source_name}
			)

		with self.assertRaises(frappe.ValidationError):
			api.records("test_emails", FROM, TO)

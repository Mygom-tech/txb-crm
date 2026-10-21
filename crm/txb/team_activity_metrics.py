"""Team Activity metrics (TXB-286).

Each metric is one MetricDescriptor whose `total` (a full SQL COUNT) and `rows` (one page of
auditable source records) are built from the same source queries, so a summary figure always
reconciles with its drill-down. A new metric is one `register()` call; the Team Activity API reads
the registry and needs no change.

Every built-in credits the recorded actor -- whoever made the call, logged the reach or booked the
meeting -- never the record's current owner.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.query_builder import Field, Order
from frappe.query_builder.functions import Coalesce, Count, NullIf
from frappe.utils import now_datetime
from pypika.terms import ValueWrapper

from crm.txb.api.actions import DISCOVERY_MEETING_FLOW
from crm.txb.coaching_calls import STATUS_COMPLETED as COACHING_COMPLETED
from crm.txb.constants import (
	FIELD_COACHING_CALL_DELIVERY_DATE,
	FIELD_COACHING_CALL_STATUS,
	FIELD_MEETING_KEY,
	REACH_NOTE_TITLE,
)
from crm.txb.human_contact import (
	CALL_COMPLETED,
	CALL_LOG_DOCTYPE,
	CALL_OUTGOING,
	CHANNEL_CALL,
	DEAL_DOCTYPE,
	MEETING_DOCTYPE,
	NOTE_DOCTYPE,
)
from crm.txb.meetings import (
	LEAD_DOCTYPE,
	STATUS_CANCELLED,
	STATUS_OPEN,
	SUBJECT_NAME_SEPARATOR,
	meeting_flow,
)
from crm.txb.pipelines.individual_session import MEETING_FLOW_BAP
from crm.txb.pipelines.selling_training import (
	MEETING_FLOW_DISCOVERY,
	MEETING_FLOW_PROPOSAL,
	MEETING_FLOW_TRAINING,
)
from crm.txb.pipelines.workshop import MEETING_FLOW_VCS, MEETING_FLOW_WORKSHOP

if TYPE_CHECKING:
	from pypika.terms import Criterion

	from crm.txb.team_activity import Scope

PAGE_LENGTH = 20

CALL_RESULT_OTHER = "Other"
TYPE_COACHING = "Coaching"
TYPE_REACH = "Reach"

# Meeting streams, in display order. A booked meeting counts only when its flow is listed here:
# Lead follow-ups (`lead_follow_up`) are callbacks, counted as Dials/Reaches once they are logged.
STREAMS = (
	{
		"key": "discovery",
		"label": "Discovery meetings",
		"description": "Lead discovery meetings and Selling Training discovery meetings.",
		"flows": (DISCOVERY_MEETING_FLOW, MEETING_FLOW_DISCOVERY),
	},
	{
		"key": "individual_sessions",
		"label": "Individual sessions",
		"description": "BAP sessions booked in the Individual Session pipeline.",
		"flows": (MEETING_FLOW_BAP,),
	},
	{
		"key": "vcs_workshops",
		"label": "VCS / Workshops",
		"description": "VCS calls and workshops, plus Selling Training proposal and training meetings.",
		"flows": (MEETING_FLOW_VCS, MEETING_FLOW_WORKSHOP, MEETING_FLOW_PROPOSAL, MEETING_FLOW_TRAINING),
	},
)
FLOW_STREAMS = {flow: stream for stream in STREAMS for flow in stream["flows"]}

PERSON_COLUMN = {"key": "person", "label": "Lead / Contact", "type": "person"}


@dataclass(frozen=True)
class MetricDescriptor:
	"""One Team Activity metric.

	`columns` describe the record rows: `{key, label, type}` with type one of datetime, link,
	text, int, badge or person. `source_link(row)` names the document a row drills down to, or
	None. `groups` (`{key, label, description}`) split the metric: `scope.group` narrows it to one.
	`ignores_period` marks a metric that counts from now on, whatever the chosen period.
	"""

	key: str
	label: str
	order: int
	columns: list[dict]
	total: Callable[[Scope], int]
	rows: Callable[..., list[dict]]
	source_link: Callable[[dict], dict | None] = lambda row: None
	description: str = ""
	groups: tuple[dict, ...] = ()
	ignores_period: bool = False


_REGISTRY: dict[str, MetricDescriptor] = {}


def register(descriptor: MetricDescriptor) -> MetricDescriptor:
	if descriptor.key in _REGISTRY:
		raise ValueError(f"Team Activity metric {descriptor.key!r} is already registered")
	_REGISTRY[descriptor.key] = descriptor
	return descriptor


def get_metric(key: str) -> MetricDescriptor:
	descriptor = _REGISTRY.get(key) if isinstance(key, str) else None
	if descriptor is None:
		frappe.throw(_("Unknown Team Activity metric: {0}").format(key), frappe.ValidationError)
	return descriptor


def list_metrics() -> list[MetricDescriptor]:
	return sorted(_REGISTRY.values(), key=lambda descriptor: descriptor.order)


def by_member(actor, scope: Scope) -> Criterion:
	"""In member mode, `actor` is the member; otherwise matches every row."""
	return actor == scope.member if scope.member else actor.isnotnull()


def in_period(timestamp, actor, scope: Scope) -> Criterion:
	"""`timestamp` falls in the scope's period and, in member mode, `actor` is the member."""
	start, end = scope.stored_bounds()
	return (timestamp >= start) & (timestamp < end) & by_member(actor, scope)


def _count(query) -> int:
	return query.select(Count("*")).run()[0][0]


def _page(query, page: int, page_length: int):
	return query.limit(page_length).offset((page - 1) * page_length)


# -- Dials and Reaches: logged calls, coaching calls and reaches, by whoever logged them ---------
#
# Each source is one SELECT with the same columns, so a metric's total is the sum of its sources'
# counts and its rows are their UNION ALL, newest first.


def _source(scope: Scope, table, doctype: str, occurred_at, actor, type_: str, outcome, criterion):
	"""One Dials/Reaches source: the shared column list every UNION ALL branch must match."""
	return (
		frappe.qb.from_(table)
		.select(
			ValueWrapper(doctype).as_("source_doctype"),
			table.name.as_("source_name"),
			occurred_at.as_("occurred_at"),
			actor.as_("actor"),
			ValueWrapper(type_).as_("type"),
			outcome.as_("outcome"),
			table.reference_doctype,
			table.reference_docname.as_("reference_name"),
		)
		.where(criterion & in_period(occurred_at, actor, scope))
	)


def _call_logs(scope: Scope, completed_only: bool):
	# Actor and time as `human_contact._actor`/`_occurred_at` read a Call Log, in SQL.
	call = frappe.qb.DocType(CALL_LOG_DOCTYPE)
	# An "Other" result is kept on record but counts nowhere, not even as a dial.
	criterion = (call.type == CALL_OUTGOING) & (call.status != CALL_RESULT_OTHER)
	if completed_only:
		criterion &= call.status == CALL_COMPLETED
	return _source(
		scope,
		call,
		CALL_LOG_DOCTYPE,
		Coalesce(call.end_time, call.start_time, call.creation),
		Coalesce(NullIf(call.caller, ""), call.owner),
		CHANNEL_CALL,
		call.status,
		criterion,
	)


def _coaching_calls(scope: Scope, completed_only: bool):
	"""Coaching Call Notes: Log Coaching Call writes their call status, and the TXB-247 patch
	backfilled it on recognizable legacy notes."""
	note = frappe.qb.DocType(NOTE_DOCTYPE)
	status = note[FIELD_COACHING_CALL_STATUS]
	criterion = note.reference_doctype == DEAL_DOCTYPE
	criterion &= (status == COACHING_COMPLETED) if completed_only else NullIf(status, "").isnotnull()
	return _source(
		scope,
		note,
		NOTE_DOCTYPE,
		Coalesce(note[FIELD_COACHING_CALL_DELIVERY_DATE], note.creation),
		note.owner,
		TYPE_COACHING,
		status,
		criterion,
	)


def _logged_reaches(scope: Scope, _completed_only: bool):
	"""Log a reach Notes, always a completed reach; matched by their untranslated title."""
	note = frappe.qb.DocType(NOTE_DOCTYPE)
	return _source(
		scope,
		note,
		NOTE_DOCTYPE,
		note.creation,
		note.owner,
		TYPE_REACH,
		ValueWrapper(CALL_COMPLETED),
		(note.title == REACH_NOTE_TITLE) & (note.reference_doctype == LEAD_DOCTYPE),
	)


def _union_metric(*sources, completed_only: bool):
	def total(scope: Scope) -> int:
		return sum(_count(frappe.qb.from_(source(scope, completed_only))) for source in sources)

	def rows(scope: Scope, page: int, page_length: int = PAGE_LENGTH) -> list[dict]:
		query = sources[0](scope, completed_only)
		for source in sources[1:]:
			query = query.union_all(source(scope, completed_only))
		query = query.orderby(Field("occurred_at"), order=Order.desc).orderby(
			Field("source_name"), order=Order.desc
		)
		return _page(query, page, page_length).run(as_dict=True)

	return total, rows


_dials_total, _dials_rows = _union_metric(_call_logs, _coaching_calls, completed_only=False)
_reaches_total, _reaches_rows = _union_metric(
	_call_logs, _coaching_calls, _logged_reaches, completed_only=True
)

CALL_COLUMNS = [
	{"key": "occurred_at", "label": "When", "type": "datetime"},
	PERSON_COLUMN,
	{"key": "outcome", "label": "Outcome", "type": "badge"},
	{"key": "type", "label": "Type", "type": "badge"},
	{"key": "actor", "label": "By", "type": "link"},
]


def _source_link(row: dict) -> dict:
	return {"doctype": row["source_doctype"], "name": row["source_name"]}


# -- Meetings: booked meeting Events in a stream, cancelled ones excluded --------------------------


def _flow_criterion(meeting, flows) -> Criterion:
	"""The meeting key's flow segment is one of `flows` (`<doctype>:<name>:<flow>[:<submission>]`)."""
	criterion = None
	for flow in flows:
		escaped = flow.replace("_", "\\_")
		match = meeting[FIELD_MEETING_KEY].like(f"%:{escaped}") | meeting[FIELD_MEETING_KEY].like(
			f"%:{escaped}:%"
		)
		criterion = match if criterion is None else criterion | match
	return criterion


def _meetings_base(scope: Scope) -> Criterion:
	meeting = frappe.qb.DocType(MEETING_DOCTYPE)
	streams = [s for s in STREAMS if s["key"] == scope.group] if scope.group else STREAMS
	flows = [flow for stream in streams for flow in stream["flows"]]
	return (meeting.status != STATUS_CANCELLED) & _flow_criterion(meeting, flows)


def _meetings_set_predicate(scope: Scope) -> Criterion:
	meeting = frappe.qb.DocType(MEETING_DOCTYPE)
	return _meetings_base(scope) & in_period(meeting.creation, meeting.owner, scope)


def _meetings_ahead_predicate(scope: Scope) -> Criterion:
	meeting = frappe.qb.DocType(MEETING_DOCTYPE)
	return (
		_meetings_base(scope)
		& (meeting.status == STATUS_OPEN)
		& (meeting.starts_on >= now_datetime())
		& by_member(meeting.owner, scope)
	)


def _meeting_metric(predicate, order_by: str, order: Order):
	def total(scope: Scope) -> int:
		meeting = frappe.qb.DocType(MEETING_DOCTYPE)
		return _count(frappe.qb.from_(meeting).where(predicate(scope)))

	def rows(scope: Scope, page: int, page_length: int = PAGE_LENGTH) -> list[dict]:
		meeting = frappe.qb.DocType(MEETING_DOCTYPE)
		query = (
			frappe.qb.from_(meeting)
			.select(
				meeting.name,
				meeting.creation.as_("booked_at"),
				meeting.owner.as_("actor"),
				meeting.subject,
				meeting.starts_on,
				meeting[FIELD_MEETING_KEY].as_("meeting_key"),
				meeting.reference_doctype,
				meeting.reference_docname.as_("reference_name"),
			)
			.where(predicate(scope))
			.orderby(meeting[order_by], order=order)
			.orderby(meeting.name, order=order)
		)
		result = _page(query, page, page_length).run(as_dict=True)
		for row in result:
			# The generated subject ends with the customer name, which has its own column.
			row["subject"] = (row.subject or "").split(SUBJECT_NAME_SEPARATOR)[0]
			stream = FLOW_STREAMS.get(meeting_flow(row.pop("meeting_key")))
			row["stream"] = _(stream["label"]) if stream else None
		return result

	return total, rows


_set_total, _set_rows = _meeting_metric(_meetings_set_predicate, "creation", Order.desc)
_ahead_total, _ahead_rows = _meeting_metric(_meetings_ahead_predicate, "starts_on", Order.asc)

MEETING_GROUPS = tuple({key: stream[key] for key in ("key", "label", "description")} for stream in STREAMS)


MEETING_COLUMNS = [
	PERSON_COLUMN,
	{"key": "subject", "label": "Meeting", "type": "text"},
	{"key": "stream", "label": "Stream", "type": "badge"},
	{"key": "starts_on", "label": "Scheduled for", "type": "datetime"},
	{"key": "actor", "label": "Booked by", "type": "link"},
]


def _meeting_link(row: dict) -> dict:
	return {"doctype": MEETING_DOCTYPE, "name": row["name"]}


register(
	MetricDescriptor(
		key="dials",
		label="Dials",
		order=10,
		description="Every logged outgoing call and coaching call, whatever its outcome.",
		columns=CALL_COLUMNS,
		total=_dials_total,
		rows=_dials_rows,
		source_link=_source_link,
	)
)

register(
	MetricDescriptor(
		key="reaches",
		label="Reaches",
		order=20,
		description="Completed calls and coaching calls, plus every Log a reach.",
		columns=CALL_COLUMNS,
		total=_reaches_total,
		rows=_reaches_rows,
		source_link=_source_link,
	)
)

register(
	MetricDescriptor(
		key="meetings_set",
		label="Meetings set",
		order=30,
		description="Meetings booked in the period, whenever they take place. Cancelled ones are left out.",
		columns=MEETING_COLUMNS,
		total=_set_total,
		rows=_set_rows,
		source_link=_meeting_link,
		groups=MEETING_GROUPS,
	)
)

register(
	MetricDescriptor(
		key="meetings_ahead",
		label="Meetings ahead",
		order=40,
		description="Every upcoming meeting that has not started yet, whatever the period.",
		columns=MEETING_COLUMNS,
		total=_ahead_total,
		rows=_ahead_rows,
		source_link=_meeting_link,
		groups=MEETING_GROUPS,
		ignores_period=True,
	)
)

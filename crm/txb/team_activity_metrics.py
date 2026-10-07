"""Team Activity metrics (TXB-286).

Each metric is one MetricDescriptor: its `predicate` is the single definition of what counts, and
both its `total` (a full SQL COUNT) and its `rows` (one page of auditable source records) are built
from that same predicate, so a summary figure always reconciles with its drill-down. A new metric
is one `register()` call; the Team Activity API reads the registry and needs no change.

Every built-in credits the recorded actor -- whoever made the call, made the contact or booked the
meeting -- never the record's current owner.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.query_builder import Order
from frappe.query_builder.functions import Count, Max

from crm.txb.constants import FIELD_MEETING_KEY
from crm.txb.contact_attribution import ATTRIBUTION_DOCTYPE
from crm.txb.human_contact import (
	CHANNEL_CALL,
	CHANNEL_COACHING_CALL,
	EVENT_DOCTYPE,
	MEETING_DOCTYPE,
)

if TYPE_CHECKING:
	from pypika.terms import Criterion

	from crm.txb.team_activity import Scope

PAGE_LENGTH = 20

# How a call row reads in the records table: a logged coaching call is labelled "Coaching".
CALL_TYPES = {CHANNEL_CALL: "Call", CHANNEL_COACHING_CALL: "Coaching"}


@dataclass(frozen=True)
class MetricDescriptor:
	"""One Team Activity metric.

	`columns` describe the record rows: `{key, label, type}` with type one of datetime, link,
	text, int or badge. `source_link(row)` names the document a row drills down to, or None.
	"""

	key: str
	label: str
	order: int
	columns: list[dict]
	predicate: Callable[[Scope], Criterion]
	total: Callable[[Scope], int]
	rows: Callable[..., list[dict]]
	source_link: Callable[[dict], dict | None] = lambda row: None


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


def in_period(timestamp, actor, scope: Scope) -> Criterion:
	"""`timestamp` falls in the scope's period and, in member mode, `actor` is the member."""
	start, end = scope.stored_bounds()
	criterion = (timestamp >= start) & (timestamp < end)
	if scope.member:
		criterion &= actor == scope.member
	return criterion


def _count(query) -> int:
	return query.select(Count("*")).run()[0][0]


def _page(query, page: int, page_length: int):
	return query.limit(page_length).offset((page - 1) * page_length)


# -- Completed calls: completed outgoing calls and logged coaching calls, by their caller ---------


def _calls_predicate(scope: Scope) -> Criterion:
	event = frappe.qb.DocType(EVENT_DOCTYPE)
	return (
		(event.voided == 0)
		& event.channel.isin(list(CALL_TYPES))
		& in_period(event.occurred_at, event.actor, scope)
	)


def _calls_total(scope: Scope) -> int:
	event = frappe.qb.DocType(EVENT_DOCTYPE)
	return _count(frappe.qb.from_(event).where(_calls_predicate(scope)))


def _calls_rows(scope: Scope, page: int, page_length: int = PAGE_LENGTH) -> list[dict]:
	event = frappe.qb.DocType(EVENT_DOCTYPE)
	query = (
		frappe.qb.from_(event)
		.select(
			event.name,
			event.occurred_at,
			event.actor,
			event.channel,
			event.reference_doctype,
			event.reference_name,
			event.source_doctype,
			event.source_name,
		)
		.where(_calls_predicate(scope))
		.orderby(event.occurred_at, order=Order.desc)
		.orderby(event.name, order=Order.desc)
	)
	rows = _page(query, page, page_length).run(as_dict=True)
	for row in rows:
		row["type"] = CALL_TYPES[row.channel]
	return rows


# -- Reached Contacts: one row per actor and Contact, at its latest human contact ---------------


def _reached_predicate(scope: Scope) -> Criterion:
	event = frappe.qb.DocType(EVENT_DOCTYPE)
	return (event.voided == 0) & in_period(event.occurred_at, event.actor, scope)


def _reached_pairs(scope: Scope):
	event = frappe.qb.DocType(EVENT_DOCTYPE)
	attribution = frappe.qb.DocType(ATTRIBUTION_DOCTYPE)
	return (
		frappe.qb.from_(attribution)
		.join(event)
		.on(event.name == attribution.event)
		.select(event.actor, attribution.contact)
		.where(_reached_predicate(scope))
		.groupby(event.actor, attribution.contact)
	)


def _reached_total(scope: Scope) -> int:
	return _count(frappe.qb.from_(_reached_pairs(scope)))


def _reached_rows(scope: Scope, page: int, page_length: int = PAGE_LENGTH) -> list[dict]:
	event = frappe.qb.DocType(EVENT_DOCTYPE)
	attribution = frappe.qb.DocType(ATTRIBUTION_DOCTYPE)
	latest = Max(event.occurred_at)
	query = (
		_reached_pairs(scope)
		.select(latest.as_("occurred_at"), Count("*").as_("activity_count"))
		.orderby(latest, order=Order.desc)
		.orderby(event.actor)
		.orderby(attribution.contact)
	)
	return _page(query, page, page_length).run(as_dict=True)


# -- Agreed meetings: every booked meeting Event, by whoever booked it, even if later cancelled ---


def _meetings_predicate(scope: Scope) -> Criterion:
	meeting = frappe.qb.DocType(MEETING_DOCTYPE)
	# Only Events the scheduling flows booked carry a meeting key (NULL never matches).
	return (meeting[FIELD_MEETING_KEY] != "") & in_period(meeting.creation, meeting.owner, scope)


def _meetings_total(scope: Scope) -> int:
	meeting = frappe.qb.DocType(MEETING_DOCTYPE)
	return _count(frappe.qb.from_(meeting).where(_meetings_predicate(scope)))


def _meetings_rows(scope: Scope, page: int, page_length: int = PAGE_LENGTH) -> list[dict]:
	meeting = frappe.qb.DocType(MEETING_DOCTYPE)
	query = (
		frappe.qb.from_(meeting)
		.select(
			meeting.name,
			meeting.creation.as_("booked_at"),
			meeting.owner.as_("actor"),
			meeting.subject,
			meeting.starts_on,
			meeting.status,
			meeting.reference_doctype,
			meeting.reference_docname.as_("reference_name"),
		)
		.where(_meetings_predicate(scope))
		.orderby(meeting.creation, order=Order.desc)
		.orderby(meeting.name, order=Order.desc)
	)
	return _page(query, page, page_length).run(as_dict=True)


register(
	MetricDescriptor(
		key="completed_calls",
		label="Completed calls",
		order=10,
		columns=[
			{"key": "occurred_at", "label": "When", "type": "datetime"},
			{"key": "actor", "label": "By", "type": "link"},
			{"key": "type", "label": "Type", "type": "badge"},
			{"key": "reference_name", "label": "Record", "type": "link"},
		],
		predicate=_calls_predicate,
		total=_calls_total,
		rows=_calls_rows,
		source_link=lambda row: {"doctype": row["source_doctype"], "name": row["source_name"]},
	)
)

register(
	MetricDescriptor(
		key="reached_contacts",
		label="Reached Contacts",
		order=20,
		columns=[
			{"key": "occurred_at", "label": "Latest activity", "type": "datetime"},
			{"key": "actor", "label": "By", "type": "link"},
			{"key": "contact", "label": "Contact", "type": "link"},
			{"key": "activity_count", "label": "Activities", "type": "int"},
		],
		predicate=_reached_predicate,
		total=_reached_total,
		rows=_reached_rows,
		source_link=lambda row: {"doctype": "Contact", "name": row["contact"]},
	)
)

register(
	MetricDescriptor(
		key="agreed_meetings",
		label="Agreed meetings",
		order=30,
		columns=[
			{"key": "booked_at", "label": "Booked", "type": "datetime"},
			{"key": "actor", "label": "Booked by", "type": "link"},
			{"key": "subject", "label": "Meeting", "type": "text"},
			{"key": "starts_on", "label": "Scheduled for", "type": "datetime"},
			{"key": "status", "label": "Status", "type": "badge"},
		],
		predicate=_meetings_predicate,
		total=_meetings_total,
		rows=_meetings_rows,
		source_link=lambda row: {"doctype": MEETING_DOCTYPE, "name": row["name"]},
	)
)

"""Team Activity (TXB-286): completed calls, reached Contacts and agreed meetings in a period.

A period is two inclusive calendar dates in the site timezone. It becomes a Scope -- the UTC
instants it starts and ends at (end exclusive), plus an optional member -- and that one Scope is
passed to every registered metric (`crm.txb.team_activity_metrics`), so the summary, the
per-member breakdown and the records drill-down always count the same thing.

Callers must check access first: `crm.txb.api.team_activity` admits Admins only.
"""

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import cint, get_system_timezone

from crm.api.session import CRM_ALLOWED_ROLES
from crm.txb.team_activity_metrics import PAGE_LENGTH, MetricDescriptor, get_metric, list_metrics

UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class Scope:
	"""What every metric counts over: [from_utc, to_utc_exclusive), for one member or the team."""

	from_utc: datetime
	to_utc_exclusive: datetime
	member: str | None
	timezone: str

	def stored_bounds(self) -> tuple[datetime, datetime]:
		"""The bounds as Frappe stores Datetime values: naive, in the site timezone."""
		site_tz = ZoneInfo(self.timezone)
		return tuple(
			bound.astimezone(site_tz).replace(tzinfo=None)
			for bound in (self.from_utc, self.to_utc_exclusive)
		)


def build_scope(from_date, to_date, member: str | None = None) -> Scope:
	"""The Scope of an inclusive site-timezone period; ValidationError on a bad period or member."""
	start, end = _parse_date(from_date), _parse_date(to_date)
	if start > end:
		frappe.throw(_("The start date must not be after the end date."), frappe.ValidationError)

	site_tz = get_system_timezone()
	return Scope(
		from_utc=_start_of_day_utc(start, site_tz),
		to_utc_exclusive=_start_of_day_utc(end + timedelta(days=1), site_tz),
		member=validate_member(member),
		timezone=site_tz,
	)


def validate_member(member: str | None) -> str | None:
	"""`member` if it is an enabled CRM user, None for the whole team; else ValidationError."""
	if not member:
		return None
	is_crm_user = frappe.db.exists(
		"Has Role", {"parenttype": "User", "parent": member, "role": ("in", CRM_ALLOWED_ROLES)}
	)
	if not (is_crm_user and frappe.db.get_value("User", member, "enabled")):
		frappe.throw(_("{0} is not an active CRM user.").format(member), frappe.ValidationError)
	return member


def crm_members() -> list[dict]:
	"""Every enabled CRM user, by name."""
	users = frappe.get_all(
		"Has Role",
		filters={"parenttype": "User", "role": ("in", CRM_ALLOWED_ROLES)},
		pluck="parent",
		distinct=True,
	)
	return frappe.get_all(
		"User",
		filters={"name": ("in", users), "enabled": 1},
		fields=["name", "full_name"],
		order_by="full_name asc",
	)


def summary(from_date, to_date, member: str | None = None) -> dict:
	scope = build_scope(from_date, to_date, member)
	return {
		**_period(scope),
		"metrics": [
			{**_describe(descriptor), "value": descriptor.total(scope)} for descriptor in list_metrics()
		],
	}


def members(from_date, to_date) -> dict:
	"""Each CRM user's value for every metric in the period."""
	scope = build_scope(from_date, to_date)
	metrics = list_metrics()
	return {
		**_period(scope),
		"metrics": [_describe(descriptor) for descriptor in metrics],
		"members": [
			{
				"user": user.name,
				"full_name": user.full_name,
				"values": {
					descriptor.key: descriptor.total(replace(scope, member=user.name))
					for descriptor in metrics
				},
			}
			for user in crm_members()
		],
	}


def records(metric: str, from_date, to_date, member: str | None = None, page=1) -> dict:
	"""One page of a metric's source records, newest first, with the metric's full total."""
	descriptor = get_metric(metric)
	page = cint(page)
	if page < 1:
		frappe.throw(_("Page must be 1 or more."), frappe.ValidationError)
	scope = build_scope(from_date, to_date, member)
	return {
		**_period(scope),
		**_describe(descriptor),
		"total": descriptor.total(scope),
		"page": page,
		"page_length": PAGE_LENGTH,
		"rows": [
			{**row, "source_link": descriptor.source_link(row)}
			for row in descriptor.rows(scope, page, PAGE_LENGTH)
		],
	}


def _parse_date(value) -> date:
	if isinstance(value, date) and not isinstance(value, datetime):
		return value
	try:
		return date.fromisoformat(value)
	except (TypeError, ValueError):
		frappe.throw(_("{0} is not a valid date (YYYY-MM-DD).").format(value), frappe.ValidationError)


def _start_of_day_utc(day: date, site_tz: str) -> datetime:
	return datetime.combine(day, time.min, tzinfo=ZoneInfo(site_tz)).astimezone(UTC)


def _period(scope: Scope) -> dict:
	start, end = scope.stored_bounds()
	return {
		"from_date": str(start.date()),
		"to_date": str((end - timedelta(days=1)).date()),
		"timezone": scope.timezone,
		"member": scope.member,
	}


def _describe(descriptor: MetricDescriptor) -> dict:
	return {
		"key": descriptor.key,
		"label": _(descriptor.label),
		"order": descriptor.order,
		"record_columns": [{**column, "label": _(column["label"])} for column in descriptor.columns],
	}

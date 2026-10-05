"""Durable per-Contact inactivity cycles (TXB-278).

Each Contact has exactly one live CRM Contact Inactivity Cycle: the period since its last human
contact, due once the site's interval has passed without another. A cycle starts from an anchor:

- History: the Contact's latest verified human contact (`contact_attribution.latest_contact`).
- Rollout: when this feature reached the site, for a Contact that existed before it without any
  verified history -- nobody is reminded about silence the app never observed.
- Creation: when the Contact was created, for one created after the rollout without history.

A cycle is Open until due, Due once `evaluate_due` finds it overdue, Reminded once its reminder
exists; those three are live and hold `live_key` (= the Contact), whose UNIQUE index keeps a
Contact to one live cycle. Newer human contact Closes it and opens the next cycle; `settle` ends
it without a successor. Every state change runs under a row lock on the Contact, and reads cycle
rows with locking reads, so concurrent writers for one Contact serialize on current data.

The interval is FCRM Settings.custom_contact_inactivity_minutes: whole minutes in 0..525600,
0 or blank meaning six calendar months in the site's time zone. Only a System Manager or the
Administrator may change it (enforced by FCRM Settings.validate).
"""

import re
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import add_to_date, cint, get_datetime, get_system_timezone, now_datetime

from crm.txb.contact_attribution import latest_contact

CYCLE_DOCTYPE = "CRM Contact Inactivity Cycle"
CONTACT_DOCTYPE = "Contact"
SETTINGS_DOCTYPE = "FCRM Settings"

SETTING_INTERVAL_MINUTES = "custom_contact_inactivity_minutes"
MAX_INTERVAL_MINUTES = 525600
DEFAULT_INTERVAL_MONTHS = 6

STATUS_OPEN = "Open"
STATUS_DUE = "Due"
STATUS_REMINDED = "Reminded"
STATUS_CLOSED = "Closed"
STATUS_SETTLED = "Settled"
LIVE_STATUSES = (STATUS_OPEN, STATUS_DUE, STATUS_REMINDED)

ANCHOR_HISTORY = "History"
ANCHOR_ROLLOUT = "Rollout"
ANCHOR_CREATION = "Creation"

# Site default holding when the feature rolled out; recorded once, never moved.
ROLLOUT_DEFAULT_KEY = "txb_contact_inactivity_rollout"

# The insert rolls back to here when a concurrent writer already opened the Contact's cycle.
INSERT_SAVEPOINT = "txb_contact_inactivity_cycle"

SETTINGS_FIELDS = [
	{
		"fieldname": SETTING_INTERVAL_MINUTES,
		"fieldtype": "Int",
		"label": "Contact Inactivity Interval (minutes)",
		"description": (
			"How long a Contact may go without human contact before its owner is reminded, in "
			f"whole minutes (0 to {MAX_INTERVAL_MINUTES}). 0 or blank means "
			f"{DEFAULT_INTERVAL_MONTHS} calendar months. Only a System Manager can change it."
		),
		"non_negative": 1,
		"insert_after": "custom_first_call_reminder_minutes",
	}
]


def ensure_interval_setting() -> None:
	"""Add the interval field to FCRM Settings if this site is missing it."""
	meta = frappe.get_meta(SETTINGS_DOCTYPE)
	missing = [field for field in SETTINGS_FIELDS if not meta.has_field(field["fieldname"])]
	if missing:
		create_custom_fields({SETTINGS_DOCTYPE: missing})
		frappe.clear_cache(doctype=SETTINGS_DOCTYPE)


def parse_interval(value) -> int:
	"""The interval as whole minutes (blank = 0), or a ValidationError if it is not one."""
	if value is None or (isinstance(value, str) and not value.strip()):
		return 0
	if isinstance(value, float) and value.is_integer():
		value = int(value)
	elif isinstance(value, str) and re.fullmatch(r"\s*[-+]?\d+\s*", value):
		value = int(value)
	if isinstance(value, bool) or not isinstance(value, int):
		frappe.throw(_("The Contact inactivity interval must be a whole number of minutes."))
	if not 0 <= value <= MAX_INTERVAL_MINUTES:
		frappe.throw(
			_("The Contact inactivity interval must be between 0 and {0} minutes.").format(
				MAX_INTERVAL_MINUTES
			)
		)
	return value


def interval_minutes() -> int:
	"""The site interval in minutes; 0 means six calendar months. Read uncached, so a save applies at once."""
	if not frappe.get_meta(SETTINGS_DOCTYPE).has_field(SETTING_INTERVAL_MINUTES):
		return 0
	return cint(frappe.db.get_single_value(SETTINGS_DOCTYPE, SETTING_INTERVAL_MINUTES, cache=False))


def rollout_date():
	"""When the feature rolled out on this site, recorded by its first call (the backfill)."""
	recorded = frappe.db.get_default(ROLLOUT_DEFAULT_KEY)
	if recorded:
		return get_datetime(recorded)
	rollout = now_datetime().replace(microsecond=0)
	frappe.db.set_default(ROLLOUT_DEFAULT_KEY, str(rollout))
	return rollout


def compute_due_at(anchor_at, minutes=None):
	"""When a cycle anchored at `anchor_at` falls due under `minutes` (default: the site interval).

	0 adds six calendar months to the site-time anchor (2026-08-31 -> 2027-02-28); otherwise the
	minutes are added as elapsed time, so a daylight-saving change never stretches or shrinks it.
	"""
	anchor = get_datetime(anchor_at)
	minutes = interval_minutes() if minutes is None else cint(minutes)
	if not minutes:
		return add_to_date(anchor, months=DEFAULT_INTERVAL_MONTHS)
	site_tz = ZoneInfo(get_system_timezone())
	due = anchor.replace(tzinfo=site_tz).astimezone(timezone.utc) + timedelta(minutes=minutes)
	return due.astimezone(site_tz).replace(tzinfo=None)


def start_cycle(contact: str) -> str | None:
	"""The Contact's live cycle, opening one anchored on its history, the rollout or its creation
	when it has none. Idempotent; None if the Contact does not exist."""
	creation = _lock(contact)
	if creation is None:
		return None
	latest = _latest_cycle(contact)
	if latest and latest.live_key:
		return latest.name

	anchor_at, anchor_source = latest_contact(contact), ANCHOR_HISTORY
	if not anchor_at:
		rollout, created = rollout_date(), get_datetime(creation)
		anchor_at, anchor_source = (
			(created, ANCHOR_CREATION) if created > rollout else (rollout, ANCHOR_ROLLOUT)
		)
	return _open(contact, (latest.cycle_no if latest else 0) + 1, anchor_at, anchor_source)


def on_qualifying_contact(contact: str, occurred_at) -> str | None:
	"""Restart the Contact's cycle from human contact at `occurred_at`; return the new cycle.

	Only contact newer than the current cycle's anchor counts: it Closes the live cycle and opens
	the next one anchored on it. Equal or older contact returns None and changes nothing.
	"""
	if _lock(contact) is None:
		return None
	occurred_at = get_datetime(occurred_at)
	latest = _latest_cycle(contact)
	if latest and occurred_at <= get_datetime(latest.anchor_at):
		return None
	if latest and latest.live_key:
		frappe.db.set_value(
			CYCLE_DOCTYPE,
			latest.name,
			{"status": STATUS_CLOSED, "live_key": None, "ended_at": now_datetime()},
		)
	return _open(contact, (latest.cycle_no if latest else 0) + 1, occurred_at, ANCHOR_HISTORY)


def recompute_unreminded(minutes=None) -> int:
	"""Re-derive due_at of every Open cycle from the interval; return how many changed.

	Due and Reminded cycles keep theirs: their reminder is already decided.
	"""
	minutes = interval_minutes() if minutes is None else cint(minutes)
	changed = 0
	for row in frappe.get_all(
		CYCLE_DOCTYPE, filters={"status": STATUS_OPEN}, fields=["name", "anchor_at", "due_at"]
	):
		due_at = compute_due_at(row.anchor_at, minutes)
		if get_datetime(row.due_at) != due_at and _transition(
			row.name, {"status": STATUS_OPEN}, {"due_at": due_at}
		):
			changed += 1
	return changed


def evaluate_due(now=None) -> list[str]:
	"""Mark every Open cycle whose due_at <= now as Due; return those cycles.

	A cycle whose Contact was deleted is Settled instead, freeing its live_key.
	"""
	now = get_datetime(now) if now else now_datetime()
	due = []
	for row in frappe.get_all(
		CYCLE_DOCTYPE,
		filters={"status": STATUS_OPEN, "due_at": ("<=", now)},
		fields=["name", "contact"],
		order_by="due_at asc",
	):
		if not frappe.db.exists(CONTACT_DOCTYPE, row.contact):
			settle(row.name)
		elif _transition(row.name, {"status": STATUS_OPEN, "due_at": ("<=", now)}, {"status": STATUS_DUE}):
			due.append(row.name)
	return due


def settle(cycle: str) -> bool:
	"""End a live cycle without a successor, freeing its live_key; False if it was not live."""
	return _transition(
		cycle,
		{"status": ("in", LIVE_STATUSES)},
		{"status": STATUS_SETTLED, "live_key": None, "ended_at": now_datetime()},
	)


def mark_reminded(cycle: str) -> bool:
	"""Record that a Due cycle's reminder exists; it stays live. False if it was not Due."""
	return _transition(
		cycle, {"status": STATUS_DUE}, {"status": STATUS_REMINDED, "reminded_at": now_datetime()}
	)


def _lock(contact: str):
	"""Row-lock the Contact for the rest of the transaction; return its creation, or None."""
	if not contact:
		return None
	return frappe.db.get_value(CONTACT_DOCTYPE, contact, "creation", for_update=True)


def _latest_cycle(contact: str):
	"""The Contact's highest-numbered cycle (its live one, if any), read with a locking read."""
	return frappe.db.get_value(
		CYCLE_DOCTYPE,
		{"contact": contact},
		["name", "cycle_no", "status", "live_key", "anchor_at"],
		as_dict=True,
		order_by="cycle_no desc",
		for_update=True,
	)


def _open(contact: str, cycle_no: int, anchor_at, anchor_source: str) -> str | None:
	frappe.db.savepoint(INSERT_SAVEPOINT)
	try:
		return (
			frappe.get_doc(
				{
					"doctype": CYCLE_DOCTYPE,
					"contact": contact,
					"cycle_no": cycle_no,
					"status": STATUS_OPEN,
					"live_key": contact,
					"anchor_at": anchor_at,
					"anchor_source": anchor_source,
					"due_at": compute_due_at(anchor_at),
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
		# A writer that skipped the Contact lock opened this cycle first; it is the live one.
		frappe.db.rollback(save_point=INSERT_SAVEPOINT)
		return frappe.db.get_value(CYCLE_DOCTYPE, {"live_key": contact}, "name", for_update=True)


def _transition(cycle: str, current: dict, values: dict) -> bool:
	"""Under its Contact's lock, apply `values` only if the cycle still matches `current`."""
	contact = frappe.db.get_value(CYCLE_DOCTYPE, cycle, "contact")
	if not contact:
		return False
	_lock(contact)
	if not frappe.db.get_value(CYCLE_DOCTYPE, {"name": cycle, **current}, "name", for_update=True):
		return False
	frappe.db.set_value(CYCLE_DOCTYPE, cycle, values)
	return True

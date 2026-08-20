"""CRM Lead document events.

Ported from the `Require Disqualified Reason` and `Prevent Duplicate Lead` Server Scripts.
Behaviour is preserved exactly; see specs/server-script-migration.md for known issues
that were deliberately carried over rather than fixed here.
"""

import frappe
from frappe import _

from crm.txb.people import find_exact_duplicate

STATUS_DISQUALIFIED = "Disqualified"
DEFAULT_DISQUALIFIED_REASON = "Pending Review"

# TXB-128: the canonical status that a Log a reach unlocks. The retired "Qualifying call"
# status is folded onto it by a migration patch, so this is the only status the reach gate
# ever has to defend.
STATUS_CONTACTED = "Contacted"

# TXB-129: the status a scheduled Discovery meeting unlocks. Every transition into it must go
# through ``crm.txb.api.actions.schedule_discovery``; see ``require_discovery_details``.
STATUS_DISCOVERY = "Discovery meeting set"


def require_discovery_details(doc, method=None):
	"""Entering Discovery meeting set must go through ``schedule_discovery`` (TXB-129).

	The status carries a mandatory scheduled meeting -- a required date, time and type, plus a
	manual link (Virtual) or address (Onsite) -- recorded on the timeline. The two Lead.vue
	handlers prompt for it, but the browser is not a security boundary: the Leads kanban drag and
	the mobile status control write ``status`` directly via ``frappe.client.set_value``, which
	would otherwise land a lead in Discovery meeting set with no scheduling details at all. This
	guard makes the server the single enforcement point -- every status->Discovery write that is
	not the flagged ``schedule_discovery`` save is refused -- so no entrypoint, present or
	future, can silently bypass the gate.

	``schedule_discovery`` arms ``frappe.flags.txb_action`` with this document's name (mirroring
	the reach guard), which is the one exemption. Inserts are exempt too: a lead created directly
	in the status (an import, or a seed) is not a transition into it.
	"""
	if doc.is_new():
		return

	if not doc.has_value_changed("status"):
		return

	if doc.status != STATUS_DISCOVERY:
		return

	# The schedule endpoint scopes the flag to this document's name, so the exemption cannot leak
	# onto another CRM Lead saved inside the same request.
	if frappe.flags.get("txb_action") == doc.name:
		return

	frappe.throw(
		_(
			"Schedule a discovery meeting to move a lead into Discovery meeting set, so the "
			"date, time, type and location that justify the change are recorded."
		),
		frappe.ValidationError,
		title=_("Schedule Discovery meeting"),
	)


def require_reach_for_contacted(doc, method=None):
	"""Entering Contacted must go through ``crm.txb.api.actions.log_reach`` (TXB-128).

	Contacted carries a mandatory Log a reach -- a required summary and follow-up context
	recorded on the timeline. The two Lead.vue handlers prompt for it, but the browser is
	not a security boundary: the Leads kanban drag and the mobile status control write
	``status`` directly via ``frappe.client.set_value``, which would otherwise land a lead
	in Contacted with no reach at all. This guard makes the server the single enforcement
	point -- every status->Contacted write that is not the flagged ``log_reach`` save is
	refused -- so no entrypoint, present or future, can silently bypass the gate.

	``log_reach`` arms ``frappe.flags.txb_action`` with this document's name (mirroring the
	deal transition guard), which is the one exemption. Inserts are exempt too: a lead
	created directly in Contacted (an import, or the demo seed) is not a transition into it.
	The migration patch writes status with direct SQL, which bypasses validation entirely
	and is unaffected.
	"""
	if doc.is_new():
		return

	if not doc.has_value_changed("status"):
		return

	if doc.status != STATUS_CONTACTED:
		return

	# The reach endpoint scopes the flag to this document's name, so the exemption cannot
	# leak onto another CRM Lead saved inside the same request.
	if frappe.flags.get("txb_action") == doc.name:
		return

	frappe.throw(
		_(
			"Move a lead into Contacted with Log a reach, so the summary and follow-up "
			"that justify the change are recorded."
		),
		frappe.ValidationError,
		title=_("Log a reach"),
	)


def guard_archived_lead(doc, method=None):
	"""A converted Lead is archived: readable, but closed to user-originated edits (TXB-132).

	Conversion is a single durable event that records the Contact, initial Opportunity and
	timestamp on the Lead and marks it ``converted``. After that the Lead is a historical
	record, so a later save -- a kanban drag, a field edit, a mobile status change, a crafted
	REST write -- must be refused rather than silently mutate an archived conversion.

	The check reads the *persisted* ``converted`` flag, so it fires only on writes to a Lead
	that was already converted before this save, never on the conversion itself. Two writers
	are exempt on purpose:

	* the conversion authority, which sets ``converted`` and the result fields with ``db_set``
	  (no validation) inside ``convert_to_deal`` and scopes ``frappe.flags.txb_conversion`` to
	  this Lead so its narrowly bounded internal writes pass;
	* inserts -- a Lead created directly as converted (an import or seed) is not a transition.

	This is a backend-only immutability guard; it deliberately does not touch CRM Deal.lead,
	which stays non-unique so later Opportunities may still reference the archived Lead.
	"""
	if doc.is_new():
		return

	if not frappe.db.get_value("CRM Lead", doc.name, "converted"):
		return

	if frappe.flags.get("txb_conversion") == doc.name:
		return

	frappe.throw(
		_("This lead has been converted and can no longer be edited."),
		frappe.ValidationError,
		title=_("Lead Converted"),
	)


def default_disqualified_reason(doc, method=None):
	"""A disqualified lead always carries a reason, so reporting never sees a blank."""
	if doc.status == STATUS_DISQUALIFIED and not doc.lost_reason:
		doc.lost_reason = DEFAULT_DISQUALIFIED_REASON


def prevent_duplicate(doc, method=None):
	"""Reject a lead when a Contact or Lead already has the same name and email.

	All three of first name, last name and email must match. Leads without a first name
	or without an email are not checked, matching the original script.

	The rule itself lives in `crm.txb.people.find_exact_duplicate` so the pre-Create
	search (TXB-112) blocks on exactly what this blocks on -- see that module.
	"""
	first_name = (doc.first_name or "").strip()
	last_name = (doc.last_name or "").strip()
	email = (doc.email or "").strip()

	duplicate_in = find_exact_duplicate(first_name, last_name, email)
	if not duplicate_in:
		return

	full_name = f"{first_name} {last_name}".strip()
	frappe.throw(
		_("A {0} with the name <b>{1}</b> and email <b>{2}</b> already exists. Cannot create a duplicate lead.").format(
			duplicate_in, full_name, email
		),
		title=_("Duplicate Found"),
	)

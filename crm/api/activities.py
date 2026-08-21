import json

import frappe
from bs4 import BeautifulSoup
from frappe import _
from frappe.desk.form.load import get_docinfo
from frappe.query_builder import JoinType
from frappe.translate import get_translated_doctypes
from frappe.utils import get_datetime

from crm.fcrm.doctype.crm_call_log.crm_call_log import parse_call_log
from crm.txb.constants import FIELD_CONVERTED_AT, FIELD_CONVERTED_CONTACT

# --- Normalized Opportunity activity event contract (TXB-133) ------------------------------
#
# One Opportunity's work history is a chronological stream of events drawn from several canonical
# sources (audit Versions, Communications, Call Logs, Comments and the document's own creation).
# Every event the readers below emit carries a stable normalized envelope so a consumer can order,
# label and open it without knowing the source shape -- and without any source content being
# copied. The canonical bodies stay in the record `canonical_docname` points at.
#
#   event_type          normalized kind (creation | field_change | comment | communication |
#                       attachment | call | meeting | note)
#   occurred_at         canonical timestamp used for deterministic chronology
#   actor               the user the event is attributed to
#   summary             a short readable label only (field label, email subject, ...); never the
#                       source body
#   canonical_doctype   the record that holds the canonical content: "Version" for a field change,
#   canonical_docname   "Communication", "CRM Call Log", "Comment", or the Deal/Lead itself
#   target              {doctype, name} a consumer can open/expand, or None
#
# Contact source metadata (is_lead / source_doctype / source_docname / source_route) is layered on
# top by the Contact aggregator (TXB-178) and is deliberately kept in its own keys so it never
# collides with the canonical source identity above.

# CRM Task lifecycle fields whose forward-only changes are normalized into an Opportunity's history
# (TXB-133). Title and description edits are deliberately excluded from auditing in this phase.
TASK_AUDITED_FIELDS = ("status", "assigned_to", "due_date", "priority")

EVENT_TYPE_BY_ACTIVITY = {
	"creation": "creation",
	"changed": "field_change",
	"added": "field_change",
	"removed": "field_change",
	"comment": "comment",
	"communication": "communication",
	"attachment_log": "attachment",
	"incoming_call": "call",
	"outgoing_call": "call",
	"event": "meeting",
	"note": "note",
}

# --- Meeting (Event) lifecycle events (TXB-186) --------------------------------------------
#
# A CRM meeting is a Frappe Event linked to the Opportunity/Lead through
# reference_doctype/reference_docname -- the same records the separate Events tab reads. Event has
# track_changes enabled, so scheduling, rescheduling, status changes, completion and cancellation
# are all recoverable from its immutable audit Versions. Each lifecycle moment becomes one
# normalized "event" activity whose canonical home is the Event; nothing is copied and the
# `target` opens that canonical Event. `meeting_action` names the lifecycle moment and `summary`
# always carries the meeting's own subject label, so a consumer renders "<actor> <verb> <subject>"
# without reading any Event body.

# starts_on / ends_on moving is a reschedule.
MEETING_RESCHEDULE_FIELDS = {"starts_on", "ends_on"}
# Terminal Event statuses that read as completion vs. cancellation in the timeline. Frappe ships
# "Completed"/"Closed" and (via customisation) "Cancelled"; spelling variants are tolerated.
MEETING_COMPLETED_STATUSES = {"Completed", "Closed"}
MEETING_CANCELLED_STATUSES = {"Cancelled", "Canceled"}


def _meeting_events(container_doctype: str, container_docname: str, *, is_lead: bool):
	"""Yield the normalized meeting lifecycle events for one Opportunity/Lead.

	Reads every Event linked to (container_doctype, container_docname) and emits a "scheduled"
	event at Event creation plus one event per relevant audit Version (reschedule, completion,
	cancellation, other reliable status change). Canonical content stays in the Event; each
	activity only references it via the envelope and open target.
	"""
	events = frappe.get_all(
		"Event",
		filters={
			"reference_doctype": container_doctype,
			"reference_docname": container_docname,
		},
		fields=["name", "subject", "starts_on", "ends_on", "status", "owner", "creation"],
	)

	activities = []
	for event in events:
		subject = event.get("subject") or _("Meeting")
		target = {"doctype": "Event", "name": event["name"]}

		scheduled = {
			"name": event["name"],
			"activity_type": "event",
			"creation": event["creation"],
			"owner": event["owner"],
			"data": {
				"meeting_action": "scheduled",
				"subject": subject,
				"starts_on": event.get("starts_on"),
				"ends_on": event.get("ends_on"),
			},
			"is_lead": is_lead,
		}
		_with_event_envelope(
			scheduled,
			occurred_at=event["creation"],
			actor=event["owner"],
			canonical_doctype="Event",
			canonical_docname=event["name"],
			summary=subject,
			target=target,
		)
		activities.append(scheduled)
		activities.extend(_meeting_version_events(event, subject, target, is_lead=is_lead))

	return activities


def _meeting_version_events(event: dict, subject: str, target: dict, *, is_lead: bool):
	"""Yield one normalized lifecycle event per relevant Event Version.

	One save can touch several fields; a single event is emitted per Version, preferring the more
	meaningful lifecycle signal -- a status change (completion / cancellation / other) over a bare
	reschedule. Canonical old/new values stay in the Version; the event only labels the moment.
	"""
	versions = frappe.get_all(
		"Version",
		filters={"ref_doctype": "Event", "docname": event["name"]},
		fields=["name", "owner", "creation", "data"],
		order_by="creation asc",
	)

	activities = []
	for version in versions:
		data = json.loads(version.data or "{}")
		changed = {change[0]: change for change in (data.get("changed") or []) if change}

		payload = {"subject": subject}
		if "status" in changed:
			new_status = changed["status"][2]
			if new_status in MEETING_CANCELLED_STATUSES:
				action = "cancelled"
			elif new_status in MEETING_COMPLETED_STATUSES:
				action = "completed"
			else:
				action = "status_changed"
			payload["status"] = new_status
		elif changed.keys() & MEETING_RESCHEDULE_FIELDS:
			action = "rescheduled"
			if "starts_on" in changed:
				payload["starts_on"] = changed["starts_on"][2]
			if "ends_on" in changed:
				payload["ends_on"] = changed["ends_on"][2]
		else:
			continue

		payload["meeting_action"] = action
		activity = {
			"name": version.name,
			"activity_type": "event",
			"creation": version.creation,
			"owner": version.owner,
			"data": payload,
			"is_lead": is_lead,
		}
		_with_event_envelope(
			activity,
			occurred_at=version.creation,
			actor=version.owner,
			canonical_doctype="Event",
			canonical_docname=event["name"],
			summary=subject,
			target=target,
		)
		activities.append(activity)

	return activities


def _note_metadata_events(notes: list, *, is_lead: bool):
	"""Yield one lightweight metadata event per linked FCRM Note (TXB-186).

	General notes and Coaching Call notes (titled "Coaching Call #N") are both canonical FCRM Note
	records owned by the specialized Notes module. Here each surfaces as a single creation entry in
	the main Activity stream -- actor, timestamp, title-only summary and an open target on the
	canonical Note. No note body is copied: the full content and all editing stay in the Notes
	module, which remains authoritative.
	"""
	activities = []
	for note in notes or []:
		if not isinstance(note, dict) or not note.get("name"):
			continue
		occurred_at = note.get("creation") or note.get("modified")
		title = note.get("title") or _("Note")
		activity = {
			"name": note["name"],
			"activity_type": "note",
			"creation": occurred_at,
			"owner": note.get("owner"),
			"data": {"title": title},
			"is_lead": is_lead,
		}
		_with_event_envelope(
			activity,
			occurred_at=occurred_at,
			actor=note.get("owner"),
			canonical_doctype="FCRM Note",
			canonical_docname=note["name"],
			summary=title,
			target={"doctype": "FCRM Note", "name": note["name"]},
		)
		activities.append(activity)
	return activities


def _with_event_envelope(
	activity: dict,
	*,
	occurred_at,
	actor,
	canonical_doctype: str,
	canonical_docname,
	summary=None,
	target=None,
):
	"""Layer the normalized event envelope onto a source activity, in place.

	Adds the stable cross-source fields without copying any source content: the Version,
	Communication, Call Log or Comment record referenced by (canonical_doctype, canonical_docname)
	remains the single home of the canonical body. Returns the same dict for convenient chaining.
	"""
	activity["event_type"] = EVENT_TYPE_BY_ACTIVITY.get(
		activity.get("activity_type"), activity.get("activity_type")
	)
	activity["occurred_at"] = occurred_at
	activity["actor"] = actor
	activity["summary"] = summary
	activity["canonical_doctype"] = canonical_doctype
	activity["canonical_docname"] = canonical_docname
	activity["target"] = target
	return activity


def _version_field_events(
	version,
	doc_fields: dict,
	avoid_fields: list,
	*,
	is_lead: bool,
	container_doctype: str,
	container_docname: str,
):
	"""Yield one normalized event per relevant field change recorded in a single Version.

	A single save can change several fields at once -- e.g. an Opportunity's status and
	deal_owner together. The audit Version records every change in ``data['changed']``; this emits
	each relevant one as its own event. (The reader previously read only ``changed[0]``, so any
	co-saved field was silently dropped from the timeline.) Canonical old/new values stay in the
	Version record; each event only references it via the envelope.
	"""
	data = json.loads(version.data)
	for change in data.get("changed") or []:
		if not change:
			continue

		field = doc_fields.get(change[0], None)
		if not field or change[0] in avoid_fields or (not change[1] and not change[2]):
			continue

		field_label = field.get("label") or change[0]
		field_option = field.get("options") or None

		activity_type = "changed"
		payload = {
			"field": change[0],
			"field_label": field_label,
			"old_value": change[1],
			"value": change[2],
		}

		if not change[1] and change[2]:
			activity_type = "added"
			payload = {
				"field": change[0],
				"field_label": field_label,
				"value": change[2],
			}
		elif change[1] and not change[2]:
			activity_type = "removed"
			payload = {
				"field": change[0],
				"field_label": field_label,
				"value": change[1],
			}

		if payload.get("value") and field_option and is_translatable(field_option):
			payload["value"] = _(payload["value"])
			if payload.get("old_value"):
				payload["old_value"] = _(payload["old_value"])

		activity = {
			"activity_type": activity_type,
			"creation": version.creation,
			"owner": version.owner,
			"data": payload,
			"is_lead": is_lead,
			"options": field_option,
		}
		_with_event_envelope(
			activity,
			occurred_at=version.creation,
			actor=version.owner,
			canonical_doctype="Version",
			canonical_docname=version.name,
			summary=field_label,
			target={"doctype": container_doctype, "name": container_docname},
		)
		yield activity


@frappe.whitelist()
def get_activities(name: str):
	if frappe.db.exists("CRM Deal", name):
		return get_deal_activities(name)
	elif frappe.db.exists("CRM Lead", name):
		return get_lead_activities(name)
	elif frappe.db.exists("Contact", name):
		return get_contact_activities(name)
	else:
		frappe.throw(_("Document not found"), frappe.DoesNotExistError)


def get_deal_activities(name: str, include_lead: bool = True):
	if not frappe.has_permission("CRM Deal", "read", name):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	return _read_deal_activities(name, include_lead=include_lead)


def _read_deal_activities(name: str, include_lead: bool = True):
	get_docinfo("", "CRM Deal", name)
	docinfo = frappe.response["docinfo"]
	deal_meta = frappe.get_meta("CRM Deal")
	deal_fields = {
		field.fieldname: {"label": field.label, "options": field.options} for field in deal_meta.fields
	}
	avoid_fields = [
		"lead",
		"response_by",
		"sla_creation",
		"sla",
		"first_response_time",
		"first_responded_on",
	]

	doc = frappe.db.get_values("CRM Deal", name, ["creation", "owner", "lead"])[0]
	lead = doc[2]

	activities = []
	calls = []
	notes = []
	tasks = []
	attachments = []
	creation_text = _("created this deal")

	if lead:
		creation_text = _("converted the lead to this deal")
		if include_lead:
			# Existing Deal-endpoint behavior: embed the source Lead's history inline so a single
			# converted Opportunity shows its full pre-conversion timeline. The Lead's activities
			# are surfaced here by aggregation, not by copying or reparenting: each record still
			# references the Lead. Attribute them to that source so the timeline shows where a
			# dial came from. The activity feed already carries is_lead; the call/note/task lists
			# do not. The Contact aggregator instead reads every distinct Lead once itself and
			# calls this with include_lead=False, so a Lead shared by several Opportunities is
			# never replayed per Deal.
			activities, calls, notes, tasks, attachments = get_lead_activities(lead)
			attribute_to_lead(calls, lead)
			attribute_to_lead(notes, lead)
			attribute_to_lead(tasks, lead)

	creation_activity = {
		"activity_type": "creation",
		"creation": doc[0],
		"owner": doc[1],
		"data": creation_text,
		"is_lead": False,
	}
	_with_event_envelope(
		creation_activity,
		occurred_at=doc[0],
		actor=doc[1],
		canonical_doctype="CRM Deal",
		canonical_docname=name,
		summary=creation_text,
		target={"doctype": "CRM Deal", "name": name},
	)
	activities.append(creation_activity)

	docinfo.versions.reverse()

	for version in docinfo.versions:
		activities.extend(
			_version_field_events(
				version,
				deal_fields,
				avoid_fields,
				is_lead=False,
				container_doctype="CRM Deal",
				container_docname=name,
			)
		)

	for comment in docinfo.comments:
		activity = {
			"name": comment.name,
			"activity_type": "comment",
			"creation": comment.creation,
			"owner": comment.owner,
			"content": comment.content,
			"attachments": get_attachments("Comment", comment.name),
			"is_lead": False,
		}
		_with_event_envelope(
			activity,
			occurred_at=comment.creation,
			actor=comment.owner,
			canonical_doctype="Comment",
			canonical_docname=comment.name,
			target={"doctype": "Comment", "name": comment.name},
		)
		activities.append(activity)

	for communication in docinfo.communications + docinfo.automated_messages:
		activity = {
			"name": communication.name,
			"activity_type": "communication",
			"communication_type": communication.communication_type,
			"communication_date": communication.communication_date or communication.creation,
			"creation": communication.creation,
			"data": {
				"subject": communication.subject,
				"content": communication.content,
				"sender_full_name": communication.sender_full_name,
				"sender": communication.sender,
				"recipients": communication.recipients,
				"cc": communication.cc,
				"bcc": communication.bcc,
				"attachments": get_attachments("Communication", communication.name),
				"read_by_recipient": communication.read_by_recipient,
				"delivery_status": communication.delivery_status,
			},
			"is_lead": False,
		}
		_with_event_envelope(
			activity,
			occurred_at=communication.communication_date or communication.creation,
			actor=communication.sender,
			canonical_doctype="Communication",
			canonical_docname=communication.name,
			summary=communication.subject,
			target={"doctype": "Communication", "name": communication.name},
		)
		activities.append(activity)

	for attachment_log in docinfo.attachment_logs:
		activity = {
			"name": attachment_log.name,
			"activity_type": "attachment_log",
			"creation": attachment_log.creation,
			"owner": attachment_log.owner,
			"data": parse_attachment_log(attachment_log.content, attachment_log.comment_type),
			"is_lead": False,
		}
		_with_event_envelope(
			activity,
			occurred_at=attachment_log.creation,
			actor=attachment_log.owner,
			canonical_doctype="Comment",
			canonical_docname=attachment_log.name,
			target={"doctype": "Comment", "name": attachment_log.name},
		)
		activities.append(activity)

	# Fold the linked Tasks' forward-only lifecycle (creation + tracked changes) into the
	# chronological activity stream so the Opportunity carries auditable Task history (TXB-133).
	activities.extend(get_linked_task_activities(name))

	linked = get_linked_calls(name)
	# This Opportunity's own notes only: any embedded Lead notes are already surfaced (with their
	# own metadata events) by the get_lead_activities call above, so they are not re-emitted here.
	deal_notes = get_linked_notes(name) + linked.get("notes", [])
	calls = calls + linked.get("calls", [])
	notes = notes + deal_notes
	tasks = tasks + get_linked_tasks(name) + linked.get("tasks", [])
	attachments = attachments + get_attachments("CRM Deal", name)

	# TXB-186: meeting lifecycle and Note-creation metadata join the main Activity stream. The full
	# Note bodies remain in `notes` (the authoritative Notes module); these are reference-only.
	activities.extend(_meeting_events("CRM Deal", name, is_lead=False))
	activities.extend(_note_metadata_events(deal_notes, is_lead=False))

	_tag_call_events(calls)

	activities.sort(key=lambda x: x["creation"], reverse=True)
	activities = handle_multiple_versions(activities)

	return activities, calls, notes, tasks, attachments


def get_lead_activities(name: str):
	if not frappe.has_permission("CRM Lead", "read", name):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	return _read_lead_activities(name)


def _read_lead_activities(name: str):
	get_docinfo("", "CRM Lead", name)
	docinfo = frappe.response["docinfo"]
	lead_meta = frappe.get_meta("CRM Lead")
	lead_fields = {
		field.fieldname: {"label": field.label, "options": field.options} for field in lead_meta.fields
	}
	avoid_fields = [
		"converted",
		"response_by",
		"sla_creation",
		"sla",
		"first_response_time",
		"first_responded_on",
	]

	doc = frappe.db.get_values("CRM Lead", name, ["creation", "owner"])[0]
	creation_text = _("created this lead")
	creation_activity = {
		"activity_type": "creation",
		"creation": doc[0],
		"owner": doc[1],
		"data": creation_text,
		"is_lead": True,
	}
	_with_event_envelope(
		creation_activity,
		occurred_at=doc[0],
		actor=doc[1],
		canonical_doctype="CRM Lead",
		canonical_docname=name,
		summary=creation_text,
		target={"doctype": "CRM Lead", "name": name},
	)
	activities = [creation_activity]

	docinfo.versions.reverse()

	for version in docinfo.versions:
		activities.extend(
			_version_field_events(
				version,
				lead_fields,
				avoid_fields,
				is_lead=True,
				container_doctype="CRM Lead",
				container_docname=name,
			)
		)

	for comment in docinfo.comments:
		activity = {
			"name": comment.name,
			"activity_type": "comment",
			"creation": comment.creation,
			"owner": comment.owner,
			"content": comment.content,
			"attachments": get_attachments("Comment", comment.name),
			"is_lead": True,
		}
		_with_event_envelope(
			activity,
			occurred_at=comment.creation,
			actor=comment.owner,
			canonical_doctype="Comment",
			canonical_docname=comment.name,
			target={"doctype": "Comment", "name": comment.name},
		)
		activities.append(activity)

	for communication in docinfo.communications + docinfo.automated_messages:
		activity = {
			"name": communication.name,
			"activity_type": "communication",
			"communication_type": communication.communication_type,
			"communication_date": communication.communication_date or communication.creation,
			"creation": communication.creation,
			"data": {
				"subject": communication.subject,
				"content": communication.content,
				"sender_full_name": communication.sender_full_name,
				"sender": communication.sender,
				"recipients": communication.recipients,
				"cc": communication.cc,
				"bcc": communication.bcc,
				"attachments": get_attachments("Communication", communication.name),
				"read_by_recipient": communication.read_by_recipient,
				"delivery_status": communication.delivery_status,
			},
			"is_lead": True,
		}
		_with_event_envelope(
			activity,
			occurred_at=communication.communication_date or communication.creation,
			actor=communication.sender,
			canonical_doctype="Communication",
			canonical_docname=communication.name,
			summary=communication.subject,
			target={"doctype": "Communication", "name": communication.name},
		)
		activities.append(activity)

	for attachment_log in docinfo.attachment_logs:
		activity = {
			"name": attachment_log.name,
			"activity_type": "attachment_log",
			"creation": attachment_log.creation,
			"owner": attachment_log.owner,
			"data": parse_attachment_log(attachment_log.content, attachment_log.comment_type),
			"is_lead": True,
		}
		_with_event_envelope(
			activity,
			occurred_at=attachment_log.creation,
			actor=attachment_log.owner,
			canonical_doctype="Comment",
			canonical_docname=attachment_log.name,
			target={"doctype": "Comment", "name": attachment_log.name},
		)
		activities.append(activity)

	linked = get_linked_calls(name)
	calls = linked.get("calls", [])
	notes = get_linked_notes(name) + linked.get("notes", [])
	tasks = get_linked_tasks(name) + linked.get("tasks", [])
	attachments = get_attachments("CRM Lead", name)

	# TXB-186: meeting lifecycle and Note-creation metadata join the main Activity stream. The full
	# Note bodies remain in `notes` (the authoritative Notes module); these are reference-only.
	activities.extend(_meeting_events("CRM Lead", name, is_lead=True))
	activities.extend(_note_metadata_events(notes, is_lead=True))

	_tag_call_events(calls)

	activities.sort(key=lambda x: x["creation"], reverse=True)
	activities = handle_multiple_versions(activities)

	return activities, calls, notes, tasks, attachments


# --- Contact activity aggregate (TXB-132) -------------------------------------------------
#
# A person-level Contact activity log is the deduplicated union of every archived Lead
# associated with the Contact (its pre-conversion history) and every linked Opportunity (its
# post-conversion history). Each distinct Lead is read exactly once and each distinct
# Opportunity exactly once -- Opportunity history is read WITHOUT its embedded Lead replay
# (include_lead=False) -- so a Lead referenced by several Opportunities is not repeated.

# Phase labels attached to every aggregated record. `converted_at` on the archived Lead is the
# cutoff: a Lead record at or before it is pre-conversion; an Opportunity record is always
# post-conversion.
PHASE_PRE_CONVERSION = "pre_conversion"
PHASE_POST_CONVERSION = "post_conversion"

# Frontend route prefix per source doctype, surfaced as `source_route` so a caller can link a
# record back to the exact Lead or Opportunity it came from.
SOURCE_ROUTES = {"CRM Lead": "leads", "CRM Deal": "deals"}


@frappe.whitelist()
def get_contact_activities(name: str):
	"""Return the deduplicated activity aggregate for a Contact.

	Spans every distinct archived Lead associated with the Contact (pre-conversion) and every
	linked Opportunity (post-conversion), tags each record with its source and phase, and
	returns deterministic chronology. Yields the same
	(activities, calls, notes, tasks, attachments) shape as the Lead and Deal endpoints, so the
	same categories -- versions, comments, communications, calls/dials, notes, tasks, and
	attachments -- are all covered.

	Each linked Opportunity is read once through the same Deal reader that emits the expanded
	normalized event contract (TXB-133): Opportunity field changes, Task lifecycle, meeting Events,
	and general/Coaching Call Note metadata all arrive in the `activities` stream already carrying
	their canonical envelope (actor, timestamp, summary, canonical source, open target). The
	aggregator adds source/phase attribution and deduplicates by canonical event/source identity
	(TXB-188); it never copies a source body -- the canonical record stays authoritative.
	"""
	_authorize_contact_activities(name)

	lead_names, deal_names = _resolve_contact_sources(name)
	converted_at = _converted_at_by_lead(lead_names)

	activities, calls, notes, tasks, attachments = [], [], [], [], []

	for lead in lead_names:
		streams = _read_lead_activities(lead)
		for stream in streams:
			_tag_lead_source(stream, lead, converted_at.get(lead))
		activities += streams[0]
		calls += streams[1]
		notes += streams[2]
		tasks += streams[3]
		attachments += streams[4]

	for deal in deal_names:
		# include_lead=False: the Lead history is read once above, not replayed per Opportunity.
		streams = _read_deal_activities(deal, include_lead=False)
		for stream in streams:
			_tag_deal_source(stream, deal)
		activities += streams[0]
		calls += streams[1]
		notes += streams[2]
		tasks += streams[3]
		attachments += streams[4]

	activities = _dedup(activities, _activity_identity)
	calls = _dedup(calls, _record_identity)
	notes = _dedup(notes, _record_identity)
	tasks = _dedup(tasks, _record_identity)
	attachments = _dedup(attachments, _record_identity)

	_sort_by_recency(activities)
	_sort_by_recency(calls)
	_sort_by_recency(notes)
	_sort_by_recency(tasks)
	_sort_by_recency(attachments)

	return activities, calls, notes, tasks, attachments


def _authorize_contact_activities(contact: str):
	"""The single authorization seam for the Contact activity aggregate.

	Contact `read` permission currently exposes the entire aggregate; source-level (per Lead /
	per Opportunity) filtering is a deliberate non-goal for this iteration and, when it lands,
	belongs here so no caller can bypass it. The aggregator reads its sources through the
	unchecked internal readers precisely so this is the only gate -- the direct Lead and Deal
	endpoints keep their own per-record permission checks untouched.
	"""
	if not frappe.has_permission("Contact", "read", contact):
		frappe.throw(_("Not permitted"), frappe.PermissionError)


def _resolve_contact_sources(contact: str):
	"""Resolve the distinct archived Leads and linked Opportunities for a Contact.

	Opportunities are the Contact's linked CRM Deals. Leads are the union of (a) every Lead
	whose conversion recorded this Contact as its result and (b) every Lead referenced for
	provenance by a linked Opportunity -- CRM Deal.lead is non-unique, so the same Lead reached
	through several Opportunities is collapsed to one here. Both lists are sorted so downstream
	ordering is deterministic.
	"""
	deal_names = sorted(
		set(
			frappe.get_all(
				"CRM Contacts",
				filters={"contact": contact, "parenttype": "CRM Deal"},
				pluck="parent",
				distinct=True,
			)
			or []
		)
	)

	lead_names = set()

	meta = frappe.get_meta("CRM Lead")
	if meta.has_field(FIELD_CONVERTED_CONTACT):
		lead_names.update(
			frappe.get_all("CRM Lead", filters={FIELD_CONVERTED_CONTACT: contact}, pluck="name") or []
		)

	if deal_names:
		for row in (
			frappe.get_all(
				"CRM Deal",
				filters={"name": ("in", deal_names), "lead": ("is", "set")},
				fields=["lead"],
				distinct=True,
			)
			or []
		):
			if row.lead:
				lead_names.add(row.lead)

	return sorted(lead_names), deal_names


def _converted_at_by_lead(lead_names: list) -> dict:
	"""Map each Lead to its `converted_at` cutoff, batched in one query (absent -> None)."""
	if not lead_names:
		return {}
	meta = frappe.get_meta("CRM Lead")
	if not meta.has_field(FIELD_CONVERTED_AT):
		return {}
	rows = frappe.get_all(
		"CRM Lead",
		filters={"name": ("in", lead_names)},
		fields=["name", FIELD_CONVERTED_AT],
	)
	return {row.name: row.get(FIELD_CONVERTED_AT) for row in rows}


def _tag_lead_source(records: list, lead: str, converted_at) -> list:
	"""Attach Lead source and per-record phase metadata, in place."""
	route = f"{SOURCE_ROUTES['CRM Lead']}/{lead}"
	for record in records or []:
		if isinstance(record, dict):
			record["is_lead"] = True
			record["source_doctype"] = "CRM Lead"
			record["source_docname"] = lead
			record["source_route"] = route
			record["phase"] = _phase_for(record, converted_at)
	return records


def _tag_deal_source(records: list, deal: str) -> list:
	"""Attach Opportunity source and (always post-conversion) phase metadata, in place."""
	route = f"{SOURCE_ROUTES['CRM Deal']}/{deal}"
	for record in records or []:
		if isinstance(record, dict):
			record["is_lead"] = False
			record["source_doctype"] = "CRM Deal"
			record["source_docname"] = deal
			record["source_route"] = route
			record["phase"] = PHASE_POST_CONVERSION
	return records


def _phase_for(record: dict, converted_at) -> str:
	"""Classify a Lead-sourced record against the conversion cutoff.

	No recorded cutoff (an unconverted or pre-patch Lead) is treated as entirely
	pre-conversion. Otherwise a record dated at or before `converted_at` is pre-conversion and
	anything after it post-conversion.
	"""
	if not converted_at:
		return PHASE_PRE_CONVERSION
	timestamp = _record_timestamp(record)
	if not timestamp:
		return PHASE_PRE_CONVERSION
	return (
		PHASE_PRE_CONVERSION
		if get_datetime(timestamp) <= get_datetime(converted_at)
		else PHASE_POST_CONVERSION
	)


def _record_timestamp(record: dict):
	"""The best available original timestamp for a record, preserved as stored."""
	return record.get("creation") or record.get("communication_date") or record.get("start_time")


def _record_identity(record: dict):
	"""Stable identity for a call/note/task/attachment: its own docname."""
	return ("name", record.get("name"))


def _activity_identity(activity: dict):
	"""Stable identity for a feed activity, keyed on its canonical event/source identity.

	Comments, attachment logs and grouped versions carry a docname; creation and version rows do
	not, so fall back to source plus type plus timestamp. A single save now emits one event per
	changed field, all sharing the version's timestamp, so the changed field is part of the key to
	keep co-saved changes (e.g. status and deal_owner) distinct. The source docname is part of
	every key, so records from different Leads/Opportunities never collide.

	The expanded Opportunity contract (TXB-133/TXB-188) folds in event types that carry no
	top-level `name` and are identified only by their canonical envelope: a Task's "created" event,
	a meeting's "scheduled" event, a Note's creation entry. Several of these can share one
	Opportunity and one timestamp (e.g. two Tasks created in the same save), so the canonical
	(doctype, docname) the event points at is part of the key. This only makes the identity more
	specific -- genuinely identical events still collapse, but distinct canonical records that
	happen to share a timestamp are no longer merged into one row.
	"""
	payload = activity.get("data")
	field = payload.get("field") if isinstance(payload, dict) else None
	return (
		activity.get("source_doctype"),
		activity.get("source_docname"),
		activity.get("canonical_doctype"),
		activity.get("canonical_docname"),
		activity.get("activity_type"),
		activity.get("name") or str(activity.get("creation")),
		field,
	)


def _dedup(records: list, identity) -> list:
	"""Drop later records sharing a stable identity, preserving first-seen order."""
	seen = set()
	deduped = []
	for record in records:
		if not isinstance(record, dict):
			deduped.append(record)
			continue
		key = identity(record)
		if key in seen:
			continue
		seen.add(key)
		deduped.append(record)
	return deduped


def _sort_by_recency(records: list) -> list:
	"""Order newest first with a stable tie-break, in place, for deterministic chronology."""
	records.sort(key=_recency_key, reverse=True)
	return records


def _recency_key(record: dict):
	timestamp = _record_timestamp(record)
	return (
		get_datetime(timestamp) if timestamp else get_datetime("1900-01-01 00:00:00"),
		str(record.get("source_docname") or ""),
		str(record.get("name") or ""),
	)


def get_attachments(doctype: str, name: str):
	return (
		frappe.db.get_all(
			"File",
			filters={"attached_to_doctype": doctype, "attached_to_name": name},
			fields=[
				"name",
				"file_name",
				"file_type",
				"file_url",
				"file_size",
				"is_private",
				"modified",
				"creation",
				"owner",
			],
		)
		or []
	)


def handle_multiple_versions(versions: list):
	activities = []
	grouped_versions = []
	old_version = None
	for version in versions:
		is_version = version["activity_type"] in ["changed", "added", "removed"]
		if not is_version:
			activities.append(version)
		if not old_version:
			old_version = version
			if is_version:
				grouped_versions.append(version)
			continue
		if (
			is_version
			and old_version.get("owner")
			and version["owner"] == old_version["owner"]
			and version.get("target") == old_version.get("target")
		):
			grouped_versions.append(version)
		else:
			if grouped_versions:
				activities.append(parse_grouped_versions(grouped_versions))
			grouped_versions = []
			if is_version:
				grouped_versions.append(version)
		old_version = version
		if version == versions[-1] and grouped_versions:
			activities.append(parse_grouped_versions(grouped_versions))

	return activities


def parse_grouped_versions(versions: list):
	version = versions[0]
	if len(versions) == 1:
		return version
	other_versions = versions[1:]
	version["other_versions"] = other_versions
	return version


def attribute_to_lead(records: list, lead: str) -> list:
	"""Tag aggregated records with their originating Lead, in place.

	A converted Deal (and the Contact that reads its linked deals) shows the Lead's calls,
	notes and tasks. They are the Lead's own records, referenced not duplicated, so mark them
	as lead-sourced rather than making copies. Idempotent and defensive: a non-dict row (none
	are expected) is skipped.
	"""
	for record in records or []:
		if isinstance(record, dict):
			record["is_lead"] = True
			record["source_doctype"] = "CRM Lead"
			record["source_docname"] = lead
	return records


def _tag_call_events(calls: list) -> list:
	"""Layer the normalized event envelope onto parsed call records, in place.

	Calls surface in the main Activity feed alongside versions, so they carry the same canonical
	envelope. The CRM Call Log record referenced by (canonical_doctype, canonical_docname) stays
	the single home of the recording, transcript and note -- nothing is copied here.
	"""
	for call in calls or []:
		if not isinstance(call, dict):
			continue
		_with_event_envelope(
			call,
			occurred_at=call.get("start_time") or call.get("creation"),
			actor=call.get("caller") or call.get("receiver"),
			canonical_doctype="CRM Call Log",
			canonical_docname=call.get("name"),
			target={"doctype": "CRM Call Log", "name": call.get("name")},
		)
	return calls


def get_linked_calls(name: str):
	calls = frappe.db.get_all(
		"CRM Call Log",
		filters={"reference_docname": name},
		fields=[
			"name",
			"caller",
			"receiver",
			"from",
			"to",
			"duration",
			"start_time",
			"end_time",
			"status",
			"type",
			"recording_url",
			"creation",
			"note",
		],
	)

	linked_calls = frappe.db.get_all(
		"Dynamic Link", filters={"link_name": name, "parenttype": "CRM Call Log"}, pluck="parent"
	)

	notes = []
	tasks = []

	if linked_calls:
		CallLog = frappe.qb.DocType("CRM Call Log")
		Link = frappe.qb.DocType("Dynamic Link")
		query = (
			frappe.qb.from_(CallLog)
			.select(
				CallLog.name,
				CallLog.caller,
				CallLog.receiver,
				CallLog["from"],
				CallLog.to,
				CallLog.duration,
				CallLog.start_time,
				CallLog.end_time,
				CallLog.status,
				CallLog.type,
				CallLog.recording_url,
				CallLog.creation,
				CallLog.note,
				Link.link_doctype,
				Link.link_name,
			)
			.join(Link, JoinType.inner)
			.on(Link.parent == CallLog.name)
			.where(CallLog.name.isin(linked_calls))
		)
		_calls = query.run(as_dict=True)

		for call in _calls:
			if call.get("link_doctype") == "FCRM Note":
				notes.append(call.link_name)
			elif call.get("link_doctype") == "CRM Task":
				tasks.append(call.link_name)

		_calls = [call for call in _calls if call.get("link_doctype") not in ["FCRM Note", "CRM Task"]]
		if _calls:
			calls = calls + _calls

	if notes:
		notes = frappe.db.get_all(
			"FCRM Note",
			filters={"name": ("in", notes)},
			fields=["name", "title", "content", "owner", "modified", "creation"],
		)

	if tasks:
		tasks = frappe.db.get_all(
			"CRM Task",
			filters={"name": ("in", tasks)},
			fields=[
				"name",
				"title",
				"description",
				"assigned_to",
				"due_date",
				"priority",
				"status",
				"modified",
			],
		)

	calls = [parse_call_log(call) for call in calls] if calls else []

	return {"calls": calls, "notes": notes, "tasks": tasks}


def get_linked_notes(name: str):
	notes = frappe.db.get_all(
		"FCRM Note",
		filters={"reference_docname": name},
		fields=["name", "title", "content", "owner", "modified", "creation"],
	)
	return notes or []


def get_linked_tasks(name: str):
	tasks = frappe.db.get_all(
		"CRM Task",
		filters={"reference_docname": name},
		fields=[
			"name",
			"title",
			"description",
			"assigned_to",
			"due_date",
			"priority",
			"status",
			"modified",
			"creation",
		],
	)
	return tasks or []


def get_linked_task_activities(name: str):
	"""Yield normalized lifecycle events for the Tasks linked to a CRM Deal (TXB-133).

	Each Task contributes one ``creation`` event plus one ``field_change`` event per tracked change
	(status, assigned_to, due_date, priority) recorded in its own audit Versions. This is forward
	only: only changes Frappe captured after track_changes was enabled surface here, and no prior
	history is inferred or fabricated. Canonical old/new values stay in the CRM Task Version record;
	each event references it through the envelope and opens the Task itself.
	"""
	tasks = frappe.db.get_all(
		"CRM Task",
		filters={"reference_doctype": "CRM Deal", "reference_docname": name},
		fields=["name", "title", "owner", "creation"],
	)
	if not tasks:
		return []

	task_meta = frappe.get_meta("CRM Task")
	task_fields = {
		field.fieldname: {"label": field.label, "options": field.options}
		for field in task_meta.fields
		if field.fieldname in TASK_AUDITED_FIELDS
	}

	activities = []
	for task in tasks:
		creation_text = _("created a task {0}").format(task.title or task.name)
		creation_activity = {
			"activity_type": "creation",
			"creation": task.creation,
			"owner": task.owner,
			"data": creation_text,
			"is_lead": False,
		}
		_with_event_envelope(
			creation_activity,
			occurred_at=task.creation,
			actor=task.owner,
			canonical_doctype="CRM Task",
			canonical_docname=task.name,
			summary=creation_text,
			target={"doctype": "CRM Task", "name": task.name},
		)
		activities.append(creation_activity)

		versions = frappe.get_all(
			"Version",
			filters={"ref_doctype": "CRM Task", "docname": task.name},
			fields=["name", "data", "owner", "creation"],
			order_by="creation asc",
		)
		for version in versions:
			activities.extend(
				_version_field_events(
					version,
					task_fields,
					avoid_fields=[],
					is_lead=False,
					container_doctype="CRM Task",
					container_docname=task.name,
				)
			)

	return activities


def parse_attachment_log(html: str, type: str):
	soup = BeautifulSoup(html, "html.parser")
	a_tag = soup.find("a")
	type = "added" if type == "Attachment" else "removed"
	if not a_tag:
		return {
			"type": type,
			"file_name": html.replace("Removed ", ""),
			"file_url": "",
			"is_private": False,
		}

	is_private = False
	if "private/files" in a_tag["href"]:
		is_private = True

	return {
		"type": type,
		"file_name": a_tag.text,
		"file_url": a_tag["href"],
		"is_private": is_private,
	}


def is_translatable(doctype: str) -> bool:
	return doctype in get_translated_doctypes()

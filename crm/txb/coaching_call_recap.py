"""The Coaching Call recap ledger: one consent decision and content snapshot per submission (TXB-273).

Log Coaching Call records, alongside its Note and in the same transaction, whether the coach
chose to email the client a recap, the address it would go to, and the exact content it would
carry. Nothing here sends mail: a `queued` row is what the dispatch consumer picks up, and an
`opted_out` row is the audit trail of a coach declining. Only the action calls `create_recap`,
so a Coaching Call Note inserted or edited any other way records no recap.
"""

import frappe
from frappe import _
from frappe.utils import escape_html, getdate, now_datetime, validate_email_address

from crm.fcrm.doctype.crm_deal.crm_deal import get_effective_primary_contact

RECAP_DOCTYPE = "CRM Coaching Call Recap"
CONTACT_DOCTYPE = "Contact"

STATUS_OPTED_OUT = "opted_out"
STATUS_QUEUED = "queued"

RECAP_RECIPIENT_MISSING = "RECAP_RECIPIENT_MISSING"


class RecapRecipientMissing(frappe.ValidationError):
	"""A recap was requested but the Deal has no usable primary client email."""


SEND_RECAP_YES = ("1", "true", "yes")
SEND_RECAP_NO = ("0", "false", "no")


def wants_recap(data: dict) -> bool:
	"""Whether the coach asked for the recap; an omitted or null `send_recap` means yes.

	Anything other than 1/0, true/false or yes/no is refused rather than read as an opt-out.
	"""
	value = data.get("send_recap")
	if value is None or value == "":
		return True
	if isinstance(value, int | float):
		return bool(value)
	text = str(value).strip().lower()
	if text in SEND_RECAP_YES:
		return True
	if text in SEND_RECAP_NO:
		return False
	frappe.throw(
		_("Invalid send_recap value {0}: use 1/0, true/false or yes/no.").format(frappe.bold(value)),
		frappe.ValidationError,
	)


def require_recipient(deal, data: dict) -> tuple[str | None, str | None]:
	"""The Deal's effective primary Contact and its valid email, when a recap is requested.

	A requested recap with no usable address is refused. Called before the action writes
	anything, so the refusal leaves no Note, recap or task.
	"""
	if not wants_recap(data):
		return None, None
	contact = get_effective_primary_contact(deal.get("contacts") or [])
	email = frappe.db.get_value(CONTACT_DOCTYPE, contact, "email_id") if contact else None
	email = validate_email_address((email or "").strip()) or None
	if not email:
		frappe.throw(
			f"{RECAP_RECIPIENT_MISSING}: "
			+ _("The client has no usable primary email. Add one, or untick sending the recap."),
			RecapRecipientMissing,
			title=_("Recap recipient missing"),
		)
	return contact, email


def find_submission(deal_name: str, submission_id: str | None) -> dict | None:
	"""The recap already recorded for `submission_id`, read under the caller's Deal lock.

	A locking read, so a submission committed by a request this one queued behind on the lock is
	seen even though this transaction's snapshot predates it.
	"""
	if not submission_id:
		return None
	row = frappe.db.get_value(
		RECAP_DOCTYPE,
		{"submission_id": submission_id},
		["name", "deal", "note", "status"],
		as_dict=True,
		for_update=True,
	)
	if row and row.deal != deal_name:
		frappe.throw(
			_("Submission {0} belongs to another Opportunity.").format(submission_id),
			frappe.ValidationError,
		)
	return row


def build_snapshot(subject: str, data: dict) -> dict:
	"""The recap content exactly as submitted, with the coach's notes escaped for HTML.

	Escaping only touches markup characters, so Lithuanian letters (ąčęėįšųūž) pass unchanged.
	"""
	notes = (data.get("call_notes") or "").replace("\r\n", "\n")
	return {
		"subject": subject,
		"call_status": data.get("call_status"),
		"delivery_date": str(getdate(data["delivery_date"])) if data.get("delivery_date") else None,
		"topic": data.get("topic") or None,
		"call_notes_html": escape_html(notes).replace("\n", "<br>"),
		"next_call_date": (
			str(getdate(data["next_call_date"])) if data.get("next_call_date") else None
		),
		"is_last_call": 1 if data.get("is_last_call") else 0,
	}


def create_recap(deal, note, data: dict, recipient: tuple[str | None, str | None], subject: str):
	"""Insert this submission's recap: `queued` to `recipient` when consented, else `opted_out`."""
	consent = wants_recap(data)
	contact, email = recipient
	user = frappe.session.user
	return frappe.get_doc(
		{
			"doctype": RECAP_DOCTYPE,
			"submission_id": data.get("submission_id") or frappe.generate_hash(length=20),
			"deal": deal.name,
			"note": note.name,
			"consent": 1 if consent else 0,
			"consent_by": user,
			"consent_at": now_datetime(),
			"recipient_contact": contact,
			"recipient_email": email,
			# Stored unescaped as UTF-8, so the raw column reads ą rather than ą.
			"content_snapshot": frappe.as_json(build_snapshot(subject, data), ensure_ascii=False),
			"status": STATUS_QUEUED if consent else STATUS_OPTED_OUT,
			"attempts": 0,
			"created_by": user,
		}
	).insert(ignore_permissions=True)


def recap_result(note: str, name: str, status: str) -> dict:
	"""The action's response payload for one logged (or replayed) coaching call."""
	return {"note": note, "recap": {"name": name, "status": status}}

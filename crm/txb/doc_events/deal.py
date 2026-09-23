"""CRM Deal document events.

Ported from the `Generate Registration Token`, `Sync Deal Contact Name` and
`Sync Delivery Coach Name` Server Scripts.
"""

import secrets

import frappe
from frappe import _
from frappe.utils import get_url

from crm.txb.admin_assignment import resolve_admin_task_assignee
from crm.txb.constants import (
	ADMIN_ROLE,
	FIELD_DELIVERY_COACH,
	FIELD_DELIVERY_COACH_NAME,
	FIELD_REGISTRATION_LINK,
	FIELD_REGISTRATION_SOURCE_DEAL,
	FIELD_REGISTRATION_TOKEN,
	FIELD_WORKSHOP_SCHEDULED_AT,
	PIPELINE_DELIVERING_COACHING,
	PIPELINE_WORKSHOP,
	REGISTRATION_PAGE_ROUTE,
	REGISTRATION_TOKEN_BYTES,
	STATUS_WORKSHOP_SET,
)
from crm.txb.pipelines.common import add_task, deal_link
from crm.txb.pipelines.delivering_coaching import is_entering_active, require_activation_readiness


def require_workshop_schedule(doc, method=None):
	"""A Workshop deal may not rest in "Workshop set" with no scheduled date and time.

	The native Workshop action collects ``custom_workshop_scheduled_at`` before it moves the
	deal, so a transition through the action flow always satisfies this. What this guards is
	the other door: a direct document edit or a bare API write that flips ``status`` to
	"Workshop set" without going through the action, which the retired Form Script used to
	catch on the client and nothing enforced on the server.

	The CRM "Admin" role keeps a direct-write hatch. Some edges the graph does not describe
	still need a human with the authority to set them by hand; enforcing this for everyone
	would trap those cases behind a field the action flow is the only supported way to fill.
	"""
	if doc.pipeline_type != PIPELINE_WORKSHOP or doc.status != STATUS_WORKSHOP_SET:
		return

	# A site without the field has no scheduling feature to enforce; guarding an absent
	# field would refuse every Workshop set write there instead.
	if not doc.meta.has_field(FIELD_WORKSHOP_SCHEDULED_AT):
		return

	if doc.get(FIELD_WORKSHOP_SCHEDULED_AT):
		return

	if ADMIN_ROLE in frappe.get_roles():
		return

	frappe.throw(
		_("A workshop date and time must be scheduled before the deal can be set to {0}.").format(
			STATUS_WORKSHOP_SET
		),
		title=_("Workshop Not Scheduled"),
	)


def require_delivery_readiness(doc, method=None):
	"""A Delivering Coaching deal may enter Active only once it is delivery-ready (TXB-251).

	The Take Action flow checks the same rule before its handler runs; this is the boundary
	every other door meets -- Kanban drag, the status control, direct saves and REST writes.
	Runs in `validate`, after `sync_delivery_coach_name` has derived the coach's name in
	`before_validate`. Only the transition is gated: deals already Active save freely.
	"""
	if is_entering_active(doc):
		require_activation_readiness(doc)


def issue_registration_link(doc) -> str:
	"""Give a Workshop deal its public registration link, minting the token once.

	The token guards a guest-accessible endpoint that creates Contacts, Organizations and
	Deals, so it is the only thing standing between the public and a write into the CRM.
	It must therefore be unguessable.

	The previous implementation built the token from ``now() + doc.name``, stripped
	non-alphanumerics and took the last 16 characters. A stripped deal name is itself 16
	characters, so the slice discarded the timestamp entirely and the token was simply the
	deal number in lower case -- trivially enumerable. It is now drawn from `secrets`.

	The link is built from the serving site (`get_url`), so staging links point at staging.
	"""
	if not doc.get(FIELD_REGISTRATION_TOKEN):
		doc.set(FIELD_REGISTRATION_TOKEN, secrets.token_urlsafe(REGISTRATION_TOKEN_BYTES))
	link = f"{get_url()}/{REGISTRATION_PAGE_ROUTE}?token={doc.get(FIELD_REGISTRATION_TOKEN)}"
	doc.set(FIELD_REGISTRATION_LINK, link)
	return link


def generate_registration_token(doc, method=None):
	"""Issue the registration link automatically once a Workshop deal reaches "Workshop set".
	The Generate Link button on the deal page issues it on demand at any status."""
	if doc.pipeline_type != PIPELINE_WORKSHOP or doc.status != STATUS_WORKSHOP_SET:
		return
	issue_registration_link(doc)


def sync_delivery_coach_name(doc, method=None):
	"""Denormalise the delivery coach's full name for display and export."""
	if not doc.get(FIELD_DELIVERY_COACH):
		doc.set(FIELD_DELIVERY_COACH_NAME, None)
		return

	coach = doc.get(FIELD_DELIVERY_COACH)
	full_name = frappe.db.get_value("User", coach, "full_name")
	doc.set(FIELD_DELIVERY_COACH_NAME, full_name or coach)


def create_coaching_admin_task(doc, method=None):
	"""A new Delivering Coaching deal lands in the Admin's task list (TXB-208).

	Fires on every real insert, which is what makes it idempotent: the handover flow
	dedupes by reusing the canonical delivery deal, so a retry never re-inserts and never
	re-tasks. Workshop QR registration deals (also this pipeline, one per attendee) are
	excluded -- a task per registrant would bury the Admin; they are recognisable by
	``custom_source_deal``, which only the registration path writes.

	Best-effort: the deal must never fail to create because the task could not.
	"""
	if doc.pipeline_type != PIPELINE_DELIVERING_COACHING:
		return
	if doc.get(FIELD_REGISTRATION_SOURCE_DEAL):
		return

	try:
		assignee = resolve_admin_task_assignee()
		first_name = frappe.db.get_value("User", assignee, "first_name") or assignee
		message = f"{first_name}, įkrito naujas delivering coaching deal'as"
		add_task(
			doc,
			title=message,
			description=f"{message} - {deal_link(doc.name)}",
			assigned_to=assignee,
		)
	except Exception as e:
		frappe.log_error(
			title="create_coaching_admin_task",
			message=f"Failed to create Admin task for {doc.name}. {e}",
		)

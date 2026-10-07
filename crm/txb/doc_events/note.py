"""FCRM Note document events keeping coaching call totals reconciled (TXB-247).

A note is the record of a coaching call, so the deal's Total Completed Calls has to follow the
notes wherever they go: a new call logged, a status corrected in place, a note deleted, or a
note re-pointed from one Opportunity to another. Every one of those recounts from scratch, so
there is no arithmetic to drift and no event that can be missed twice.

Only Delivering Coaching deals are touched; `crm.txb.coaching_calls.reconcile_deal` is the one
place that rule lives.
"""

from crm.txb import human_contact
from crm.txb.coaching_calls import (
	DEAL_DOCTYPE,
	classify_note_status,
	is_coaching_call_note,
	lock_deal_for_first_call,
	reconcile_deal,
	seed_first_call_date,
	status_field_installed,
)
from crm.txb.constants import FIELD_COACHING_CALL_STATUS


def classify_coaching_call(doc, method=None):
	"""Keep the note's app-owned status metadata matching the status its body states.

	Bound before validation so a note logged by the workflow, imported, or edited by hand all
	leave the same structured value behind. A body with no readable status never clears an
	already-stored one -- a coaching note the workflow seeded keeps its status even if someone
	rewrites the body around it.
	"""
	if not status_field_installed():
		return

	status = classify_note_status(
		doc.title or "", doc.content or "", doc.get(FIELD_COACHING_CALL_STATUS)
	)
	if status:
		doc.set(FIELD_COACHING_CALL_STATUS, status)


def lock_first_call_deal(doc, method=None):
	"""Lock the Opportunity a Coaching Call Note is about to join, so first-call seeding is serial."""
	lock_deal_for_first_call(doc)


def seed_first_coaching_call_date(doc, method=None):
	"""Seed the deal's empty First Coaching Call Date from its first Coaching Call Note (TXB-224).

	Bound to insert only: the date is a one-time seed, so a later edit, delete or move of the
	note never re-derives it.
	"""
	seed_first_call_date(doc)


def reconcile_coaching_totals(doc, method=None):
	"""Recount every deal this note's lifecycle event could have changed.

	Bound to insert, update and delete. An update carries two deals when the note's reference
	moved, and both are recounted -- the one it left and the one it joined.
	"""
	for deal in _affected_deals(doc):
		reconcile_deal(deal)


def _affected_deals(doc) -> set:
	"""The deal names to recount: the note's current reference and the one it just left."""
	deals = set()

	for state in (doc, doc.get_doc_before_save()):
		if not state:
			continue
		if state.get("reference_doctype") != DEAL_DOCTYPE or not state.get("reference_docname"):
			continue
		if not _is_relevant(state):
			continue
		deals.add(state.get("reference_docname"))

	return deals


def _is_relevant(state) -> bool:
	"""Whether a note version is a coaching call note, by its metadata or its title.

	Ordinary notes are ignored outright: nothing about a hold reason or a contract note can
	change a completed call count, and a deal with hundreds of notes should not be recounted
	every time one of them is touched.
	"""
	return bool(state.get(FIELD_COACHING_CALL_STATUS)) or is_coaching_call_note(state.get("title"))


def sync_human_contact(doc, method=None):
	"""Keep a logged coaching call's Human Contact Event in step with its Note (TXB-285).

	Bound to insert and update. Only a Note the Log Coaching Call action recorded has an event,
	so an edit away from Completed voids it and an edit back un-voids the same row, while a Note
	inserted by any other path records nothing.
	"""
	human_contact.sync_doc(doc)


def void_human_contact(doc, method=None):
	"""A deleted coaching call Note voids its Human Contact Event, if it had one (TXB-285)."""
	human_contact.sync_source(doc.doctype, doc.name)

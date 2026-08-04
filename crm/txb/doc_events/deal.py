"""CRM Deal document events.

Ported from the `Generate Registration Token`, `Sync Deal Contact Name` and
`Sync Delivery Coach Name` Server Scripts.
"""

import secrets

import frappe

from crm.txb.constants import (
	FIELD_DELIVERY_COACH,
	FIELD_DELIVERY_COACH_NAME,
	FIELD_REGISTRATION_LINK,
	FIELD_REGISTRATION_TOKEN,
	PIPELINE_WORKSHOP,
	REGISTRATION_BASE_URL,
	REGISTRATION_TOKEN_BYTES,
	STATUS_WORKSHOP_SET,
)


def generate_registration_token(doc, method=None):
	"""Issue a registration token once a Workshop deal reaches "Workshop set".

	The token guards a guest-accessible endpoint that creates Contacts, Organizations and
	Deals, so it is the only thing standing between the public and a write into the CRM.
	It must therefore be unguessable.

	The previous implementation built the token from ``now() + doc.name``, stripped
	non-alphanumerics and took the last 16 characters. A stripped deal name is itself 16
	characters, so the slice discarded the timestamp entirely and the token was simply the
	deal number in lower case -- trivially enumerable. It is now drawn from `secrets`.
	"""
	if doc.pipeline_type != PIPELINE_WORKSHOP or doc.status != STATUS_WORKSHOP_SET:
		return

	if not doc.get(FIELD_REGISTRATION_TOKEN):
		doc.set(FIELD_REGISTRATION_TOKEN, secrets.token_urlsafe(REGISTRATION_TOKEN_BYTES))

	doc.set(
		FIELD_REGISTRATION_LINK,
		f"{REGISTRATION_BASE_URL}/registration?token={doc.get(FIELD_REGISTRATION_TOKEN)}",
	)


def sync_contact_name(doc, method=None):
	"""Fill the deal's person name from its primary contact.

	Deals created from a Contact page get the contact linked but not the name fields.
	Only empty fields are filled, so a name entered on the deal always wins.
	"""
	if doc.first_name and doc.last_name:
		return

	contact_name = primary_contact(doc)
	if not contact_name:
		return

	contact = frappe.get_doc("Contact", contact_name)
	if not doc.first_name and contact.first_name:
		doc.first_name = contact.first_name
	if not doc.last_name and contact.last_name:
		doc.last_name = contact.last_name


def primary_contact(doc):
	"""The contact flagged primary, else the first one, else None."""
	contacts = doc.get("contacts") or []

	for row in contacts:
		if row.is_primary:
			return row.contact

	return contacts[0].contact if contacts else None


def sync_delivery_coach_name(doc, method=None):
	"""Denormalise the delivery coach's full name for display and export."""
	if not doc.get(FIELD_DELIVERY_COACH):
		doc.set(FIELD_DELIVERY_COACH_NAME, None)
		return

	coach = doc.get(FIELD_DELIVERY_COACH)
	full_name = frappe.db.get_value("User", coach, "full_name")
	doc.set(FIELD_DELIVERY_COACH_NAME, full_name or coach)

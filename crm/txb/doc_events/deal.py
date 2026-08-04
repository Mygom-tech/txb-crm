"""CRM Deal document events."""

import secrets

from crm.txb.constants import (
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

# Copyright (c) 2026, Mygom and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.patches.v1_0.reissue_registration_tokens import is_predictable
from crm.txb.constants import (
	FIELD_REGISTRATION_LINK,
	FIELD_REGISTRATION_TOKEN,
	PIPELINE_WORKSHOP,
	STATUS_WORKSHOP_SET,
)
from crm.txb.doc_events.deal import generate_registration_token


class FakeDeal:
	"""Minimal stand-in for a CRM Deal, so the generator can be tested without the DB."""

	def __init__(self, name, pipeline_type, status, token=None):
		self.name = name
		self.pipeline_type = pipeline_type
		self.status = status
		self._values = {FIELD_REGISTRATION_TOKEN: token, FIELD_REGISTRATION_LINK: None}

	def get(self, fieldname):
		return self._values.get(fieldname)

	def set(self, fieldname, value):
		self._values[fieldname] = value


def workshop_deal(name="CRM-DEAL-2026-00356", token=None):
	return FakeDeal(name, PIPELINE_WORKSHOP, STATUS_WORKSHOP_SET, token)


class TestRegistrationToken(FrappeTestCase):
	def test_token_is_not_derived_from_deal_name(self):
		"""The old scheme made the token the deal number; it must not be guessable now."""
		deal = workshop_deal()
		generate_registration_token(deal)

		token = deal.get(FIELD_REGISTRATION_TOKEN)
		self.assertTrue(token)
		self.assertNotEqual(token, "crmdeal202600356")
		self.assertFalse(is_predictable(deal.name, token))
		# The deal number must not appear in the token in any casing.
		self.assertNotIn("crmdeal", token.lower())

	def test_tokens_are_unique_across_deals(self):
		tokens = set()
		for i in range(50):
			deal = workshop_deal(name=f"CRM-DEAL-2026-{i:05d}")
			generate_registration_token(deal)
			tokens.add(deal.get(FIELD_REGISTRATION_TOKEN))

		self.assertEqual(len(tokens), 50)

	def test_token_is_long_enough_to_resist_guessing(self):
		deal = workshop_deal()
		generate_registration_token(deal)
		self.assertGreaterEqual(len(deal.get(FIELD_REGISTRATION_TOKEN)), 32)

	def test_existing_token_is_preserved(self):
		"""Re-saving a deal must not invalidate a link already sent out."""
		deal = workshop_deal(token="already-issued-token")
		generate_registration_token(deal)
		self.assertEqual(deal.get(FIELD_REGISTRATION_TOKEN), "already-issued-token")

	def test_link_is_built_from_the_token(self):
		deal = workshop_deal()
		generate_registration_token(deal)

		token = deal.get(FIELD_REGISTRATION_TOKEN)
		self.assertEqual(
			deal.get(FIELD_REGISTRATION_LINK),
			f"https://crm.txbconsulting.com/registration?token={token}",
		)

	def test_no_token_for_other_pipelines_or_statuses(self):
		for pipeline, status in [
			("Individual Session", STATUS_WORKSHOP_SET),
			(PIPELINE_WORKSHOP, "VCS call run"),
		]:
			deal = FakeDeal("CRM-DEAL-2026-00001", pipeline, status)
			generate_registration_token(deal)
			self.assertIsNone(deal.get(FIELD_REGISTRATION_TOKEN))

	def test_is_predictable_detects_the_old_scheme(self):
		"""Guards the patch: old tokens are reissued, random ones are left alone."""
		self.assertTrue(is_predictable("CRM-DEAL-2026-00356", "crmdeal202600356"))
		self.assertFalse(is_predictable("CRM-DEAL-2026-00356", "kJ8-x_Qa2ZpL9vRt"))
		self.assertFalse(is_predictable("CRM-DEAL-2026-00356", ""))

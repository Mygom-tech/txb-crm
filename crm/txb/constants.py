"""Shared constants for TXB customisations.

Replaces magic strings that were previously repeated across Server Script records.
"""

# Pipeline types -- the `pipeline_type` Select on CRM Deal.
PIPELINE_INDIVIDUAL_SESSION = "Individual Session"
PIPELINE_WORKSHOP = "Workshop"
PIPELINE_SELLING_TRAINING = "Selling Training"
PIPELINE_DELIVERING_COACHING = "Delivering Coaching"

# Deal statuses referenced by automation.
STATUS_WORKSHOP_SET = "Workshop set"

# Custom fieldnames.
FIELD_REGISTRATION_TOKEN = "custom_registration_token"
FIELD_REGISTRATION_LINK = "custom_registration_link"

# Number of random bytes behind a registration token. 32 bytes of urandom is far beyond
# guessing range and keeps the URL a reasonable length.
REGISTRATION_TOKEN_BYTES = 32

# Public base URL used to build registration links.
# TODO: move to site config so non-production environments generate their own links.
REGISTRATION_BASE_URL = "https://crm.txbconsulting.com"

# Delivery coach fields on CRM Deal.
FIELD_DELIVERY_COACH = "custom_delivery_coach"
FIELD_DELIVERY_COACH_NAME = "custom_delivery_coach_name"

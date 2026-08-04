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

# The CRM calls this role "Admin" in Settings > Users; it maps to the Frappe role
# System Manager (see frontend/src/components/Settings/Users.vue and stores/users.js).
ADMIN_ROLE = "System Manager"

# Fields that express a deal's delivery state. `custom_delivery_status` duplicates
# `status` for the Delivering Coaching pipeline and is blank on almost every deal; it is
# guarded alongside `status` so it cannot be used to sidestep the restriction. Removing it
# is tracked separately.
STATUS_FIELDS = ("status", "custom_delivery_status")

# The statuses each pipeline may use, in display order.
#
# Single source of truth. This previously existed as four separate copies inside Form
# Script records, which had already drifted: one offered "Training RFQ received", a status
# that does not exist in CRM Deal Status at all.
#
# Deliberately many-to-many -- "Lost" belongs to both Individual Session and Workshop,
# "Submitted" to both Individual Session and Delivering Coaching -- so this cannot be
# expressed as a single field on CRM Deal Status.
#
# Statuses absent from every list (Delivery, Demo/Making, Discovery, Proposal Sent,
# Training submitted, Workshop Delivered) are unused stock leftovers; no deal uses them.
PIPELINE_STATUSES = {
	PIPELINE_INDIVIDUAL_SESSION: [
		"Submitted",
		"Session Set",
		"Session Run",
		"Follow-up",
		"Won",
		"Lost",
	],
	PIPELINE_WORKSHOP: [
		"Workshop submitted",
		"VCS call set",
		"VCS call run",
		"Workshop set",
		"Workshop ran",
		"Workshop rescheduling in progress",
		"Sold",
		"Lost",
	],
	PIPELINE_SELLING_TRAINING: [
		"Training discovery meeting set",
		"Training discovery meeting run",
		"Training proposal submitted",
		"Training proposal meeting set",
		"Training proposal meeting run",
		"Training negotiations",
		"Contract signed",
		"Training date set",
		"Training run",
		"Training not interested",
	],
	PIPELINE_DELIVERING_COACHING: [
		"Submitted",
		"Waiting on Review",
		"Contract Cleared",
		"Active",
		"On Hold",
		"Payment Hold",
		"Inactive",
	],
}

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

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
		# The pipeline's entry status. Both Form Script copies of this map omitted it, so
		# it could never be selected and "Set Discovery Meeting" -- which starts from it --
		# was unreachable.
		"Training submitted",
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

# Lead conversion is restricted to a subset of pipelines, and the deal's initial state is
# derived from the chosen pipeline server-side (TXB-125). This is the single source of
# truth for both the allowed pipelines and their required entry status, so the interactive
# modal, bulk conversion, and the whitelisted convert endpoint cannot drift or be bypassed.
#
# The entry status is intentionally the first status of each pipeline in PIPELINE_STATUSES,
# but is pinned explicitly here so a reordering of that display list can never silently
# change which state a freshly converted deal lands in.
CONVERSION_PIPELINE_INITIAL_STATUS = {
	PIPELINE_INDIVIDUAL_SESSION: "Submitted",
	PIPELINE_WORKSHOP: "Workshop submitted",
	PIPELINE_SELLING_TRAINING: "Training submitted",
}

# The Lead status a dial attempt lands on. "Contact attempted" may only be reached by the
# canonical Log a dial action (crm.txb.lead_actions): a Kanban drag, the status dropdown and
# Take Action all route through it, and crm.txb.lead_actions.guard_contact_attempted rejects
# any other write to this status. Named once here so the guard, the action's to_state and the
# browser's requiresDial() check cannot drift.
LEAD_STATUS_CONTACT_ATTEMPTED = "Contact attempted"

# Final CRM Call Log statuses a user may select when manually logging a completed dial attempt.
# Transient telephony states (Initiated, Ringing, In Progress, Queued) are intentionally excluded:
# Log a dial records what happened after the attempt, not a live provider lifecycle state.
DIAL_RESULTS = ("Completed", "Failed", "Busy", "No Answer", "Canceled")

# The Lead status a booked discovery meeting rests at before it is run. "Run Discovery Meeting"
# (crm.txb.lead_actions.run_discovery_meeting) is a guarded action available only from here:
# it records meeting notes and applies exactly one outcome in a single transaction, so the lead
# is never left in a stranded "meeting run" resting state. Named once so the action's
# from-state, the seeded status, and the browser's requiresDiscovery() check cannot drift.
LEAD_STATUS_DISCOVERY_MEETING_SET = "Discovery meeting set"

# The three non-conversion outcomes of a discovery meeting, each a resting Lead status.
LEAD_STATUS_NURTURE = "Nurture"
LEAD_STATUS_NOT_INTERESTED = "Not interested"
LEAD_STATUS_DISQUALIFIED = "Disqualified"

# A discovery meeting closes on exactly one of six outcomes. Three keep the lead as a lead
# (mapped here to the resting Lead status each lands on); the other three convert it into a
# pipeline-correct Opportunity and reuse the existing conversion authority
# (crm.fcrm.doctype.crm_lead.crm_lead.convert_to_deal), so the convertible set is exactly
# CONVERSION_PIPELINE_INITIAL_STATUS.
DISCOVERY_STATUS_OUTCOMES = {
	LEAD_STATUS_NURTURE: LEAD_STATUS_NURTURE,
	LEAD_STATUS_NOT_INTERESTED: LEAD_STATUS_NOT_INTERESTED,
	LEAD_STATUS_DISQUALIFIED: LEAD_STATUS_DISQUALIFIED,
}

# The terminal discovery outcomes. Once a lead rests in one of these closed (Lost-type)
# statuses, only an Admin may reopen it -- move it to any other status --
# (crm.txb.lead_actions.guard_discovery_outcome). "Nurture" is deliberately excluded: it is a
# warm resting state a user keeps working, not a closed one.
DISCOVERY_TERMINAL_OUTCOMES = (LEAD_STATUS_NOT_INTERESTED, LEAD_STATUS_DISQUALIFIED)

# Custom fieldnames.
FIELD_REGISTRATION_TOKEN = "custom_registration_token"
FIELD_REGISTRATION_LINK = "custom_registration_link"

# The scheduled workshop date and time, collected by the native Set Workshop action. A
# Workshop deal may not rest in "Workshop set" without it; see
# `crm.txb.doc_events.deal.require_workshop_schedule`.
FIELD_WORKSHOP_SCHEDULED_AT = "custom_workshop_scheduled_at"

# Number of random bytes behind a registration token. 32 bytes of urandom is far beyond
# guessing range and keeps the URL a reasonable length.
REGISTRATION_TOKEN_BYTES = 32

# Public base URL used to build registration links.
# TODO: move to site config so non-production environments generate their own links.
REGISTRATION_BASE_URL = "https://crm.txbconsulting.com"

# Delivery coach fields on CRM Deal.
FIELD_DELIVERY_COACH = "custom_delivery_coach"
FIELD_DELIVERY_COACH_NAME = "custom_delivery_coach_name"

# The field carrying commission-bearing ownership, per doctype.
#
# Single source of truth: `crm.permissions.org_hierarchy` scopes record visibility by the
# same fields, and `crm.txb.ownership` guards them. Contact's is a site Custom Field, so
# every consumer checks `meta.has_field` before using it.
OWNER_FIELDS = {
	"CRM Lead": "lead_owner",
	"CRM Deal": "deal_owner",
	"Contact": "custom_contact_owner",
}

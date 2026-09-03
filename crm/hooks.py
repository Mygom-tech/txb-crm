app_name = "crm"
app_title = "Frappe CRM"
app_publisher = "Frappe Technologies Pvt. Ltd."
app_description = "Kick-ass Open Source CRM"
app_email = "shariq@frappe.io"
app_license = "AGPLv3"
app_icon_url = "/assets/crm/images/logo.svg"
app_icon_title = "CRM"
app_icon_route = "/crm"

# Apps
# ------------------

# required_apps = []
add_to_apps_screen = [
	{
		"name": "crm",
		"logo": "/assets/crm/images/logo.svg",
		"title": "CRM",
		"route": "/crm",
		"has_permission": "crm.api.check_app_permission",
	}
]

get_site_info = "crm.activation.get_site_info"

export_python_type_annotations = True
require_type_annotated_api_methods = True

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/crm/css/crm.css"
# app_include_js = "/assets/crm/js/crm.js"

# include js, css files in header of web template
# web_include_css = "/assets/crm/css/crm.css"
# web_include_js = "/assets/crm/js/crm.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "crm/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
	"Quotation": "public/js/erpnext_quotation_prefill.js",
	"Sales Order": "public/js/erpnext_sales_order_customer.js",
	"CRM Lead": "public/js/domain_enrichment.js",
	"CRM Organization": "public/js/domain_enrichment.js",
	"CRM Deal": "public/js/domain_enrichment.js",
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# "Role": "home_page"
# }

website_route_rules = [
	{"from_route": "/crm/<path:app_path>", "to_route": "crm"},
	{"from_route": "/crm-form/<route>", "to_route": "crm_form"},
]

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# "methods": "crm.utils.jinja_methods",
# "filters": "crm.utils.jinja_filters"
# }

# Setup wizard
# setup_wizard_requires = "assets/crm/js/setup_wizard.js"
# setup_wizard_stages = "crm.setup.setup_wizard.setup_wizard.get_setup_stages"
setup_wizard_complete = "crm.demo.api.create_demo_data"
# setup_wizard_test = "crm.setup.setup_wizard.test_setup_wizard.run_setup_wizard_test"

# Installation
# ------------

before_install = "crm.install.before_install"
after_install = "crm.install.after_install"

# Uninstallation
# ------------

before_uninstall = "crm.uninstall.before_uninstall"
# after_uninstall = "crm.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "crm.utils.before_app_install"
# after_app_install = "crm.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "crm.utils.before_app_uninstall"
# after_app_uninstall = "crm.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "crm.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

permission_query_conditions = {
	"CRM Lead": "crm.permissions.org_hierarchy.get_lead_permission_query_conditions",
	"CRM Deal": "crm.permissions.org_hierarchy.get_deal_permission_query_conditions",
	"CRM Notification": "crm.fcrm.doctype.crm_notification.crm_notification.get_permission_query_conditions",
}

has_permission = {
	"CRM Lead": "crm.permissions.org_hierarchy.has_lead_permission",
	"CRM Deal": "crm.permissions.org_hierarchy.has_deal_permission",
	"CRM Notification": "crm.fcrm.doctype.crm_notification.crm_notification.has_permission",
}

# DocType Class
# ---------------
# Override standard doctype classes

override_doctype_class = {
	"Contact": "crm.overrides.contact.CustomContact",
	"Email Template": "crm.overrides.email_template.CustomEmailTemplate",
}

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Contact": {
		"before_insert": ["crm.txb.ownership.claim_owner_on_insert"],
		"before_validate": ["crm.txb.doc_events.contact.sync_organization"],
		"validate": ["crm.api.contact.validate", "crm.txb.ownership.guard_owner_change"],
	},
	"CRM Lead": {
		# prevent_duplicate first: it throws, so nothing else should run before it.
		"before_insert": [
			"crm.txb.doc_events.lead.prevent_duplicate",
			"crm.txb.ownership.claim_owner_on_insert",
		],
		"before_validate": ["crm.txb.doc_events.lead.default_disqualified_reason"],
		# Reach guard first, so a user moving status and owner together hears the more
		# specific "Log a reach" message. This is the server-side enforcement point for
		# TXB-128: every route into Contacted (kanban drag, mobile, bulk, direct API) must
		# go through log_reach, not just the two Lead.vue handlers.
		"validate": [
			# Archived (converted) Leads reject user edits first, so no later validator runs
			# against a record that is closed to mutation (TXB-132).
			"crm.txb.doc_events.lead.guard_archived_lead",
			"crm.txb.doc_events.lead.require_reach_for_contacted",
			# Discovery meeting set is reachable only through
			# crm.txb.api.actions.schedule_discovery; every other write to that status is
			# refused here (TXB-129).
			"crm.txb.doc_events.lead.require_discovery_details",
			# Follow-up is reachable only through crm.txb.api.actions.schedule_follow_up and
			# Nurture only through crm.txb.api.actions.set_nurture (or, for Nurture, the
			# discovery-meeting outcome); every other write to those statuses is refused
			# here (TXB-210).
			"crm.txb.doc_events.lead.require_follow_up_context",
			"crm.txb.doc_events.lead.require_nurture_context",
			"crm.txb.ownership.guard_owner_change",
			# Contact attempted is reachable only through crm.txb.lead_actions.log_a_dial;
			# every other write to that status is refused here.
			"crm.txb.lead_actions.guard_contact_attempted",
			# A terminal discovery outcome (Not interested, Disqualified) may only be reopened
			# by an Admin; every other user's attempt to move the status out is refused here.
			"crm.txb.lead_actions.guard_discovery_outcome",
			# Discovery meeting run is a guarded trigger, never a resting state: every write
			# that would strand a lead there is refused so the flow can only go through
			# run_discovery_meeting.
			"crm.txb.lead_actions.guard_discovery_meeting_run",
		],
	},
	"CRM Call Log": {
		"before_validate": ["crm.txb.doc_events.call_log.default_phone_numbers"],
		"after_insert": ["crm.txb.doc_events.call_log.update_deal_call_count"],
		"on_update": ["crm.txb.doc_events.call_log.update_deal_call_count"],
		"after_delete": ["crm.txb.doc_events.call_log.update_deal_call_count"],
	},
	"ToDo": {
		"after_insert": ["crm.api.todo.after_insert"],
		"on_update": ["crm.api.todo.on_update"],
	},
	"Communication": {
		"after_insert": ["crm.utils.on_communication_insert"],
		"on_update": ["crm.utils.on_communication_update"],
	},
	"Comment": {
		"after_insert": ["crm.utils.on_comment_insert"],
		"on_update": ["crm.api.comment.on_update"],
	},
	"WhatsApp Message": {
		"validate": ["crm.api.whatsapp.validate"],
		"on_update": ["crm.api.whatsapp.on_update"],
	},
	"CRM Deal": {
		# Guards every status-writing path: status field, Kanban, Take Action and the API.
		# guard_status_change is the role rule (TXB-105); guard_transition is the state
		# machine (TXB-110). Role first, so a coach on Delivering Coaching hears the more
		# specific message. Note the controller's own CRMDeal.validate() runs ahead of both
		# (frappe Document.hook.compose), so a bare write to a Lost-type status is refused
		# by validate_lost_reason before either guard is reached.
		"validate": [
			"crm.txb.permissions.guard_status_change",
			"crm.txb.permissions.guard_transition",
			# The scheduling invariant (TXB-149): "Workshop set" needs a scheduled date and
			# time. Runs after the transition guard so a disallowed move is refused first,
			# and before the field guards, which are about who may edit rather than what
			# state the deal may rest in.
			"crm.txb.doc_events.deal.require_workshop_schedule",
			# Owner last: a user changing status and owner together hears about the
			# status rule first, which is the more common mistake.
			"crm.txb.ownership.guard_owner_change",
			# Delivery Coach and any future Admin-only field.
			"crm.txb.permissions.guard_admin_only_fields",
		],
		"before_insert": ["crm.txb.ownership.claim_owner_on_insert"],
		# A new Delivering Coaching deal drops a linked task into the Admin's list (TXB-208).
		"after_insert": ["crm.txb.doc_events.deal.create_coaching_admin_task"],
		"before_validate": [
			"crm.txb.doc_events.deal.generate_registration_token",
			"crm.txb.doc_events.deal.sync_contact_name",
			"crm.txb.doc_events.deal.sync_delivery_coach_name",
		],
		"on_update": [
			"crm.fcrm.doctype.erpnext_crm_settings.erpnext_crm_settings.create_customer_in_erpnext"
		],
	},
	"Sales Order": {
		"before_validate": [
			"crm.fcrm.doctype.erpnext_crm_settings.erpnext_crm_settings.create_customer_on_sales_order"
		],
	},
	"Item": {
		"after_insert": ["crm.integrations.erpnext.item.after_insert"],
		"on_update": ["crm.integrations.erpnext.item.on_update"],
		"before_rename": ["crm.integrations.erpnext.item.before_rename"],
		"after_rename": ["crm.integrations.erpnext.item.after_rename"],
		"on_trash": ["crm.integrations.erpnext.item.on_trash"],
	},
	"User Permission": {
		"before_validate": ["crm.integrations.erpnext.user_permission.before_validate"],
		"after_insert": ["crm.integrations.erpnext.user_permission.after_insert"],
		"on_update": ["crm.integrations.erpnext.user_permission.on_update"],
		"on_trash": ["crm.integrations.erpnext.user_permission.on_trash"],
	},
	"DocShare": {
		"before_validate": ["crm.integrations.erpnext.doc_share.before_validate"],
		"after_insert": ["crm.integrations.erpnext.doc_share.after_insert"],
		"on_update": ["crm.integrations.erpnext.doc_share.on_update"],
		"on_trash": ["crm.integrations.erpnext.doc_share.on_trash"],
	},
	"User": {
		"before_validate": ["crm.api.live_demo.validate_user"],
		"validate_reset_password": ["crm.api.live_demo.validate_reset_password"],
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"all": ["crm.api.event.trigger_offset_event_notifications"],
	"hourly": ["crm.api.event.trigger_hourly_event_notifications"],
	"daily": [
		"crm.api.event.trigger_daily_event_notifications",
		"crm.fcrm.doctype.crm_invitation.crm_invitation.expire_invitations",
		"crm.fcrm.doctype.crm_view_settings.crm_view_settings.clear_old_versions",
		"crm.telemetry.capture_feature_state",
	],
	"weekly": ["crm.api.event.trigger_weekly_event_notifications"],
	"daily_long": ["crm.lead_syncing.background_sync.sync_leads_from_sources_daily"],
	"hourly_long": ["crm.lead_syncing.background_sync.sync_leads_from_sources_hourly"],
	"monthly_long": ["crm.lead_syncing.background_sync.sync_leads_from_sources_monthly"],
	"cron": {
		"*/5 * * * *": ["crm.lead_syncing.background_sync.sync_leads_from_sources_5_minutes"],
		"*/10 * * * *": ["crm.lead_syncing.background_sync.sync_leads_from_sources_10_minutes"],
		"*/15 * * * *": ["crm.lead_syncing.background_sync.sync_leads_from_sources_15_minutes"],
		# Cadence preserved from the Server Scripts these replaced.
		"0 9 * * *": ["crm.txb.tasks.reminders.stale_session_run_alert"],
		"0 9 * * 1": ["crm.txb.tasks.reminders.weekly_vcs_reminder"],
	},
}

# Testing
# -------

before_tests = "crm.tests.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# "frappe.desk.doctype.event.event.get_events": "crm.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# "Task": "crm.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

ignore_links_on_delete = ["Failed Lead Sync Log"]

# Request Events
# ----------------
# before_request = ["crm.utils.before_request"]
# after_request = ["crm.utils.after_request"]

# Job Events
# ----------
# before_job = ["crm.utils.before_job"]
# after_job = ["crm.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# {
# "doctype": "{doctype_1}",
# "filter_by": "{filter_by}",
# "redact_fields": ["{field_1}", "{field_2}"],
# "partial": 1,
# },
# {
# "doctype": "{doctype_2}",
# "filter_by": "{filter_by}",
# "partial": 1,
# },
# {
# "doctype": "{doctype_3}",
# "strict": False,
# },
# {
# "doctype": "{doctype_4}"
# }
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# "crm.auth.validate"
# ]

after_migrate = [
	"crm.fcrm.doctype.fcrm_settings.fcrm_settings.after_migrate",
	"crm.api.whatsapp.add_roles",
	"crm.domain_enrichment.install.seed_default_rules_and_mappings",
	"crm.install.add_default_scripts",
	"crm.install.add_web_form_custom_fields",
	# Re-asserted on every migrate, not once via a patch: a Patch Log entry cannot undo a
	# script someone re-enabled afterwards. See crm/txb/retired_scripts.py.
	"crm.txb.retired_scripts.retire_scripts",
	# Same reasoning for a drifted CRM Call Log `status` options override that would otherwise
	# reject the canonical "No Answer" dial result. See crm/txb/call_log_status.py.
	"crm.txb.call_log_status.reconcile_call_log_status_options",
]

standard_dropdown_items = [
	{
		"name1": "app_selector",
		"label": "Apps",
		"type": "Route",
		"route": "#",
		"is_standard": 1,
	},
	{
		"name1": "settings",
		"label": "Settings",
		"type": "Route",
		"icon": "settings",
		"route": "#",
		"is_standard": 1,
	},
	{
		"name1": "login_to_fc",
		"label": "Login to Frappe Cloud",
		"type": "Route",
		"route": "#",
		"is_standard": 1,
	},
	{
		"name1": "about",
		"label": "About",
		"type": "Route",
		"icon": "info",
		"route": "#",
		"is_standard": 1,
	},
	{
		"name1": "separator",
		"label": "",
		"type": "Separator",
		"is_standard": 1,
	},
	{
		"name1": "logout",
		"label": "Log out",
		"type": "Route",
		"icon": "log-out",
		"route": "#",
		"is_standard": 1,
	},
]

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, get_datetime, get_first_day_of_week, nowdate

from crm.api.dashboard import get_today_events, get_today_tasks, get_week_referrals
from crm.fcrm.doctype.crm_lead.test_crm_lead import create_lead
from crm.patches.v1_0 import add_lead_referred_by_reference_fields
from crm.txb.constants import FIELD_REFERRED_BY, FIELD_REFERRED_BY_TYPE


def _user(email, role):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		).insert(ignore_permissions=True)
	frappe.get_doc("User", email).add_roles(role)
	return email


class TestDashboardWidgets(FrappeTestCase):
	"""Today's tasks, Today's events and Referrals this week on the Dashboard."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		add_lead_referred_by_reference_fields.execute()
		# A site Custom Field in production, not shipped by the app. Never touch a real one.
		if not frappe.db.exists("Custom Field", "Contact-custom_contact_owner"):
			create_custom_fields(
				{"Contact": [{"fieldname": "custom_contact_owner", "fieldtype": "Link", "options": "User"}]}
			)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		self.today = nowdate()
		self.rep = _user("widgets.rep@example.com", "Sales User")
		self.other = _user("widgets.other@example.com", "Sales User")
		self.manager = _user("widgets.manager@example.com", "Sales Manager")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _task(self, title, assigned_to, due=None, status="Todo"):
		task = frappe.get_doc(
			{"doctype": "CRM Task", "title": title, "status": status, "assigned_to": assigned_to, "due_date": due}
		).insert(ignore_permissions=True)
		return task.name

	def _backdate(self, doctype, name, field, when):
		frappe.db.set_value(doctype, name, field, when, update_modified=False)

	def _groups(self, user=None):
		return {t["title"]: t["group"] for t in get_today_tasks(user)}

	def test_tasks_land_in_their_groups(self):
		self._task("due today", self.rep, f"{self.today} 15:00:00")
		self._task("assigned today, due next week", self.rep, f"{add_days(self.today, 7)} 10:00:00")
		self._task("overdue", self.rep, f"{add_days(self.today, -2)} 10:00:00")
		done = self._task("done today", self.rep, f"{add_days(self.today, -1)} 10:00:00", status="Done")
		self._task("canceled", self.rep, f"{self.today} 11:00:00", status="Canceled")
		old = self._task("assigned last week", self.rep, f"{add_days(self.today, 5)} 10:00:00")
		frappe.db.sql(
			"update tabToDo set creation=%s where reference_type='CRM Task' and reference_name=%s",
			(add_days(self.today, -7), old),
		)
		old_done = self._task("done last week", self.rep, status="Done")
		self._backdate("CRM Task", old_done, "modified", add_days(self.today, -7))

		frappe.set_user(self.rep)
		self.assertEqual(
			self._groups(),
			{
				"overdue": "overdue",
				"due today": "today",
				"assigned today, due next week": "today",
				"done today": "done",
			},
		)
		self.assertTrue(done)

	def test_sales_user_sees_only_own_tasks(self):
		self._task("mine", self.rep, f"{self.today} 10:00:00")
		self._task("theirs", self.other, f"{self.today} 10:00:00")

		frappe.set_user(self.rep)
		self.assertEqual(set(self._groups()), {"mine"})
		self.assertEqual(set(self._groups(self.other)), {"mine"})

		frappe.set_user(self.manager)
		self.assertTrue({"mine", "theirs"} <= set(self._groups()))
		self.assertEqual(set(self._groups(self.other)), {"theirs"})

	def _event(self, subject, owner, starts_on, ends_on=None, status="Open", participants=None):
		frappe.set_user(owner)
		event = frappe.get_doc(
			{
				"doctype": "Event",
				"subject": subject,
				"event_type": "Private",
				"starts_on": starts_on,
				"ends_on": ends_on,
				"status": status,
				"event_participants": [{"reference_doctype": "User", "reference_docname": p, "email": p} for p in participants or []],
			}
		).insert(ignore_permissions=True)
		frappe.set_user("Administrator")
		return event.name

	def _subjects(self, user=None):
		return {e["subject"] for e in get_today_events(user)}

	def test_events_today_and_who_sees_them(self):
		self._event("rep meeting", self.rep, f"{self.today} 09:00:00", f"{self.today} 09:30:00")
		self._event("other private", self.other, f"{self.today} 13:00:00", f"{self.today} 14:00:00")
		self._event("other with rep", self.other, f"{self.today} 15:00:00", participants=[self.rep])
		self._event("multi-day", self.other, f"{add_days(self.today, -1)} 10:00:00", f"{add_days(self.today, 1)} 10:00:00")
		self._event("yesterday", self.rep, f"{add_days(self.today, -1)} 10:00:00", f"{add_days(self.today, -1)} 11:00:00")
		self._event("cancelled", self.rep, f"{self.today} 16:00:00", status="Cancelled")

		frappe.set_user(self.rep)
		self.assertEqual(self._subjects(), {"rep meeting", "other with rep"})

		frappe.set_user(self.manager)
		self.assertTrue({"rep meeting", "other private", "other with rep", "multi-day"} <= self._subjects())
		self.assertFalse({"yesterday", "cancelled"} & self._subjects())
		self.assertEqual(self._subjects(self.rep), {"rep meeting", "other with rep"})

	def _referred(self, created, referrer, referrer_type="CRM Lead", owner=None):
		lead = create_lead(first_name="Referred", lead_owner=owner or self.other)
		frappe.db.set_value(
			"CRM Lead",
			lead.name,
			{FIELD_REFERRED_BY_TYPE: referrer_type, FIELD_REFERRED_BY: referrer},
			update_modified=False,
		)
		self._backdate("CRM Lead", lead.name, "creation", get_datetime(f"{created} 10:00:00"))
		return lead.name

	def test_referrals_earn_the_referrers_owner_a_point(self):
		week_start = get_first_day_of_week(self.today)
		last_week = add_days(week_start, -3)
		frappe.db.delete("CRM Lead", {FIELD_REFERRED_BY: ("is", "set")})
		rep_lead = create_lead(first_name="Rep's", lead_owner=self.rep).name
		other_lead = create_lead(first_name="Other's", lead_owner=self.other).name
		contact = frappe.get_doc(
			{"doctype": "Contact", "first_name": "Referrer", "custom_contact_owner": self.other}
		).insert(ignore_permissions=True)

		# Other's new lead referred by rep's lead: rep earns it.
		hidden = self._referred(week_start, rep_lead, owner=self.other)
		self._referred(week_start, rep_lead, owner=self.rep)  # self-referral counts
		self._referred(week_start, contact.name, "Contact")
		ownerless = create_lead(first_name="Ownerless").name
		frappe.db.set_value("CRM Lead", ownerless, "lead_owner", None)
		self._referred(week_start, ownerless)  # counted, no point
		self._referred(last_week, other_lead)
		create_lead(first_name="Unreferred", lead_owner=self.rep)

		this_week = get_week_referrals()
		self.assertEqual(this_week["total"], 4)
		self.assertEqual(this_week["reps"], [{"user": self.rep, "points": 2}, {"user": self.other, "points": 1}])
		self.assertEqual(get_week_referrals(week_of=str(last_week))["reps"], [{"user": self.other, "points": 1}])

		frappe.set_user(self.manager)
		mine = get_week_referrals(user=self.rep)
		self.assertEqual(mine["total"], 2)
		self.assertEqual({lead["point_to"] for lead in mine["leads"]}, {self.rep})

		frappe.set_user(self.rep)
		seen = get_week_referrals(user=self.other)  # a sales user can't filter
		self.assertEqual(seen["reps"], this_week["reps"])
		self.assertNotIn(hidden, {lead["name"] for lead in seen["leads"]})

import frappe
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

	def _referred(self, owner, created, referrer=None):
		referrer = referrer or create_lead(first_name="Referrer").name
		lead = create_lead(first_name="Referred", lead_owner=owner)
		frappe.db.set_value(
			"CRM Lead",
			lead.name,
			{FIELD_REFERRED_BY_TYPE: "CRM Lead", FIELD_REFERRED_BY: referrer},
			update_modified=False,
		)
		self._backdate("CRM Lead", lead.name, "creation", get_datetime(f"{created} 10:00:00"))
		return lead.name

	def test_referrals_count_for_the_new_leads_owner(self):
		week_start = get_first_day_of_week(self.today)
		last_week = add_days(week_start, -3)
		frappe.db.delete("CRM Lead", {FIELD_REFERRED_BY: ("is", "set")})
		self._referred(self.rep, week_start)
		self._referred(self.rep, week_start)
		other_lead = self._referred(self.other, week_start)
		self._referred(self.other, last_week)
		create_lead(first_name="Unreferred", lead_owner=self.rep)

		this_week = get_week_referrals()
		self.assertEqual(this_week["total"], 3)
		self.assertEqual(this_week["reps"], [{"user": self.rep, "points": 2}, {"user": self.other, "points": 1}])
		self.assertEqual(get_week_referrals(week_of=str(last_week))["reps"], [{"user": self.other, "points": 1}])

		frappe.set_user(self.rep)
		seen = get_week_referrals()
		self.assertEqual(seen["reps"], this_week["reps"])
		self.assertNotIn(other_lead, {lead["name"] for lead in seen["leads"]})

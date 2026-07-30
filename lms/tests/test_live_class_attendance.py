"""The hourly attendance job must not be blockable by one bad class.

Erasure disables the Zoom account of a subject who asked to be forgotten,
and `authenticate` throws for a disabled account. Without isolation, that
exception escapes the loop and the class stays eligible — so one erased
host stops attendance collection for every Zoom class, on every run.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms.lms.doctype.lms_live_class import lms_live_class


class TestAttendanceCollection(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.hash = frappe.generate_hash(length=6)
		self.created = []

		# Inserting a live class creates a calendar event, which throws
		# unless a Google Calendar is configured. Irrelevant to attendance
		# collection, and it would stop the fixtures existing at all.
		original_event = lms_live_class.LMSLiveClass.create_calendar_event
		lms_live_class.LMSLiveClass.create_calendar_event = lambda self: None
		self.addCleanup(
			setattr, lms_live_class.LMSLiveClass, "create_calendar_event", original_event
		)

		self.batch = self._make(
			{
				"doctype": "LMS Batch",
				"title": f"Attendance batch {self.hash}",
				"start_date": "2026-01-01",
				"end_date": "2026-06-01",
				"start_time": "09:00:00",
				"end_time": "10:00:00",
				"description": "x",
				"batch_details": "x",
				"timezone": "Europe/Istanbul",
				"published": 0,
				"instructors": [{"instructor": "Administrator"}],
			}
		)
		self.disabled = self._make(
			{
				"doctype": "LMS Zoom Settings",
				"member": "Administrator",
				"account_name": f"erased-{self.hash}",
				"account_id": "x",
				"client_id": "x",
				"client_secret": "x",
				"enabled": 0,
			}
		)
		self.enabled = self._make(
			{
				"doctype": "LMS Zoom Settings",
				"member": "Administrator",
				"account_name": f"live-{self.hash}",
				"account_id": "x",
				"client_id": "x",
				"client_secret": "x",
				"enabled": 1,
			}
		)
		frappe.db.commit()

	def tearDown(self):
		for doctype, name in reversed(self.created):
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDown()

	def _make(self, payload):
		doc = frappe.get_doc(payload).insert(ignore_permissions=True)
		self.created.append((doc.doctype, doc.name))
		return doc

	def _live_class(self, zoom_account, uuid):
		doc = self._make(
			{
				"doctype": "LMS Live Class",
				"batch_name": self.batch.name,
				"title": f"class-{uuid}",
				"date": "2026-02-01",
				"time": "09:00:00",
				"duration": 60,
				"conferencing_provider": "Zoom",
				"zoom_account": zoom_account,
				"host": "Administrator",
				"timezone": "Europe/Istanbul",
				"uuid": uuid,
			}
		)
		return doc

	def test_a_class_on_a_disabled_account_is_skipped(self):
		"""Its host was erased; it is not collectable and never will be."""
		blocked = self._live_class(self.disabled.name, f"uuid-blocked-{self.hash}")
		frappe.db.commit()

		seen = []
		original = lms_live_class.get_attendance
		lms_live_class.get_attendance = lambda lc: seen.append(lc.name) or []
		try:
			lms_live_class.update_attendance()
		finally:
			lms_live_class.get_attendance = original

		self.assertNotIn(blocked.name, seen, "attendance was attempted on a disabled account")

	def test_one_failing_class_does_not_block_the_others(self):
		"""The failure this makes routine must stay contained."""
		doomed = self._live_class(self.enabled.name, f"uuid-doomed-{self.hash}")
		healthy = self._live_class(self.enabled.name, f"uuid-healthy-{self.hash}")
		frappe.db.commit()

		attempted = []

		def flaky(live_class):
			attempted.append(live_class.name)
			if live_class.name == doomed.name:
				raise RuntimeError("zoom is unreachable for this meeting")
			return []

		original_get = lms_live_class.get_attendance
		original_create = lms_live_class.create_attendance
		original_count = lms_live_class.update_attendees_count
		lms_live_class.get_attendance = flaky
		lms_live_class.create_attendance = lambda *a, **k: None
		lms_live_class.update_attendees_count = lambda *a, **k: None
		try:
			lms_live_class.update_attendance()  # must not raise
		finally:
			lms_live_class.get_attendance = original_get
			lms_live_class.create_attendance = original_create
			lms_live_class.update_attendees_count = original_count

		self.assertIn(doomed.name, attempted)
		self.assertIn(
			healthy.name, attempted, "a failing class stopped the run before the next one"
		)

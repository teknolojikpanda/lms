"""The exam clock is decided by the server, not the browser (§4.7.3).

`started_at` is a naive timestamp in the site's timezone. A client that
parsed it with `new Date()` read it as local time, so a student ahead of
the server computed a deadline already in the past and was submitted the
moment the page loaded. Sending the remainder removes the arithmetic from
the one place that cannot know the timezone.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from lms.lms.doctype.lms_placement_attempt.lms_placement_attempt import (
	SUBMIT_GRACE_SECONDS,
	_marshal_attempt,
)


class TestPlacementTimer(IntegrationTestCase):
	DURATION = 30

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.hash = frappe.generate_hash(length=6)
		self.created = []

		self.questions = [
			self._make(
				{
					"doctype": "LMS Question",
					"question": f"timer q{i} {self.hash}?",
					"type": "Choices",
					"option_1": "a",
					"is_correct_1": 1,
					"option_2": "b",
					"language_level": "A1",
					"language_skill": "Reading",
					"topic": f"timer-{self.hash}",
				}
			).name
			for i in range(2)
		]
		self.blueprint = self._make(
			{
				"doctype": "LMS Placement Blueprint",
				"title": f"Timer bp {self.hash}",
				"duration": self.DURATION,
				"default_level": "A1",
				"enabled": 1,
				"segments": [
					{"question_count": 1, "skill": "Reading", "level": "A1", "topic": f"timer-{self.hash}"}
				],
				"level_mappings": [{"min_score": 0, "level": "A1"}],
			}
		)

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

	def _attempt(self, started_at, status="In Progress"):
		doc = self._make(
			{
				"doctype": "LMS Placement Attempt",
				"member": "Administrator",
				"blueprint": self.blueprint.name,
				"status": status,
				"questions": [{"question": self.questions[0], "marks": 1}],
			}
		)
		frappe.db.set_value(
			"LMS Placement Attempt", doc.name, "started_at", started_at, update_modified=False
		)
		frappe.db.commit()
		return frappe.get_doc("LMS Placement Attempt", doc.name)

	def test_the_payload_carries_the_remaining_time(self):
		"""So the client never has to work it out from a naive timestamp."""
		attempt = self._attempt(add_to_date(now_datetime(), minutes=-10))
		payload = _marshal_attempt(attempt)

		self.assertIn("remaining_seconds", payload)
		expected = (self.DURATION - 10) * 60 + SUBMIT_GRACE_SECONDS
		self.assertAlmostEqual(payload["remaining_seconds"], expected, delta=5)

	def test_an_expired_attempt_reports_no_time_left(self):
		attempt = self._attempt(add_to_date(now_datetime(), minutes=-(self.DURATION + 5)))
		self.assertEqual(_marshal_attempt(attempt)["remaining_seconds"], 0)

	def test_the_remainder_never_goes_negative(self):
		"""A negative would count *up* on a client rendering it."""
		attempt = self._attempt(add_to_date(now_datetime(), days=-3))
		self.assertGreaterEqual(_marshal_attempt(attempt)["remaining_seconds"], 0)

	def test_a_finished_attempt_has_no_clock(self):
		attempt = self._attempt(add_to_date(now_datetime(), minutes=-5), status="Completed")
		self.assertIsNone(_marshal_attempt(attempt)["remaining_seconds"])

	def test_the_client_deadline_matches_the_server_expiry(self):
		"""The countdown and `is_expired` must agree on when time is up.

		They are separate calculations, so a mismatched grace period would
		auto-submit a student the server still considers in time — or
		leave a clock running on an attempt already refused.
		"""
		attempt = self._attempt(add_to_date(now_datetime(), minutes=-(self.DURATION + 5)))
		self.assertTrue(attempt.is_expired())
		self.assertEqual(_marshal_attempt(attempt)["remaining_seconds"], 0)

		fresh = self._attempt(add_to_date(now_datetime(), minutes=-1))
		self.assertFalse(fresh.is_expired())
		self.assertGreater(_marshal_attempt(fresh)["remaining_seconds"], 0)

"""Direct document reads must be gated, not just list queries.

`permission_query_conditions` constrains list and report queries. A read
of `/api/resource/<doctype>/<name>` is answered by role and document
permissions alone, and these doctypes grant `LMS Student` read — so a
list filter without a matching `has_permission` hook leaves every record
reachable by anyone who knows its name.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms.lms.test_helpers import BaseTestUtils


class TestLanguagePlatformIDOR(BaseTestUtils, IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		# Inserting a speaking submission enqueues a real pipeline job via
		# after_insert; a worker then claims the row and teardown's delete
		# hits a lock-wait timeout. Nothing here needs the job to run.
		patcher = patch("frappe.enqueue")
		patcher.start()
		self.addCleanup(patcher.stop)

		h = frappe.generate_hash(length=6)
		self.victim = self._create_user(f"vic-{h}@example.com", "Vic", "Tim", ["LMS Student"])
		self.attacker = self._create_user(f"atk-{h}@example.com", "At", "Tacker", ["LMS Student"])
		self.created = []
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		for doctype, name in self.created:
			frappe.delete_doc(
				doctype, name, force=True, ignore_permissions=True, ignore_missing=True
			)
		frappe.db.commit()
		super().tearDown()

	def _track(self, doc):
		self.created.append((doc.doctype, doc.name))
		return doc

	# --- the two hook lists must stay in step -----------------------------

	def test_every_filtered_doctype_also_has_a_document_check(self):
		"""The regression that let this happen: a doctype gains a list
		filter and nobody adds the per-document gate.

		Reads this app's own hooks rather than the merged registry —
		`frappe.get_hooks` folds in every installed app, and frappe's own
		Dashboard and Notification Log filter lists without a document
		hook by design. Their choices are not this app's invariant.
		"""
		import lms.hooks

		query_conditions = set(lms.hooks.permission_query_conditions)
		document_checks = set(lms.hooks.has_permission)

		missing = sorted(query_conditions - document_checks)
		self.assertEqual(
			missing,
			[],
			f"these doctypes filter lists but not direct reads: {missing}",
		)

	# --- per-doctype ------------------------------------------------------

	def _assert_denied(self, doctype, name, label):
		frappe.set_user(self.attacker.email)
		try:
			self.assertFalse(
				frappe.has_permission(doctype, doc=name, ptype="read"),
				f"another student could read {label} by name",
			)
			with self.assertRaises(frappe.PermissionError, msg=f"{label} was readable"):
				frappe.get_doc(doctype, name).check_permission("read")
		finally:
			frappe.set_user("Administrator")

	def test_a_student_cannot_read_another_students_accessibility_preference(self):
		"""Named after the member, so the identifier is already known."""
		doc = self._track(
			frappe.get_doc(
				{
					"doctype": "LMS Accessibility Preference",
					"member": self.victim.email,
					"font_step": 5,
					"contrast_mode": "high",
				}
			).insert(ignore_permissions=True)
		)
		frappe.db.commit()
		self._assert_denied("LMS Accessibility Preference", doc.name, "an accessibility preference")

	def test_a_student_cannot_read_another_students_placement_attempt(self):
		blueprint = self._track(
			frappe.get_doc(
				{
					"doctype": "LMS Placement Blueprint",
					"title": f"IDOR bp {frappe.generate_hash(length=5)}",
					"duration": 30,
					"default_level": "A1",
					"enabled": 1,
					"segments": [{"question_count": 1, "skill": "Reading", "level": "A1"}],
					"level_mappings": [{"min_score": 0, "level": "A1"}],
				}
			).insert(ignore_permissions=True)
		)
		attempt = self._track(
			frappe.get_doc(
				{
					"doctype": "LMS Placement Attempt",
					"member": self.victim.email,
					"blueprint": blueprint.name,
					"status": "In Progress",
				}
			).insert(ignore_permissions=True)
		)
		frappe.db.commit()
		self._assert_denied("LMS Placement Attempt", attempt.name, "a placement attempt")

	def test_a_student_cannot_read_another_students_speaking_submission(self):
		prompt = self._track(
			frappe.get_doc(
				{
					"doctype": "LMS Speaking Prompt",
					"title": f"IDOR prompt {frappe.generate_hash(length=5)}",
					"scenario": "x",
					"enabled": 1,
				}
			).insert(ignore_permissions=True)
		)
		submission = self._track(
			frappe.get_doc(
				{
					"doctype": "LMS Speaking Submission",
					"member": self.victim.email,
					"prompt": prompt.name,
					"audio_file": "/private/files/idor.webm",
					"duration_seconds": 20,
					"transcript": "a private recording transcript",
				}
			).insert(ignore_permissions=True)
		)
		frappe.db.commit()
		self._assert_denied("LMS Speaking Submission", submission.name, "a speaking submission")

	def _lesson(self):
		"""A course the attacker is enrolled in, so only scope is in play."""
		h = frappe.generate_hash(length=5)
		instructor = self._create_user(
			f"idor-instr-{h}@example.com", "In", "Structor", ["Course Creator", "Moderator"]
		)
		self.created.append(("User", instructor.name))
		course = self._track(
			self._create_course(title=f"IDOR Course {h}", instructor=instructor.email)
		)
		chapter = self._track(self._create_chapter(f"IDOR Ch {h}", course.name))
		lesson = self._track(self._create_lesson(f"IDOR L {h}", chapter.name, course.name))
		self._track(
			frappe.get_doc(
				{
					"doctype": "LMS Enrollment",
					"member": self.attacker.email,
					"course": course.name,
				}
			).insert(ignore_permissions=True)
		)
		return course, lesson

	def test_a_student_cannot_read_an_unpublished_overlay(self):
		"""Drafts are staff-only, whatever the lesson access."""
		course, lesson = self._lesson()
		overlay = self._track(
			frappe.get_doc(
				{
					"doctype": "LMS Video Overlay",
					"lesson": lesson.name,
					"course": course.name,
					"timestamp_ms": 1000,
					"type": "Note",
					"scope": "Course",
					"note_text": "an unfinished draft",
					"published": 0,
				}
			).insert(ignore_permissions=True)
		)
		frappe.db.commit()
		self._assert_denied("LMS Video Overlay", overlay.name, "an unpublished overlay")

	def test_a_student_cannot_read_another_cohorts_batch_overlay(self):
		"""Course access is not batch access.

		`get_lesson_overlays` filters Batch-scoped overlays by enrolment,
		so the per-document gate has to apply the same rule — otherwise a
		student in the course reads another cohort's overlay by name.
		"""
		course, lesson = self._lesson()
		other_batch = self._track(
			frappe.get_doc(
				{
					"doctype": "LMS Batch",
					"title": f"Other cohort {frappe.generate_hash(length=5)}",
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
			).insert(ignore_permissions=True)
		)
		overlay = self._track(
			frappe.get_doc(
				{
					"doctype": "LMS Video Overlay",
					"lesson": lesson.name,
					"course": course.name,
					"timestamp_ms": 2000,
					"type": "Note",
					"scope": "Batch",
					"batch": other_batch.name,
					"note_text": "for the other cohort only",
					"published": 1,
				}
			).insert(ignore_permissions=True)
		)
		frappe.db.commit()
		self._assert_denied("LMS Video Overlay", overlay.name, "another cohort's batch overlay")

	def test_a_student_cannot_read_another_students_overlay_response(self):
		course, lesson = self._lesson()
		overlay = self._track(
			frappe.get_doc(
				{
					"doctype": "LMS Video Overlay",
					"lesson": lesson.name,
					"course": course.name,
					"timestamp_ms": 3000,
					"type": "Note",
					"scope": "Course",
					"note_text": "published",
					"published": 1,
				}
			).insert(ignore_permissions=True)
		)
		response = self._track(
			frappe.get_doc(
				{
					"doctype": "LMS Overlay Response",
					"overlay": overlay.name,
					"lesson": lesson.name,
					"member": self.victim.email,
					"answer": "the victim's answer",
				}
			).insert(ignore_permissions=True)
		)
		frappe.db.commit()
		self._assert_denied("LMS Overlay Response", response.name, "an overlay response")

	def test_a_system_manager_keeps_direct_access(self):
		"""The gate must not take away what the permission table grants."""
		h = frappe.generate_hash(length=5)
		manager = self._create_user(f"sm-{h}@example.com", "Sys", "Manager", ["System Manager"])
		doc = self._track(
			frappe.get_doc(
				{
					"doctype": "LMS Accessibility Preference",
					"member": self.victim.email,
					"font_step": 3,
					"contrast_mode": "normal",
				}
			).insert(ignore_permissions=True)
		)
		frappe.db.commit()

		frappe.set_user(manager.email)
		try:
			self.assertTrue(
				frappe.has_permission("LMS Accessibility Preference", doc=doc.name, ptype="read"),
				"a System Manager lost access the doctype grants it",
			)
		finally:
			frappe.set_user("Administrator")
			frappe.delete_doc("User", manager.name, force=True, ignore_permissions=True)

	def test_the_owner_can_still_read_their_own_record(self):
		"""The gate must not lock people out of their own data."""
		doc = self._track(
			frappe.get_doc(
				{
					"doctype": "LMS Accessibility Preference",
					"member": self.victim.email,
					"font_step": 4,
					"contrast_mode": "normal",
				}
			).insert(ignore_permissions=True)
		)
		frappe.db.commit()

		frappe.set_user(self.victim.email)
		try:
			self.assertTrue(
				frappe.has_permission("LMS Accessibility Preference", doc=doc.name, ptype="read")
			)
		finally:
			frappe.set_user("Administrator")

	def test_moderators_cannot_enumerate_watermark_traces_directly(self):
		"""Identification must go through the audited trace endpoint.

		Raw read/report/export would let a moderator map codes to viewers,
		with IP and user agent, without the audit comment and security log
		that `trace_watermark` writes on every identification.
		"""
		import json
		from pathlib import Path

		path = Path(
			frappe.get_app_path(
				"lms", "lms", "doctype", "lms_watermark_session", "lms_watermark_session.json"
			)
		)
		roles = {p["role"] for p in json.loads(path.read_text(encoding="utf-8"))["permissions"]}
		self.assertNotIn(
			"Moderator",
			roles,
			"Moderator holds raw doctype access, bypassing the audited trace path",
		)

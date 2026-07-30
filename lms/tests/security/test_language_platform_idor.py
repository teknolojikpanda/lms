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

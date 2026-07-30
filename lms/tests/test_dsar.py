"""KVKK export and erasure, checked against the database rather than the
summary the operation returns.

The defect these cover was precisely a summary that said "retained,
pseudonymous" about rows nothing had touched: the label was the only
thing that changed.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms.lms.language_platform.dsar import anonymize_user, collect_user_data
from lms.lms.language_platform.privacy_rules import (
	PERSONAL_DATA_SOURCES,
	anonymized_email,
)


class TestDataSubjectRights(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		patcher = patch("frappe.enqueue")
		patcher.start()
		self.addCleanup(patcher.stop)

		self.hash = frappe.generate_hash(length=6)
		self.subject = f"dsar-{self.hash}@example.com"
		self.pseudonym = anonymized_email(self.subject)
		frappe.get_doc(
			{
				"doctype": "User",
				"email": self.subject,
				"first_name": "Data",
				"last_name": "Subject",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)

		self.course = frappe.get_doc(
			{
				"doctype": "LMS Course",
				"title": f"DSAR course {self.hash}",
				"short_introduction": "x",
				"description": "x",
				"published": 1,
				"instructors": [{"instructor": "Administrator"}],
			}
		).insert(ignore_permissions=True)
		self.enrolment = frappe.get_doc(
			{"doctype": "LMS Enrollment", "member": self.subject, "course": self.course.name}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		for doctype, filters in (
			("LMS Enrollment", {"course": self.course.name}),
			("LMS Placement Attempt", {"blueprint": ["like", f"%{self.hash}%"]}),
		):
			for name in frappe.get_all(doctype, filters=filters, pluck="name"):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		for doctype, name in (
			("LMS Course", self.course.name),
			("User", self.pseudonym),
			("User", self.subject),
		):
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDown()

	# --- erasure ---------------------------------------------------------

	def test_retained_records_no_longer_carry_the_original_address(self):
		"""The defect: rows were labelled pseudonymous, not made so."""
		anonymize_user(self.subject)
		frappe.db.commit()

		member = frappe.db.get_value("LMS Enrollment", self.enrolment.name, "member")
		self.assertNotEqual(member, self.subject, "the retained row still names the subject")
		self.assertEqual(member, self.pseudonym)

	def test_the_owner_column_is_rewritten_too(self):
		"""Frappe's after_rename walks every table; verify it did here.

		This is the residual the module previously documented as needing a
		separate migration. It does not.
		"""
		frappe.db.set_value(
			"LMS Enrollment", self.enrolment.name, "owner", self.subject, update_modified=False
		)
		frappe.db.commit()

		anonymize_user(self.subject)
		frappe.db.commit()

		owner = frappe.db.get_value("LMS Enrollment", self.enrolment.name, "owner")
		self.assertNotEqual(owner, self.subject, "owner still holds the original address")

	def test_the_original_login_no_longer_exists(self):
		anonymize_user(self.subject)
		frappe.db.commit()

		self.assertFalse(frappe.db.exists("User", self.subject))
		self.assertTrue(frappe.db.exists("User", self.pseudonym))
		self.assertFalse(frappe.db.get_value("User", self.pseudonym, "enabled"))

	def test_the_summary_reports_the_pseudonym(self):
		summary = anonymize_user(self.subject)
		frappe.db.commit()
		self.assertEqual(summary["Pseudonym"], self.pseudonym)

	def test_system_accounts_are_still_refused(self):
		for account in ("Administrator", "Guest"):
			with self.assertRaises(frappe.ValidationError):
				anonymize_user(account)

	# --- export ----------------------------------------------------------

	def test_export_includes_child_table_content(self):
		"""A quiz submission's answers live in a child table.

		`fields=["*"]` returned the wrapper and none of the content, while
		reporting a row count that looked complete.
		"""
		quiz = frappe.get_doc(
			{
				"doctype": "LMS Quiz",
				"title": f"DSAR quiz {self.hash}",
			}
		).insert(ignore_permissions=True)
		submission = frappe.get_doc(
			{
				"doctype": "LMS Quiz Submission",
				"member": self.subject,
				"quiz": quiz.name,
				"score": 8,
				"score_out_of": 10,
				"percentage": 80,
				"passing_percentage": 50,
				"result": [
					{
						"question": "What did the student answer?",
						"answer": "a recorded answer",
						"is_correct": 1,
					}
				],
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		self.addCleanup(
			lambda: (
				frappe.delete_doc(
					"LMS Quiz Submission", submission.name, force=True, ignore_permissions=True
				),
				frappe.delete_doc("LMS Quiz", quiz.name, force=True, ignore_permissions=True),
				frappe.db.commit(),
			)
		)

		payload = collect_user_data(self.subject)
		body = frappe.as_json(payload)

		self.assertIn("LMS Quiz Submission", payload["records"])
		self.assertIn(
			"a recorded answer", body, "the export omitted the answers it claims to contain"
		)

	def test_export_covers_every_registered_source(self):
		"""The registry is the source list for export and erasure both."""
		payload = collect_user_data(self.subject)
		self.assertIn("LMS Enrollment", payload["records"])
		self.assertEqual(payload["subject"]["name"], self.subject)
		# The counts are what a reader trusts, so they must match the rows
		# actually carried rather than a separate tally.
		for doctype, rows in payload["records"].items():
			self.assertEqual(payload["counts"][doctype], len(rows))

	def test_denormalised_copies_of_the_name_are_scrubbed(self):
		"""A rename fixes the link and not the copies beside it.

		`fetch_from` duplicates the member's name onto the row at write
		time, so an erasure that only renamed left the subject's name in
		records it reported as pseudonymous.
		"""
		progress = frappe.get_doc(
			{
				"doctype": "LMS Course Progress",
				"member": self.subject,
				"course": self.course.name,
				"status": "Complete",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		self.addCleanup(
			lambda: frappe.db.exists("LMS Course Progress", progress.name)
			and frappe.delete_doc(
				"LMS Course Progress", progress.name, force=True, ignore_permissions=True
			)
		)

		before = frappe.db.get_value("LMS Course Progress", progress.name, "member_name")
		self.assertEqual(before, "Data Subject", "fixture did not denormalise the name")

		anonymize_user(self.subject)
		frappe.db.commit()

		after = frappe.db.get_value("LMS Course Progress", progress.name, "member_name")
		self.assertNotEqual(after, "Data Subject", "the subject's name survived erasure")

	def test_a_second_erasure_of_a_reused_address_gets_its_own_pseudonym(self):
		"""An address can be registered again after being erased.

		The deterministic pseudonym is then already taken, and the rename
		either fails on the unique-email constraint or is skipped —
		leaving the new account under its real address while the summary
		says otherwise.
		"""
		anonymize_user(self.subject)
		frappe.db.commit()
		self.assertTrue(frappe.db.exists("User", self.pseudonym))

		frappe.get_doc(
			{
				"doctype": "User",
				"email": self.subject,
				"first_name": "Data",
				"last_name": "Subject",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

		summary = anonymize_user(self.subject)
		frappe.db.commit()
		second = summary["Pseudonym"]
		self.addCleanup(
			lambda: frappe.db.exists("User", second)
			and frappe.delete_doc("User", second, force=True, ignore_permissions=True)
		)

		self.assertNotEqual(second, self.pseudonym, "the two subjects share one pseudonym")
		self.assertFalse(
			frappe.db.exists("User", self.subject), "the re-registered account kept its address"
		)

	def test_a_username_held_by_someone_else_does_not_block_erasure(self):
		"""`username` is unique too, and the scrub writes to it.

		Checking only the User primary key accepts a candidate whose
		username another account holds, and the erasure then fails partway
		through rather than before it starts.
		"""
		from lms.lms.language_platform.privacy_rules import anonymized_handle

		squatter = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"squatter-{self.hash}@example.com",
				"first_name": "Squatter",
				"username": anonymized_handle(self.subject),
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		self.addCleanup(
			lambda: frappe.db.exists("User", squatter.name)
			and frappe.delete_doc("User", squatter.name, force=True, ignore_permissions=True)
		)

		summary = anonymize_user(self.subject)
		frappe.db.commit()
		new_user = summary["Pseudonym"]
		self.addCleanup(
			lambda: frappe.db.exists("User", new_user)
			and frappe.delete_doc("User", new_user, force=True, ignore_permissions=True)
		)

		self.assertNotEqual(new_user, self.pseudonym, "collided with the taken username")
		self.assertFalse(frappe.db.exists("User", self.subject))

	def test_conferencing_settings_survive_being_referenced(self):
		"""Purging a row a batch links to fails the whole request.

		LMS Batch and LMS Live Class hold Link fields to these settings,
		so they are scrubbed rather than deleted.
		"""
		settings = frappe.get_doc(
			{
				"doctype": "LMS Zoom Settings",
				"member": self.subject,
				"account_name": "the subject's zoom account",
				"account_id": "acct-123",
				"client_id": "client-123",
				"client_secret": "secret-123",
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		self.addCleanup(
			lambda: frappe.db.exists("LMS Zoom Settings", settings.name)
			and frappe.delete_doc(
				"LMS Zoom Settings", settings.name, force=True, ignore_permissions=True
			)
		)

		anonymize_user(self.subject)
		frappe.db.commit()

		# Asserted against the configured scrub map rather than a list
		# repeated here, so adding a field to the rules without clearing it
		# fails this test instead of passing quietly.
		configured = next(
			s["scrub"] for s in PERSONAL_DATA_SOURCES if s["doctype"] == "LMS Zoom Settings"
		)
		row = frappe.db.get_value(
			"LMS Zoom Settings", settings.name, list(configured), as_dict=True
		)
		self.assertIsNotNone(row, "the settings row was deleted despite being referenceable")
		for field, expected in configured.items():
			self.assertEqual(
				row[field], expected, f"{field} was not scrubbed as the rules require"
			)
		self.assertNotIn(
			"secret-123", frappe.as_json(row), "the subject's credentials survived erasure"
		)

	def test_conferencing_settings_hold_nothing_personal_beyond_the_scrub(self):
		"""Every field on these doctypes is scrubbed, fetched, or a link.

		A credential added later without a scrub rule would otherwise
		survive an erasure silently.
		"""
		for doctype in ("LMS Zoom Settings", "LMS Google Meet Settings"):
			source = next(s for s in PERSONAL_DATA_SOURCES if s["doctype"] == doctype)
			scrubbed = set(source.get("scrub") or {})
			accounted = scrubbed | {source["owner_field"]}
			leftovers = []
			for field in frappe.get_meta(doctype).fields:
				if field.fieldname in accounted or field.fetch_from:
					continue
				if field.fieldtype in ("Section Break", "Column Break", "Tab Break"):
					continue
				leftovers.append(field.fieldname)
			self.assertEqual(
				leftovers, [], f"{doctype} has unscrubbed fields: {leftovers}"
			)

	def test_every_lms_doctype_linking_to_a_user_is_classified(self):
		"""Registered, or explicitly excluded — never merely forgotten.

		The registry was hand-written and missed sources twice. This makes
		a new User link fail the suite until someone decides what it is.
		"""
		from lms.lms.language_platform.privacy_rules import NON_SUBJECT_USER_LINKS

		registered = {s["doctype"] for s in PERSONAL_DATA_SOURCES}
		unclassified = []
		for doctype in frappe.get_all("DocType", filters={"module": ["like", "%LMS%"]}, pluck="name"):
			if doctype in registered or doctype in NON_SUBJECT_USER_LINKS:
				continue
			try:
				meta = frappe.get_meta(doctype)
			except Exception:
				continue
			if any(f.fieldtype == "Link" and f.options == "User" for f in meta.fields):
				unclassified.append(doctype)

		self.assertEqual(
			sorted(unclassified),
			[],
			"these link to User but are neither registered as personal data nor "
			f"listed as staff assignments: {sorted(unclassified)}",
		)

	def test_the_registry_names_only_real_doctypes(self):
		"""A typo would silently drop a source from export and erasure."""
		unknown = [
			source["doctype"]
			for source in PERSONAL_DATA_SOURCES
			if not frappe.db.exists("DocType", source["doctype"])
		]
		self.assertEqual(unknown, [], f"registry names doctypes that do not exist: {unknown}")

	def test_the_registry_owner_fields_exist_on_their_doctypes(self):
		"""A wrong owner field matches nothing, so the source is skipped."""
		wrong = []
		for source in PERSONAL_DATA_SOURCES:
			doctype, field = source["doctype"], source["owner_field"]
			if not frappe.db.exists("DocType", doctype):
				continue
			if field == "owner":
				continue
			if not frappe.get_meta(doctype).get_field(field):
				wrong.append(f"{doctype}.{field}")
		self.assertEqual(wrong, [], f"registry names fields that do not exist: {wrong}")

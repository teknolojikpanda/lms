"""KVKK export and erasure, checked against the database rather than the
summary the operation returns.

The defect these cover was precisely a summary that said "retained,
pseudonymous" about rows nothing had touched: the label was the only
thing that changed.
"""

from unittest.mock import patch

import frappe
from frappe.utils.password import get_decrypted_password
from frappe.tests import IntegrationTestCase

from lms.lms.language_platform.dsar import anonymize_user, collect_user_data
from lms.lms.language_platform.privacy_rules import (
	ERASED_MARKER,
	PERSONAL_DATA_SOURCES,
	UNIQUE_ERASED_MARKER,
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
		# Found by owner, not by name: `account_name` is the autoname
		# field, so scrubbing it renames the document.
		renamed = frappe.db.get_value("LMS Zoom Settings", {"member": self.pseudonym}, "name")
		self.assertTrue(renamed, "the settings row was deleted despite being referenceable")
		self.assertNotEqual(renamed, settings.name, "the erased account name survived as the key")
		self.addCleanup(
			lambda: frappe.db.exists("LMS Zoom Settings", renamed)
			and frappe.delete_doc("LMS Zoom Settings", renamed, force=True, ignore_permissions=True)
		)
		row = frappe.db.get_value("LMS Zoom Settings", renamed, list(configured), as_dict=True)
		for field, expected in configured.items():
			if expected is UNIQUE_ERASED_MARKER:
				# Resolved per row, so only its shape can be asserted.
				self.assertTrue(
					str(row[field]).startswith(f"{ERASED_MARKER}-"),
					f"{field} did not get a unique erased marker: {row[field]!r}",
				)
			else:
				self.assertEqual(
					row[field], expected, f"{field} was not scrubbed as the rules require"
				)
		self.assertNotIn(
			"secret-123", frappe.as_json(row), "the subject's credentials survived erasure"
		)

	def test_two_scrubbed_zoom_accounts_do_not_collide(self):
		"""`account_name` is unique, so a constant marker fails the second.

		It is also the autoname field, so scrubbing the column alone would
		leave the erased account name sitting in the primary key.
		"""
		second = f"dsar2-{self.hash}@example.com"
		frappe.get_doc(
			{"doctype": "User", "email": second, "first_name": "Other", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		self.addCleanup(
			lambda: [
				frappe.delete_doc("User", u, force=True, ignore_permissions=True)
				for u in (second, anonymized_email(second))
				if frappe.db.exists("User", u)
			]
		)

		names = []
		for owner, label in ((self.subject, "first"), (second, "second")):
			doc = frappe.get_doc(
				{
					"doctype": "LMS Zoom Settings",
					"member": owner,
					"account_name": f"zoom-{label}-{self.hash}",
					"account_id": "a",
					"client_id": "c",
					"client_secret": "s",
					"enabled": 1,
				}
			).insert(ignore_permissions=True)
			names.append(doc.name)
		frappe.db.commit()

		anonymize_user(self.subject)
		anonymize_user(second)  # must not collide on account_name
		frappe.db.commit()

		# Scoped to these two subjects: other tests scrub Zoom rows too, and
		# a bare `erased-%` count would pick theirs up.
		pseudonyms = [self.pseudonym, anonymized_email(second)]
		surviving = frappe.get_all(
			"LMS Zoom Settings",
			filters={"member": ["in", pseudonyms]},
			pluck="account_name",
		)
		self.addCleanup(
			lambda: [
				frappe.delete_doc("LMS Zoom Settings", n, force=True, ignore_permissions=True)
				for n in frappe.get_all(
					"LMS Zoom Settings", filters={"member": ["in", pseudonyms]}, pluck="name"
				)
			]
		)
		self.assertEqual(len(surviving), 2, "the two scrubbed rows did not both survive")
		self.assertEqual(len(set(surviving)), 2, "both rows took the same marker")
		for original in names:
			self.assertFalse(
				frappe.db.exists("LMS Zoom Settings", original),
				"the erased account name survived as the primary key",
			)

	def test_conferencing_settings_hold_nothing_personal_beyond_the_scrub(self):
		"""Every field on these doctypes is scrubbed, fetched, or a link.

		A credential added later without a scrub rule would otherwise
		survive an erasure silently.
		"""
		registered = {s["doctype"] for s in PERSONAL_DATA_SOURCES}
		for doctype in ("LMS Zoom Settings", "LMS Google Meet Settings"):
			source = next(s for s in PERSONAL_DATA_SOURCES if s["doctype"] == doctype)
			scrubbed = set(source.get("scrub") or {})
			accounted = scrubbed | {source["owner_field"]}
			leftovers = []
			for field in frappe.get_meta(doctype).fields:
				if field.fieldname in accounted or field.fetch_from:
					continue
				# A Link is accounted for when the doctype it points at is
				# itself registered: the personal data lives there and is
				# erased at its own source, which is what lets this row keep
				# a valid reference instead of a NULL or a dangling marker.
				if field.fieldtype == "Link" and field.options in registered:
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


class TestScrubbedRowsStaySavable(IntegrationTestCase):
	"""A scrubbed row must still satisfy its own doctype.

	`db.set_value` bypasses validation during erasure, so a NULL is
	written happily into a mandatory field and only fails later — the
	next time anything loads and saves the row. That turns an erasure
	into a landmine under whatever touches the record next.
	"""

	def test_mandatory_fields_are_never_scrubbed_to_null(self):
		"""Checked against the doctype, not against a hand-kept list.

		`LMS Google Meet Settings` nulled `account_name`, which is
		mandatory *and* the autoname field, while the Zoom entry directly
		below it already documented exactly this hazard.
		"""
		offenders = []
		for source in PERSONAL_DATA_SOURCES:
			scrub = source.get("scrub") or {}
			if not scrub:
				continue
			meta = frappe.get_meta(source["doctype"])
			for fieldname, value in scrub.items():
				if value is not None:
					continue
				field = meta.get_field(fieldname)
				if field and field.reqd:
					offenders.append(f"{source['doctype']}.{fieldname}")

		self.assertEqual(
			offenders,
			[],
			"a mandatory field is scrubbed to NULL, leaving the row unsavable: "
			f"{offenders}",
		)


class TestGoogleCalendarErasure(IntegrationTestCase):
	"""The calendar behind a Google Meet setting is erased at its source.

	`LMS Google Meet Settings.google_calendar` is a mandatory Link, so it
	could be neither nulled nor markered without leaving the row invalid.
	Registering the target instead removes the personal data — the
	person's name, their calendar address, and the OAuth tokens — while
	the settings row keeps a valid reference and every Batch link to it
	stays intact.
	"""

	def test_google_calendar_is_registered(self):
		source = next(
			(s for s in PERSONAL_DATA_SOURCES if s["doctype"] == "Google Calendar"), None
		)
		self.assertIsNotNone(source, "Google Calendar is not classified")
		self.assertEqual(source["owner_field"], "user")

	def test_the_link_from_google_meet_settings_is_left_intact(self):
		"""Nulling it was the defect; it must not come back."""
		meet = next(
			s for s in PERSONAL_DATA_SOURCES if s["doctype"] == "LMS Google Meet Settings"
		)
		self.assertNotIn("google_calendar", meet.get("scrub") or {})

	def test_the_oauth_material_is_cleared(self):
		"""A refresh token outlives the session it was minted for.

		An erasure that leaves one behind has not ended the platform's
		access to that person's calendar.
		"""
		scrub = next(
			s for s in PERSONAL_DATA_SOURCES if s["doctype"] == "Google Calendar"
		)["scrub"]
		for credential in ("refresh_token", "authorization_code", "next_sync_token"):
			self.assertIn(credential, scrub, f"{credential} survives an erasure")

	def test_the_identifying_fields_are_covered(self):
		"""calendar_name is the primary key and usually the person's name."""
		scrub = next(
			s for s in PERSONAL_DATA_SOURCES if s["doctype"] == "Google Calendar"
		)["scrub"]
		self.assertEqual(scrub["calendar_name"], UNIQUE_ERASED_MARKER)
		self.assertIn("google_calendar_id", scrub)

	def test_a_real_calendar_row_is_scrubbed_and_still_linked(self):
		"""End to end, not just registry bookkeeping.

		The row must survive — a Google Meet setting holds a mandatory
		Link to it — carry none of the person's data, and still be
		reachable through that link.
		"""
		hash_ = frappe.generate_hash(length=6).lower()
		email = f"cal-{hash_}@example.com"
		subject = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Calendar",
				"last_name": "Subject",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		calendar = frappe.get_doc(
			{
				"doctype": "Google Calendar",
				"calendar_name": f"Calendar Subject {hash_}",
				"user": subject.name,
				"google_calendar_id": email,
			}
		)
		# The doctype refuses to save unless Google API credentials are
		# configured site-wide. Irrelevant here — the fixture only has to
		# exist for the erasure to find it.
		calendar.flags.ignore_validate = True
		calendar.insert(ignore_permissions=True)

		# The reason this disposition exists: a mandatory Link pointing at
		# the calendar. Without a row holding one, the test would prove the
		# calendar is scrubbed and nothing about the link staying valid.
		meet = frappe.get_doc(
			{
				"doctype": "LMS Google Meet Settings",
				"member": subject.name,
				"account_name": f"Meet {hash_}",
				"google_calendar": calendar.name,
				"enabled": 1,
			}
		)
		meet.flags.ignore_validate = True
		meet.insert(ignore_permissions=True)
		frappe.db.commit()
		# The subject is renamed by the erasure, so clean up by the
		# pseudonym rather than the original address — filtering on the old
		# one silently matches nothing and leaks rows into the next test.
		pseudonym = anonymized_email(subject.name)
		self.addCleanup(frappe.db.commit)
		self.addCleanup(
			lambda: frappe.db.sql(
				"delete from `tabGoogle Calendar` where user = %s", pseudonym
			)
		)

		anonymize_user(subject.name)
		frappe.db.commit()

		# Scoped to this subject: other tests leave scrubbed calendars
		# behind, so counting every erased row would be a suite-order bug.
		surviving = frappe.get_all(
			"Google Calendar", filters={"user": pseudonym}, pluck="name"
		)
		self.assertEqual(len(surviving), 1, "the calendar row did not survive the erasure")
		# Renamed, because calendar_name is the autoname field.
		self.assertNotIn(hash_, surviving[0], "the person's name survived as the primary key")
		self.assertEqual(
			frappe.db.get_value("Google Calendar", surviving[0], "google_calendar_id"),
			ERASED_MARKER,
			"the calendar address survived",
		)
		self.assertFalse(
			frappe.db.exists("Google Calendar", calendar.name),
			"the original identifying name is still a row",
		)

		# And the link that made scrubbing impossible still resolves. The
		# calendar was renamed, so this also checks the rename carried the
		# reference with it rather than leaving it dangling.
		surviving_meet = frappe.get_all(
			"LMS Google Meet Settings", filters={"member": pseudonym}, pluck="name"
		)
		self.assertEqual(len(surviving_meet), 1, "the Meet settings row did not survive")
		link = frappe.db.get_value(
			"LMS Google Meet Settings", surviving_meet[0], "google_calendar"
		)
		self.assertEqual(link, surviving[0], "the link no longer points at the calendar")
		self.assertTrue(
			frappe.db.exists("Google Calendar", link),
			"the link is dangling — the row it names does not exist",
		)

	def test_a_stored_credential_does_not_survive_the_erasure(self):
		"""A Password field's value is not in the column the scrub writes.

		frappe keeps it in `__Auth`; the column holds a placeholder. So
		`db.set_value` wrote the marker over the placeholder and left the
		secret exactly where it lives — an erasure that reported success
		while a still-valid refresh token stayed behind. Until that token
		is gone the platform can reach the calendar of someone who asked
		to be forgotten.
		"""
		hash_ = frappe.generate_hash(length=6).lower()
		secret = f"refresh-token-{hash_}"
		subject = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"cred-{hash_}@example.com",
				"first_name": "Credential",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		calendar = frappe.get_doc(
			{
				"doctype": "Google Calendar",
				"calendar_name": f"Credential Holder {hash_}",
				"user": subject.name,
				"google_calendar_id": f"cred-{hash_}@example.com",
				"refresh_token": secret,
			}
		)
		calendar.flags.ignore_validate = True
		calendar.insert(ignore_permissions=True)
		frappe.db.commit()

		# Stored where frappe really puts it, not in the column.
		self.assertEqual(
			get_decrypted_password("Google Calendar", calendar.name, "refresh_token"),
			secret,
			"the fixture never stored a credential, so this proves nothing",
		)

		pseudonym = anonymized_email(subject.name)
		self.addCleanup(frappe.db.commit)
		self.addCleanup(
			lambda: frappe.db.sql(
				"delete from `tabGoogle Calendar` where user = %s", pseudonym
			)
		)

		anonymize_user(subject.name)
		frappe.db.commit()

		scrubbed = frappe.get_all(
			"Google Calendar", filters={"user": pseudonym}, pluck="name"
		)[0]
		self.assertIsNone(
			get_decrypted_password(
				"Google Calendar", scrubbed, "refresh_token", raise_exception=False
			),
			"the refresh token survived the erasure",
		)

	def test_every_credential_on_a_scrubbed_doctype_is_registered(self):
		"""A Password field added later must not be able to slip through.

		Checked against each doctype's own metadata rather than a list —
		the same approach that found the mandatory-NULL cases nobody had
		reported.
		"""
		unscrubbed = []
		for source in PERSONAL_DATA_SOURCES:
			scrub = source.get("scrub") or {}
			if not scrub or not frappe.db.exists("DocType", source["doctype"]):
				continue
			for field in frappe.get_meta(source["doctype"]).fields:
				if field.fieldtype == "Password" and field.fieldname not in scrub:
					unscrubbed.append(f"{source['doctype']}.{field.fieldname}")

		self.assertEqual(
			unscrubbed, [], f"credentials survive an erasure: {unscrubbed}"
		)

import frappe
from frappe.tests import IntegrationTestCase

from lms.lms.language_platform import tenant_setup
from lms.lms.language_platform.tenant_setup import import_roster_rows


class TestRosterImportRowNumbers(IntegrationTestCase):
	"""An import failure must name the row in the *uploaded file*.

	`parse_roster` filters the rejected rows out, so a survivor's position
	in the remaining list no longer matches the spreadsheet. Reporting the
	position instead of the source line pointed the admin at the wrong
	row — and frequently at a row already listed in `errors` for an
	unrelated reason, so the report contradicted itself.
	"""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.hash = frappe.generate_hash(length=6)
		self.emails = []

	def tearDown(self):
		for email in self.emails:
			frappe.delete_doc(
				"User", email, force=True, ignore_permissions=True, ignore_missing=True
			)
		frappe.db.commit()
		super().tearDown()

	def _rows(self):
		"""Four rows: 1 imports, 2 and 3 are rejected, 4 fails on import."""
		good = f"good-{self.hash}@example.com"
		doomed = f"doomed-{self.hash}@example.com"
		self.emails += [good, doomed]
		return [
			{"email": good, "first_name": "Ada"},  # row 1 — imports
			{"email": "not-an-email", "first_name": "Bob"},  # row 2 — rejected
			{"email": "", "first_name": "Cem"},  # row 3 — rejected
			{"email": doomed, "first_name": "Dev"},  # row 4 — fails importing
		], doomed

	def test_import_failure_reports_the_source_row(self):
		rows, doomed = self._rows()

		original = tenant_setup._import_row

		def failing(row, batch_cache):
			if row.get("email") == doomed:
				raise frappe.ValidationError("simulated import failure")
			return original(row, batch_cache)

		tenant_setup._import_row = failing
		try:
			result = import_roster_rows(rows)
		finally:
			tenant_setup._import_row = original

		self.assertEqual(result["imported"], 1)

		failures = [e for e in result["errors"] if "simulated import failure" in str(e["errors"])]
		self.assertEqual(len(failures), 1, f"expected one import failure, got {result['errors']}")
		self.assertEqual(
			failures[0]["row"],
			4,
			"the failing row is line 4 of the file; reporting its position among "
			f"the survivors would say 2. Got {failures[0]['row']}.",
		)
		self.assertEqual(failures[0]["email"], doomed)

	def test_row_numbers_are_unique_across_parse_and_import_failures(self):
		"""The two error sources must not collide on one line number."""
		rows, doomed = self._rows()

		original = tenant_setup._import_row

		def failing(row, batch_cache):
			if row.get("email") == doomed:
				raise frappe.ValidationError("simulated import failure")
			return original(row, batch_cache)

		tenant_setup._import_row = failing
		try:
			result = import_roster_rows(rows)
		finally:
			tenant_setup._import_row = original

		reported = [e["row"] for e in result["errors"]]
		self.assertEqual(sorted(reported), [2, 3, 4])
		self.assertEqual(len(reported), len(set(reported)), f"duplicate row numbers: {reported}")

	def test_source_row_is_not_written_onto_the_user(self):
		"""The carried position is bookkeeping, not roster data."""
		rows, _ = self._rows()
		good = rows[0]["email"]

		import_roster_rows([rows[0]])

		user = frappe.get_doc("User", good)
		self.assertEqual(user.first_name, "Ada")
		self.assertFalse(getattr(user, "source_row", None))

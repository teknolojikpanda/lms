import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from lms.lms.doctype.lms_tenant.lms_tenant import (
	archive_tenant,
	assert_purge_allowed,
	get_purge_readiness,
	restore_tenant,
)
from lms.lms.language_platform.tenant_rules import DEFAULT_GRACE_DAYS


class TestTenantPurgeGuards(IntegrationTestCase):
	"""The grace period a tenant was archived under must be the one it is judged by.

	`archive_tenant` accepts a `grace_days` override, but the checks used
	to recompute eligibility with the default — so a tenant archived with
	a 7-day grace was still refused for 90, and the override silently did
	nothing.
	"""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.subdomain = f"purge-{frappe.generate_hash(length=6)}".lower()
		self.tenant = frappe.get_doc(
			{
				"doctype": "LMS Tenant",
				"tenant_name": "Purge Test Koleji",
				"subdomain": self.subdomain,
				"status": "Active",
				"seat_limit": 10,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	def tearDown(self):
		frappe.delete_doc(
			"LMS Tenant", self.subdomain, force=True, ignore_permissions=True, ignore_missing=True
		)
		frappe.db.commit()
		super().tearDown()

	def _archive(self, grace_days, days_ago):
		archive_tenant(self.subdomain, "Contract ended", grace_days=grace_days)
		# Backdate the archival so the grace period has had time to elapse.
		frappe.db.set_value(
			"LMS Tenant",
			self.subdomain,
			{
				"archived_at": add_to_date(now_datetime(), days=-days_ago),
				"export_location": "s3://exports/purge-test.tar.gz",
				"backup_verified": 1,
			},
		)
		frappe.db.commit()

	def test_short_grace_override_is_honoured(self):
		"""Archived 10 days ago under a 7-day grace: eligible."""
		self._archive(grace_days=7, days_ago=10)

		self.assertEqual(frappe.db.get_value("LMS Tenant", self.subdomain, "grace_days"), 7)
		readiness = get_purge_readiness(self.subdomain)
		self.assertTrue(
			readiness["ready"],
			f"override ignored; still blocked by {readiness['blockers']}",
		)
		assert_purge_allowed(self.subdomain)  # must not raise

	def test_default_grace_still_blocks_the_same_tenant(self):
		"""The same 10-day-old archival under the default is not eligible.

		Pins the contrast: without this, the case above could pass because
		the guard was skipped rather than because the override was read.
		"""
		self._archive(grace_days=DEFAULT_GRACE_DAYS, days_ago=10)

		readiness = get_purge_readiness(self.subdomain)
		self.assertFalse(readiness["ready"])
		self.assertTrue(any("Grace period" in b for b in readiness["blockers"]))
		with self.assertRaises(frappe.ValidationError):
			assert_purge_allowed(self.subdomain)

	def test_a_longer_grace_override_also_holds(self):
		"""Overrides must be able to extend, not only shorten."""
		self._archive(grace_days=180, days_ago=100)

		self.assertFalse(get_purge_readiness(self.subdomain)["ready"])
		with self.assertRaises(frappe.ValidationError):
			assert_purge_allowed(self.subdomain)

	def test_guards_refuse_without_a_verified_backup(self):
		self._archive(grace_days=7, days_ago=10)
		frappe.db.set_value("LMS Tenant", self.subdomain, "backup_verified", 0)
		frappe.db.commit()

		with self.assertRaises(frappe.ValidationError):
			assert_purge_allowed(self.subdomain)

	def test_guards_refuse_without_a_recorded_export(self):
		self._archive(grace_days=7, days_ago=10)
		frappe.db.set_value("LMS Tenant", self.subdomain, "export_location", None)
		frappe.db.commit()

		with self.assertRaises(frappe.ValidationError):
			assert_purge_allowed(self.subdomain)

	def test_restoring_clears_the_grace_period(self):
		"""A restored tenant carries no stale clock into a later archival."""
		self._archive(grace_days=7, days_ago=10)
		restore_tenant(self.subdomain)
		frappe.db.commit()

		doc = frappe.get_doc("LMS Tenant", self.subdomain)
		self.assertEqual(doc.status, "Active")
		self.assertFalse(doc.grace_days)
		self.assertFalse(doc.purge_after)

		with self.assertRaises(frappe.ValidationError):
			assert_purge_allowed(self.subdomain)  # not archived any more

"""A corrected subdomain has to take the document name with it.

`LMS Tenant` is `autoname: field:subdomain`, so the name is fixed at
insert. `validate_subdomain_field` deliberately allows the field to
change while the tenant is still Requested — correcting a typo before
any site exists is exactly what that branch is for — but changing the
field did not rename the document. Since every server-side entry point
reaches a tenant by subdomain, the correction produced a registry row
the provisioner could no longer find.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms.lms.doctype.lms_tenant.lms_tenant import (
	mark_provisioning,
	register_tenant,
)


class TestTenantSubdomainRename(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.hash = frappe.generate_hash(length=6).lower()
		self.typo = f"ankra-{self.hash}"
		self.corrected = f"ankara-{self.hash}"
		self.names = [self.typo, self.corrected]

		# rename_doc enqueues a global-search rebuild; a real job here
		# would outlive the test and fight it for row locks.
		enqueue = patch("frappe.enqueue")
		enqueue.start()
		self.addCleanup(enqueue.stop)

	def tearDown(self):
		for name in self.names:
			frappe.delete_doc(
				"LMS Tenant", name, force=True, ignore_permissions=True, ignore_missing=True
			)
		frappe.db.commit()
		super().tearDown()

	def _tenant(self, subdomain, status="Requested"):
		doc = frappe.get_doc(
			{
				"doctype": "LMS Tenant",
				"tenant_name": "Ankara Koleji",
				"subdomain": subdomain,
				"status": status,
				"seat_limit": 100,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		return doc

	def test_correcting_the_subdomain_actually_changes_it(self):
		"""The defect, at its plainest: the save reported success and kept the typo.

		`_sync_autoname_field` copies `name` back over an
		``autoname: field:`` field during every save, so the correction
		was accepted by validation and then discarded without a word.
		"""
		doc = self._tenant(self.typo)
		self.assertEqual(doc.name, self.typo)

		doc.subdomain = self.corrected
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		self.assertEqual(doc.subdomain, self.corrected, "the correction was silently discarded")
		self.assertEqual(
			frappe.db.get_value("LMS Tenant", self.corrected, "subdomain"),
			self.corrected,
			"the stored subdomain is not the corrected one",
		)

	def test_the_document_name_follows_the_subdomain(self):
		"""Name and field must not be allowed to disagree.

		The name is the lookup key for every server-side entry point; a
		row whose name and subdomain differ is unreachable by one of them.
		"""
		doc = self._tenant(self.typo)
		doc.subdomain = self.corrected
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		self.assertEqual(doc.name, self.corrected, "the in-memory doc kept the old name")
		self.assertTrue(frappe.db.exists("LMS Tenant", self.corrected))
		self.assertFalse(
			frappe.db.exists("LMS Tenant", self.typo), "the old name went on squatting"
		)

	def test_the_provisioner_can_find_the_corrected_tenant(self):
		"""The consequence that matters — this is how the CLI looks it up.

		`register_tenant` hands the operator a command built from the
		*field*, so after a correction the CLI was told a subdomain that
		did not resolve.
		"""
		doc = self._tenant(self.typo)
		doc.subdomain = self.corrected
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		mark_provisioning(self.corrected, notes="Provisioning after a corrected subdomain")
		frappe.db.commit()

		self.assertEqual(
			frappe.db.get_value("LMS Tenant", self.corrected, "status"), "Provisioning"
		)

	def test_the_registered_next_step_names_a_tenant_that_exists(self):
		"""What `register_tenant` tells the operator to run must work."""
		registered = register_tenant("Ankara Koleji", self.typo, seat_limit=50)
		frappe.db.commit()

		doc = frappe.get_doc("LMS Tenant", registered["name"])
		doc.subdomain = self.corrected
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		# The CLI is invoked with the subdomain, so that is what has to resolve.
		self.assertEqual(frappe.get_doc("LMS Tenant", self.corrected).subdomain, self.corrected)

	def test_the_site_name_follows_the_corrected_subdomain(self):
		"""The name, the field and the derived site name must agree."""
		with patch.dict(frappe.conf, {"lms_platform_domain": "dilplatformu.com"}):
			doc = self._tenant(self.typo)
			doc.subdomain = self.corrected
			doc.save(ignore_permissions=True)
			frappe.db.commit()

			self.assertEqual(doc.name, self.corrected)
			self.assertEqual(doc.site_name, f"{self.corrected}.dilplatformu.com")

	def test_the_correction_is_recorded(self):
		"""A registry that changes identity silently cannot be audited."""
		doc = self._tenant(self.typo)
		doc.subdomain = self.corrected
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "LMS Tenant", "reference_name": self.corrected},
			pluck="content",
		)
		self.assertTrue(
			any(self.typo in (c or "") for c in comments),
			f"no record of the rename on the tenant: {comments}",
		)

	def test_a_provisioned_tenant_still_cannot_be_renamed(self):
		"""The guard this fix must not weaken.

		Once a site exists the subdomain names a live directory, so the
		refusal has to stay — renaming the row would orphan it.
		"""
		doc = self._tenant(self.typo, status="Active")
		doc.subdomain = self.corrected

		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)
		frappe.db.rollback()

		self.assertTrue(frappe.db.exists("LMS Tenant", self.typo))
		self.assertFalse(frappe.db.exists("LMS Tenant", self.corrected))

	def test_an_unchanged_subdomain_does_not_rename(self):
		"""Ordinary saves must not churn the primary key."""
		doc = self._tenant(self.typo)

		with patch("lms.lms.doctype.lms_tenant.lms_tenant.rename_doc") as renamer:
			doc.seat_limit = 250
			doc.save(ignore_permissions=True)
			frappe.db.commit()

		renamer.assert_not_called()
		self.assertEqual(doc.name, self.typo)

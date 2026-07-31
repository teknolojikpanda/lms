"""RPO reporting must measure the backups that exist.

`bench backup` writes dumps to `sites/<site>/private/backups` and creates
no File document. Reading File rows therefore found nothing on a site
that was being backed up correctly, and the RPO panel said "unknown"
however healthy the platform was — which trains an operator to ignore it.
"""

import os
import time
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from lms.lms.language_platform.dr_rules import STATUS_UNKNOWN
from lms.lms.language_platform.dr import (
	_backup_directory,
	_last_backup_time,
	_latest_backup_on_disk,
	get_dr_readiness,
)


class TestBackupFreshness(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.backups = _backup_directory()
		self.backups.mkdir(parents=True, exist_ok=True)
		self.written = []

	def tearDown(self):
		for path in self.written:
			if path.exists():
				path.unlink()
		frappe.db.commit()
		super().tearDown()

	def _write_dump(self, name, age_minutes=0):
		return self._write_dump_at(self.backups, name, age_minutes)

	def _write_dump_at(self, directory, name, age_minutes=0):
		"""A dump on disk, aged by setting its mtime like a real one."""
		path = directory / name
		path.write_bytes(b"not a real dump")
		self.written.append(path)
		if age_minutes:
			stamp = time.time() - age_minutes * 60
			os.utime(path, (stamp, stamp))
		return path

	def test_a_dump_on_disk_is_found(self):
		"""The defect: only File rows were consulted, so this was missed."""
		self._write_dump("20260731_120000-staging-database.sql.gz", age_minutes=5)

		found = _latest_backup_on_disk()
		self.assertIsNotNone(found, "a dump written by bench backup was not seen")
		age = (now_datetime() - found).total_seconds() / 60
		self.assertAlmostEqual(age, 5, delta=2, msg=f"mtime read as the wrong time: {found}")

	def test_the_newest_dump_wins(self):
		self._write_dump("20260701_000000-staging-database.sql.gz", age_minutes=600)
		self._write_dump("20260731_120000-staging-database.sql.gz", age_minutes=3)

		age = (now_datetime() - _latest_backup_on_disk()).total_seconds() / 60
		self.assertLess(age, 30, "an older dump shadowed the newest one")

	def test_unrelated_files_are_ignored(self):
		"""Only database dumps say anything about RPO."""
		self._write_dump("20260731_120000-staging-files.tar", age_minutes=1)
		self._write_dump("20260731_120000-staging-site_config_backup.json", age_minutes=1)

		self.assertIsNone(_latest_backup_on_disk())

	def test_no_backup_reads_as_unknown_not_healthy(self):
		"""Nothing discoverable must never look like a fresh backup."""
		self.assertIsNone(_last_backup_time())

		readiness = get_dr_readiness()
		rpo = next(check for check in readiness["checks"] if check["check"].startswith("RPO"))
		self.assertEqual(rpo["status"], STATUS_UNKNOWN, "an absent backup reported as healthy")

	def test_readiness_reports_a_fresh_backup_as_ok(self):
		"""The whole point: a healthy platform must be able to say so."""
		self._write_dump("20260731_120000-staging-database.sql.gz", age_minutes=1)

		readiness = get_dr_readiness()
		rpo = next(check for check in readiness["checks"] if check["check"].startswith("RPO"))
		self.assertEqual(
			rpo["status"], "ok", f"a one-minute-old backup did not read as within RPO: {rpo}"
		)

	def test_a_stale_backup_breaches_the_target(self):
		settings = frappe.get_cached_doc("LMS Language Settings")
		target = settings.rpo_target_minutes or 60
		self._write_dump("20260731_120000-staging-database.sql.gz", age_minutes=target * 4)

		readiness = get_dr_readiness()
		rpo = next(check for check in readiness["checks"] if check["check"].startswith("RPO"))
		# Specifically *stale*, not merely "not ok" — unknown would satisfy
		# that too, and did while the dump on disk was going unseen.
		self.assertNotEqual(rpo["status"], STATUS_UNKNOWN, "the stale dump was not even found")
		self.assertNotEqual(rpo["status"], "ok", "a stale backup passed the RPO check")
		self.assertIsNotNone(rpo["age_minutes"])

	def test_a_relocated_backup_directory_is_followed(self):
		"""A site that moves `backup_path` is still backed up.

		Hardcoding `private/backups` would call it unbacked-up — the same
		mistake as reading File rows, one directory along.
		"""
		# Built without asking _backup_directory(), or the test would just
		# agree with whatever that returns and prove nothing.
		relative = f"private/backups-moved-{frappe.generate_hash(length=5)}"
		moved = Path(frappe.get_site_path(relative))
		moved.mkdir(parents=True, exist_ok=True)
		self.addCleanup(lambda: moved.exists() and moved.rmdir())

		frappe.local.conf["backup_path"] = relative
		self.addCleanup(frappe.local.conf.pop, "backup_path", None)

		self.assertFalse(
			list(self.backups.glob("*-database.sql.gz")),
			"the default directory holds a dump, so this test cannot tell the two apart",
		)
		self._write_dump_at(moved, "20260731_120000-staging-database.sql.gz", age_minutes=4)

		found = _latest_backup_on_disk()
		self.assertIsNotNone(found, "a dump in the configured backup directory was not seen")
		self.assertAlmostEqual((now_datetime() - found).total_seconds() / 60, 4, delta=2)

	def test_a_file_document_still_counts(self):
		"""Deployments that attach dumps keep working."""
		attached = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"20260731_120000-attached-{frappe.generate_hash(length=5)}-database.sql.gz",
				"is_private": 1,
				"content": "x",
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"File", attached.name, "creation", add_to_date(now_datetime(), minutes=-2),
			update_modified=False,
		)
		frappe.db.commit()
		self.addCleanup(
			lambda: frappe.db.exists("File", attached.name)
			and frappe.delete_doc("File", attached.name, force=True, ignore_permissions=True)
		)

		self.assertIsNotNone(_last_backup_time())

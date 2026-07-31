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
		# A dump left by a real `bench backup` on this site would decide
		# these tests instead of the fixtures — the newest one wins, and it
		# would not be ours. Each test gets an empty directory of its own,
		# via the same site-config knob a deployment would use.
		relative = f"private/test-backups-{frappe.generate_hash(length=8)}"
		frappe.local.conf["backup_path"] = relative
		self.addCleanup(frappe.local.conf.pop, "backup_path", None)

		self.backups = Path(frappe.get_site_path(relative))
		self.backups.mkdir(parents=True, exist_ok=True)
		self.addCleanup(self._remove_directory, self.backups)

	def tearDown(self):
		frappe.db.commit()
		super().tearDown()

	def _remove_directory(self, directory):
		if not directory.exists():
			return
		for leftover in directory.iterdir():
			leftover.unlink()
		directory.rmdir()

	def _write_dump(self, name, age_minutes=0, content=b"not a real dump"):
		return self._write_dump_at(self.backups, name, age_minutes, content)

	def _write_dump_at(self, directory, name, age_minutes=0, content=b"not a real dump"):
		"""A dump on disk, aged by setting its mtime like a real one."""
		path = directory / name
		path.write_bytes(content)
		self.addCleanup(lambda: path.exists() and path.unlink())
		if age_minutes:
			stamp = time.time() - age_minutes * 60
			os.utime(path, (stamp, stamp))
		return path

	def test_the_default_location_is_the_site_backup_directory(self):
		"""With nothing configured, look where `bench backup` writes."""
		frappe.local.conf.pop("backup_path", None)

		self.assertEqual(
			_backup_directory().resolve(),
			Path(frappe.get_site_path("private", "backups")).resolve(),
		)

	def test_an_empty_dump_is_not_a_backup(self):
		"""A failed backup leaves a zero-byte file with a fresh mtime."""
		self._write_dump("20260731_120000-staging-database.sql.gz", content=b"")

		self.assertIsNone(_latest_backup_on_disk(), "an empty dump was counted as a backup")

	def test_an_empty_dump_does_not_shadow_a_good_one(self):
		"""The failure mode that matters: last night's backup broke.

		A zero-byte dump written minutes ago must not make yesterday's
		real one look current — that reports healthy exactly when the
		backups have stopped working.
		"""
		self._write_dump("20260730_090000-staging-database.sql.gz", age_minutes=90)
		self._write_dump("20260731_120000-staging-database.sql.gz", content=b"")

		age = (now_datetime() - _latest_backup_on_disk()).total_seconds() / 60
		self.assertAlmostEqual(age, 90, delta=2, msg="an empty dump was taken as the backup")

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
		# The status alone would still be "ok" if the age were computed
		# wrongly but happened to land under the target.
		self.assertIsNotNone(rpo["age_minutes"], f"no age reported: {rpo}")
		self.assertLess(rpo["age_minutes"], 5, f"a one-minute-old backup aged wrongly: {rpo}")

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
		self._write_dump_at(moved, "20260731_120000-staging-database.sql.gz", age_minutes=4)
		self.assertFalse(
			list(self.backups.glob("*-database.sql.gz")),
			"the dump is in the directory this test relocated away from",
		)

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

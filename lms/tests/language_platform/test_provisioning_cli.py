"""Ordering guarantees in the tenant purge CLI.

A pure test: `provision_tenant` imports only the stdlib and the pure
`tenant_rules`, so this runs without a bench.

The bug these cover is not a wrong result but a wrong *order*. The CLI
used to drop the site first and let the registry refuse afterwards, so a
tenant that failed a guard was deleted anyway and the registry was left
claiming it was still archived. Refusing after an irreversible step is
not a guard.
"""

import argparse
import importlib.util
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_cli() -> types.ModuleType:
	"""Import the CLI by path — `provisioning/` is not a package."""
	if str(REPO_ROOT) not in sys.path:
		sys.path.insert(0, str(REPO_ROOT))
	spec = importlib.util.spec_from_file_location(
		"provision_tenant_under_test", REPO_ROOT / "provisioning" / "provision_tenant.py"
	)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


cli = _load_cli()


class TestReportedArtifacts(unittest.TestCase):
	"""Backup verification must check the file *this* run wrote.

	Scanning the backup directory and taking the newest dump reports
	success for a leftover from an earlier run when the current backup
	landed elsewhere or produced nothing — verifying a backup that does
	not describe the data about to be destroyed.
	"""

	SAMPLE = """Backup Summary for site1.local at 2026-07-29 12:00:00
	Config  : ./sites/site1.local/private/backups/20260729_120000-site1_local-site_config_backup.json 1.2KiB
	Database: ./sites/site1.local/private/backups/20260729_120000-site1_local-database.sql.gz 4.5MiB
	"""

	def test_parses_the_names_bench_reported(self):
		names = cli.reported_artifacts(self.SAMPLE)
		self.assertIn("20260729_120000-site1_local-database.sql.gz", names)
		self.assertIn("20260729_120000-site1_local-site_config_backup.json", names)

	def test_paths_are_reduced_to_bare_filenames(self):
		"""Compared against directory entries, so directories must not leak in."""
		for name in cli.reported_artifacts(self.SAMPLE):
			self.assertNotIn("/", name)

	def test_empty_or_unparseable_output_yields_nothing(self):
		for output in ("", None, "Backup failed", "no artefacts here"):
			self.assertEqual(cli.reported_artifacts(output), set())


class TestPurgeOrdering(unittest.TestCase):
	def setUp(self):
		self.calls = []
		self._real_run = cli.run

		def recording_run(command, *, dry_run=False, check=True):
			self.calls.append(list(command))
			return self.refuse_hook(command)

		cli.run = recording_run
		self.addCleanup(lambda: setattr(cli, "run", self._real_run))
		self.refuse_hook = lambda command: ""

	def _args(self, **overrides):
		args = {
			"subdomain": "ankara-koleji",
			"base_domain": "dilplatformu.com",
			"control_site": "app.dilplatformu.com",
			"confirm": "ankara-koleji",
			"yes_i_am_sure": True,
			"db_root_password": None,
			"dry_run": False,
		}
		args.update(overrides)
		return argparse.Namespace(**args)

	def _index_of(self, needle):
		for i, command in enumerate(self.calls):
			if any(needle in part for part in command):
				return i
		return -1

	def test_guards_are_checked_before_the_site_is_dropped(self):
		self.assertEqual(cli.purge(self._args()), 0)

		guard = self._index_of("assert_purge_allowed")
		drop = self._index_of("drop-site")
		record = self._index_of("mark_purged")

		self.assertNotEqual(guard, -1, "the registry guard was never checked")
		self.assertNotEqual(drop, -1, "the site was never dropped")
		self.assertLess(guard, drop, "guards must be checked BEFORE the irreversible drop")
		self.assertLess(drop, record, "the purge is recorded after the drop")

	def test_a_refused_guard_stops_before_anything_is_destroyed(self):
		"""The regression: a refusal must cost nothing."""

		def refuse(command):
			if any("assert_purge_allowed" in part for part in command):
				raise cli.ProvisioningError("Purge Refused: Backup has not been verified.")
			return ""

		self.refuse_hook = refuse

		with self.assertRaises(cli.ProvisioningError):
			cli.purge(self._args())

		self.assertEqual(
			self._index_of("drop-site"), -1, "the site was dropped despite a refused guard"
		)
		self.assertEqual(self._index_of("mark_purged"), -1)

	def test_confirmation_mismatch_never_reaches_the_registry(self):
		self.assertEqual(cli.purge(self._args(confirm="ankara")), 5)
		self.assertEqual(self.calls, [], "a bad confirmation must not run any command")

	def test_missing_are_you_sure_never_reaches_the_registry(self):
		self.assertEqual(cli.purge(self._args(yes_i_am_sure=False)), 5)
		self.assertEqual(self.calls, [])

	def test_purge_requires_a_control_site(self):
		"""Without the registry there is nothing to check the guards against."""
		self.assertEqual(cli.purge(self._args(control_site=None)), 2)
		self.assertEqual(self.calls, [])


if __name__ == "__main__":
	unittest.main()

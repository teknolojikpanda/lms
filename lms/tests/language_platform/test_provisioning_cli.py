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


class TestSecretRedaction(unittest.TestCase):
	"""Secrets must not reach terminal scrollback or a CI log.

	`run` echoes every command and repeats it in failure messages, which
	is worth keeping — an operator needs to see what ran. But the value
	after a password flag outlives the run in whatever captured it, so
	only the rendering may carry it.
	"""

	ADMIN = "s3cret-admin-pw"
	ROOT = "s3cret-root-pw"

	def _command(self):
		return [
			"bench",
			"new-site",
			"ankara-koleji.dilplatformu.com",
			"--admin-password",
			self.ADMIN,
			"--mariadb-root-password",
			self.ROOT,
			"--no-mariadb-socket",
		]

	def test_secret_values_are_masked(self):
		shown = cli.redact_command(self._command())
		self.assertNotIn(self.ADMIN, shown)
		self.assertNotIn(self.ROOT, shown)

	def test_the_flags_themselves_are_still_visible(self):
		"""Redaction must not make the log useless."""
		shown = cli.redact_command(self._command())
		self.assertIn("--admin-password", shown)
		self.assertIn("--mariadb-root-password", shown)
		self.assertIn("bench new-site", shown)
		self.assertIn("ankara-koleji.dilplatformu.com", shown)

	def test_the_equals_form_is_masked_too(self):
		shown = cli.redact_command(["bench", f"--admin-password={self.ADMIN}"])
		self.assertNotIn(self.ADMIN, shown)
		self.assertIn("--admin-password=", shown)

	def test_a_value_that_merely_follows_a_normal_flag_is_kept(self):
		shown = cli.redact_command(["bench", "--site", "ankara.example.com"])
		self.assertIn("ankara.example.com", shown)

	def test_the_executed_command_is_not_altered(self):
		"""Only the display is redacted; the real call still needs the value."""
		command = self._command()
		cli.redact_command(command)
		self.assertIn(self.ADMIN, command)
		self.assertIn(self.ROOT, command)

	def test_the_echoed_command_does_not_carry_the_secret(self):
		"""The leak on every successful run, not just failures.

		`run` echoes before executing, so this line is written whether or
		not anything goes wrong — it is the path that put passwords into
		operator scrollback in the first place.
		"""
		import contextlib
		import io as _io

		buffer = _io.StringIO()
		with contextlib.redirect_stdout(buffer):
			cli.run(self._command(), dry_run=True)
		echoed = buffer.getvalue()

		self.assertIn("bench new-site", echoed, "the command was not echoed at all")
		self.assertNotIn(self.ADMIN, echoed)
		self.assertNotIn(self.ROOT, echoed)

	def test_failures_do_not_quote_the_secret(self):
		"""The error path repeats the command, and used to repeat the secret."""
		captured = {}

		class _Result:
			returncode = 1
			stdout = ""
			stderr = "bench: something went wrong"

		real = cli.subprocess.run
		cli.subprocess.run = lambda *a, **k: _Result()
		try:
			with self.assertRaises(cli.ProvisioningError) as caught:
				cli.run(self._command())
			captured["message"] = str(caught.exception)
		finally:
			cli.subprocess.run = real

		self.assertNotIn(self.ADMIN, captured["message"])
		self.assertNotIn(self.ROOT, captured["message"])
		self.assertIn("Command failed (1)", captured["message"])

	def test_a_non_string_token_still_arms_masking(self):
		"""A flag compared in its raw form would not arm the mask.

		argparse hands back strings, but callers build these lists by
		hand; one non-string token before a secret would print it in full.
		"""

		class _Flag:
			def __str__(self):
				return "--admin-password"

		shown = cli.redact_command(["bench", _Flag(), self.ADMIN])
		self.assertNotIn(self.ADMIN, shown)

	def test_bench_side_flags_are_covered_though_the_parser_never_sees_them(self):
		"""Why SECRET_FLAGS is a list and not derived from the parser.

		`--mariadb-root-password` and `--root-password` are bench's flags,
		passed outward and never parsed here. A parser-derived set would
		omit exactly the two carrying the database root password.
		"""
		parser_flags = {
			option
			for action in cli.build_parser()._actions
			for option in action.option_strings
		}
		for flag in ("--mariadb-root-password", "--root-password"):
			self.assertNotIn(flag, parser_flags, f"{flag} is now a parser option; revisit this")
			self.assertIn(flag, cli.SECRET_FLAGS, f"{flag} would be echoed in full")

	def test_every_password_argument_is_declared_secret(self):
		"""The drift guard: a new password flag must be added to SECRET_FLAGS.

		Redaction is an allowlist, so a flag nobody registers leaks
		silently — which is exactly how the original leak went unnoticed.
		"""
		parser = cli.build_parser()
		password_flags = {
			option
			for action in parser._actions
			for option in action.option_strings
			if "password" in option
		}
		missing = sorted(password_flags - cli.SECRET_FLAGS)
		self.assertEqual(
			missing, [], f"these password flags would be echoed in full: {missing}"
		)


class TestGeneratedPasswordDisclosure(unittest.TestCase):
	"""A caller-supplied password must never be echoed.

	The advice to pass `--admin-password` from a secret store is only
	advice if taking it helps: printing it here would land it in the same
	log the caller moved it to a secret store to avoid.
	"""

	SUPPLIED = "from-the-secret-store"

	def _run_provision(self, admin_password=None):
		"""Drive `provision` with real parser defaults, stubbing the shell.

		Built from `build_parser` rather than a hand-made Namespace so a
		new option cannot make this test fail for want of an attribute.
		"""
		import contextlib
		import io as _io

		argv = ["--subdomain", "ankara-koleji", "--base-domain", "dilplatformu.com"]
		if admin_password:
			argv += ["--admin-password", admin_password]
		args = cli.build_parser().parse_args(argv)

		real_run, real_exists = cli.run, cli.site_exists
		cli.run = lambda command, **k: ""
		cli.site_exists = lambda site: False
		buffer = _io.StringIO()
		try:
			with contextlib.redirect_stdout(buffer):
				cli.provision(args)
		finally:
			cli.run, cli.site_exists = real_run, real_exists
		return buffer.getvalue()

	def test_a_supplied_password_is_not_printed(self):
		output = self._run_provision(admin_password=self.SUPPLIED)
		self.assertNotIn(self.SUPPLIED, output, "the caller's own secret was echoed back")
		self.assertIn("not echoed", output)

	def test_a_generated_password_is_printed(self):
		"""It has to reach the operator; it is not recoverable afterwards."""
		output = self._run_provision()
		self.assertIn("Administrator password:", output)
		self.assertIn("password manager", output)
		self.assertNotIn("not echoed", output)


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

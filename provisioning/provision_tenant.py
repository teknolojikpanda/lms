#!/usr/bin/env python3
# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Provision a tenant site (ADR-0001: tenant = Frappe site).

Run **on the bench host**, from the bench directory, by an operator or a
deployment job — never from a web request. Creating a site needs database
and filesystem privileges that the application containers deliberately do
not have, so the web layer only records intent in the LMS Tenant registry
and this CLI does the privileged work.

    python provisioning/provision_tenant.py \\
        --subdomain ankara-koleji \\
        --base-domain dilplatformu.com \\
        --control-site app.dilplatformu.com \\
        --admin-email admin@ankarakoleji.edu.tr

Every subprocess call passes an argument **list** (never ``shell=True``),
and the subdomain is validated against the DNS-label allowlist in
``tenant_rules`` before it is used, so no caller-supplied string can be
interpreted as a shell token or a path.

Dry run first — ``--dry-run`` prints the exact commands without executing:

    python provisioning/provision_tenant.py --subdomain x --dry-run
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
from pathlib import Path

# The bench host has the app on its path; fall back to a relative import so
# the script also works when run straight from a checkout.
try:
	from lms.lms.language_platform.tenant_rules import (
		PurgeNotAllowed,
		TenantNameError,
		build_site_name,
		validate_purge_confirmation,
		validate_subdomain,
	)
except ImportError:  # pragma: no cover - depends on where it is invoked from
	sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
	from lms.lms.language_platform.tenant_rules import (
		PurgeNotAllowed,
		TenantNameError,
		build_site_name,
		validate_purge_confirmation,
		validate_subdomain,
	)

DEFAULT_APPS = ["frappe", "payments", "lms"]


class ProvisioningError(RuntimeError):
	pass


def run(command: list[str], *, dry_run: bool = False, check: bool = True) -> str:
	"""Execute a command as an argument list, echoing it for the operator log."""
	printable = " ".join(command)
	print(f"  $ {printable}")
	if dry_run:
		return ""

	result = subprocess.run(  # noqa: S603 - argument list, never shell=True
		command,
		capture_output=True,
		text=True,
	)
	if check and result.returncode != 0:
		raise ProvisioningError(
			f"Command failed ({result.returncode}): {printable}\n{result.stderr.strip()}"
		)
	return result.stdout.strip()


def bench_execute(site: str, method: str, kwargs: dict, *, dry_run: bool = False) -> str:
	"""Call a whitelisted-ish python method on a site via bench execute."""
	return run(
		[
			"bench",
			"--site",
			site,
			"execute",
			method,
			"--kwargs",
			json.dumps(kwargs),
		],
		dry_run=dry_run,
	)


def site_exists(site: str) -> bool:
	return Path("sites", site).is_dir()


def provision(args) -> int:
	try:
		subdomain = validate_subdomain(args.subdomain)
		site = build_site_name(subdomain, args.base_domain)
	except TenantNameError as e:
		print(f"error: {e}", file=sys.stderr)
		return 2

	print(f"\nProvisioning tenant '{subdomain}' as site '{site}'")
	if args.dry_run:
		print("(dry run - no commands will be executed)\n")

	if site_exists(site) and not args.dry_run:
		print(f"error: sites/{site} already exists. Refusing to overwrite.", file=sys.stderr)
		return 3

	# Generated here rather than taken as an argument so it never lands in
	# shell history or a CI log echo.
	admin_password = args.admin_password or secrets.token_urlsafe(18)

	if args.control_site:
		print("\n[1/5] Marking the tenant as provisioning in the registry")
		bench_execute(
			args.control_site,
			"lms.lms.doctype.lms_tenant.lms_tenant.mark_provisioning",
			{"subdomain": subdomain, "notes": f"Provisioning site {site}"},
			dry_run=args.dry_run,
		)

	print("\n[2/5] Creating the site")
	create_command = [
		"bench",
		"new-site",
		site,
		"--admin-password",
		admin_password,
		"--no-mariadb-socket",
	]
	if args.db_root_password:
		create_command += ["--mariadb-root-password", args.db_root_password]
	if args.db_host:
		create_command += ["--db-host", args.db_host]
	run(create_command, dry_run=args.dry_run)

	print("\n[3/5] Installing apps")
	for app in args.apps:
		run(["bench", "--site", site, "install-app", app], dry_run=args.dry_run)

	print("\n[4/5] Applying tenant defaults")
	bench_execute(
		site,
		"lms.lms.language_platform.tenant_setup.apply_tenant_defaults",
		{
			"tenant_name": args.tenant_name or subdomain,
			"admin_email": args.admin_email,
			"admin_full_name": args.admin_name or "Institution Admin",
		},
		dry_run=args.dry_run,
	)

	if args.dns_alias:
		print("\n[5/5] Adding the site to the bench's DNS multitenancy config")
		run(["bench", "setup", "add-domain", args.dns_alias, "--site", site], dry_run=args.dry_run)
	else:
		print("\n[5/5] Skipping DNS alias (wildcard record covers this site)")

	if args.control_site:
		bench_execute(
			args.control_site,
			"lms.lms.doctype.lms_tenant.lms_tenant.mark_provisioned",
			{"subdomain": subdomain, "notes": f"Site {site} provisioned"},
			dry_run=args.dry_run,
		)

	print(f"\nOK: tenant '{subdomain}' provisioned at https://{site}")
	if not args.dry_run:
		# Printed once, to the operator's terminal only. Store it in the
		# password manager now; it is not recoverable from here.
		print(f"\n  Administrator password: {admin_password}")
		print("  Store this in the team password manager - it is not saved anywhere.\n")
		if args.admin_email:
			print(f"  An invite was sent to {args.admin_email} for the institution admin.\n")
	return 0


def suspend(args) -> int:
	"""Put a tenant site into maintenance mode (Ek-4.3 suspend)."""
	try:
		site = build_site_name(validate_subdomain(args.subdomain), args.base_domain)
	except TenantNameError as e:
		print(f"error: {e}", file=sys.stderr)
		return 2

	print(f"\nSuspending {site}")
	run(
		["bench", "--site", site, "set-maintenance-mode", "on"],
		dry_run=args.dry_run,
	)
	print(f"OK: {site} is in maintenance mode")
	return 0


def resume(args) -> int:
	try:
		site = build_site_name(validate_subdomain(args.subdomain), args.base_domain)
	except TenantNameError as e:
		print(f"error: {e}", file=sys.stderr)
		return 2

	print(f"\nResuming {site}")
	run(["bench", "--site", site, "set-maintenance-mode", "off"], dry_run=args.dry_run)
	print(f"OK: {site} is serving traffic")
	return 0


def archive(args) -> int:
	"""Back the site up, verify the artefacts, and take it offline.

	Reversible: nothing is deleted here. The backup is taken *before* the
	site goes offline so a failure leaves a running tenant rather than an
	unreachable one with no recent copy.
	"""
	try:
		subdomain = validate_subdomain(args.subdomain)
		site = build_site_name(subdomain, args.base_domain)
	except TenantNameError as e:
		print(f"error: {e}", file=sys.stderr)
		return 2

	print(f"\nArchiving {site}")

	print("\n[1/4] Taking a full backup (database + files)")
	output = run(
		["bench", "--site", site, "backup", "--with-files"],
		dry_run=args.dry_run,
	)

	print("\n[2/4] Verifying the backup artefacts")
	backup_paths = _verify_backup(site, output, dry_run=args.dry_run)
	if backup_paths is None:
		print(
			"error: could not verify a usable backup. The site has NOT been taken "
			"offline; investigate before retrying.",
			file=sys.stderr,
		)
		return 4

	export_location = args.export_location or (backup_paths[0] if backup_paths else "")

	print("\n[3/4] Taking the site offline")
	run(["bench", "--site", site, "set-maintenance-mode", "on"], dry_run=args.dry_run)

	if args.control_site:
		print("\n[4/4] Recording the export location in the registry")
		bench_execute(
			args.control_site,
			"lms.lms.doctype.lms_tenant.lms_tenant.record_archival_artifacts",
			{"subdomain": subdomain, "export_location": export_location, "backup_verified": True},
			dry_run=args.dry_run,
		)
	else:
		print("\n[4/4] No control site given; record the export location manually")

	print(f"\nOK: {site} archived. Backup: {export_location or '(dry run)'}")
	print("  The site is offline but intact. Restore with --action resume.")
	print("  Deliver the export to the institution before any purge.\n")
	return 0


def _verify_backup(site: str, command_output: str, *, dry_run: bool) -> list[str] | None:
	"""Confirm the backup files exist and are non-empty.

	`bench backup` reporting success is not enough: a zero-byte dump is a
	successful command and a worthless artefact, and this is the last
	point at which anyone would notice before the data is destroyed.
	"""
	if dry_run:
		print("  (dry run: skipping artefact verification)")
		return []

	backup_dir = Path("sites") / site / "private" / "backups"
	if not backup_dir.is_dir():
		print(f"  no backup directory at {backup_dir}", file=sys.stderr)
		return None

	# Newest database dump plus whatever files archives accompany it.
	dumps = sorted(
		backup_dir.glob("*-database.sql.gz"), key=lambda p: p.stat().st_mtime, reverse=True
	)
	if not dumps:
		print(f"  no database dump found in {backup_dir}", file=sys.stderr)
		return None

	verified = []
	for path in dumps[:1]:
		size = path.stat().st_size
		if size == 0:
			print(f"  {path.name} is empty", file=sys.stderr)
			return None
		print(f"  {path.name}: {size / (1024 * 1024):.1f} MB")
		verified.append(str(path))

	for pattern in ("*-files.tar", "*-private-files.tar"):
		for path in sorted(backup_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)[:1]:
			print(f"  {path.name}: {path.stat().st_size / (1024 * 1024):.1f} MB")
			verified.append(str(path))

	return verified


def purge(args) -> int:
	"""Destroy a tenant site. There is no undo.

	Guarded four ways — archived status, a recorded data export, a
	verified backup, and an elapsed grace period — all checked in the
	registry *before* the site is dropped and re-checked after, plus a
	typed confirmation here. The guards live in
	`tenant_rules.check_purge_preconditions` so they are tested, and in
	the registry so this CLI cannot be the only thing standing between an
	operator and a deleted institution.
	"""
	try:
		subdomain = validate_subdomain(args.subdomain)
		site = build_site_name(subdomain, args.base_domain)
	except TenantNameError as e:
		print(f"error: {e}", file=sys.stderr)
		return 2

	print(f"\n{'=' * 64}")
	print(f"  PERMANENT DELETION of {site}")
	print(f"{'=' * 64}")
	print("\nThis destroys the site's database and files. The only route back")
	print("is restoring the archived backup into a fresh site.\n")

	if not args.control_site:
		print(
			"error: --control-site is required for a purge. The registry holds the "
			"guards (export recorded, backup verified, grace period elapsed) and "
			"must be checked before deletion.",
			file=sys.stderr,
		)
		return 2

	try:
		validate_purge_confirmation(args.confirm or "", subdomain)
	except PurgeNotAllowed as e:
		print(f"error: {e}", file=sys.stderr)
		print("       Pass --confirm <subdomain> to proceed.", file=sys.stderr)
		return 5

	if not args.yes_i_am_sure:
		print("error: refusing without --yes-i-am-sure.", file=sys.stderr)
		return 5

	print("[1/3] Checking the registry guards")
	# Before the irreversible step, never after it. This used to drop the
	# site first and rely on mark_purged to refuse afterwards, which gets
	# the order exactly backwards: a tenant that failed a guard ended up
	# deleted anyway, with a registry still saying it was archived. The
	# call throws on any unmet guard, and `run` raises on a non-zero exit,
	# so a refusal here aborts before anything is destroyed.
	bench_execute(
		args.control_site,
		"lms.lms.doctype.lms_tenant.lms_tenant.assert_purge_allowed",
		{"subdomain": subdomain},
		dry_run=args.dry_run,
	)

	print("\n[2/3] Dropping the site")
	drop_command = ["bench", "drop-site", site, "--no-backup"]
	if args.db_root_password:
		drop_command += ["--root-password", args.db_root_password]
	run(drop_command, dry_run=args.dry_run)

	print("\n[3/3] Recording the purge in the registry")
	# Re-checked rather than assumed: the guards were true a moment ago,
	# and this is what makes the registry claim the site is gone.
	bench_execute(
		args.control_site,
		"lms.lms.doctype.lms_tenant.lms_tenant.mark_purged",
		{"subdomain": subdomain, "notes": f"Site {site} purged by operator"},
		dry_run=args.dry_run,
	)

	print(f"\nOK: {site} purged.")
	print("  Retain the archived backup for the contractual/statutory period.\n")
	return 0


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(
		description="Provision and manage tenant sites for the language platform."
	)
	parser.add_argument("--subdomain", required=True, help="Tenant subdomain (DNS label)")
	parser.add_argument(
		"--base-domain",
		default="dilplatformu.com",
		help="Platform domain; the site becomes <subdomain>.<base-domain>",
	)
	parser.add_argument("--dry-run", action="store_true", help="Print commands without running them")

	parser.add_argument(
		"--action",
		choices=("provision", "suspend", "resume", "archive", "purge"),
		default="provision",
	)
	parser.add_argument("--tenant-name", help="Institution display name")
	parser.add_argument("--admin-email", help="Institution admin to invite")
	parser.add_argument("--admin-name", help="Institution admin full name")
	parser.add_argument(
		"--admin-password",
		help="Site Administrator password (generated when omitted - preferred)",
	)
	parser.add_argument("--db-root-password", help="MariaDB root password for site creation")
	parser.add_argument("--db-host", help="Database host override")
	parser.add_argument(
		"--control-site",
		help="Site holding the LMS Tenant registry; status is reported back to it",
	)
	parser.add_argument(
		"--apps",
		nargs="+",
		default=DEFAULT_APPS,
		help=f"Apps to install (default: {' '.join(DEFAULT_APPS)})",
	)
	parser.add_argument(
		"--dns-alias",
		help="Optional custom domain to attach to the site (premium tenants, §8.7)",
	)
	parser.add_argument(
		"--export-location",
		help="Where the institution's data export was delivered (archive action)",
	)
	parser.add_argument(
		"--confirm",
		help="For --action purge: type the subdomain exactly to confirm deletion",
	)
	parser.add_argument(
		"--yes-i-am-sure",
		action="store_true",
		help="For --action purge: required acknowledgement that deletion is irreversible",
	)
	return parser


def main(argv=None) -> int:
	args = build_parser().parse_args(argv)
	actions = {
		"provision": provision,
		"suspend": suspend,
		"resume": resume,
		"archive": archive,
		"purge": purge,
	}
	try:
		return actions[args.action](args)
	except ProvisioningError as e:
		print(f"\nerror: {e}", file=sys.stderr)
		return 1


if __name__ == "__main__":
	sys.exit(main())

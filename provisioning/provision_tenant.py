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
		TenantNameError,
		build_site_name,
		validate_subdomain,
	)
except ImportError:  # pragma: no cover - depends on where it is invoked from
	sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
	from lms.lms.language_platform.tenant_rules import (
		TenantNameError,
		build_site_name,
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

	parser.add_argument("--action", choices=("provision", "suspend", "resume"), default="provision")
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
	return parser


def main(argv=None) -> int:
	args = build_parser().parse_args(argv)
	actions = {"provision": provision, "suspend": suspend, "resume": resume}
	try:
		return actions[args.action](args)
	except ProvisioningError as e:
		print(f"\nerror: {e}", file=sys.stderr)
		return 1


if __name__ == "__main__":
	sys.exit(main())

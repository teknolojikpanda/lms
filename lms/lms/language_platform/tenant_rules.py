# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Tenant provisioning *policy*: naming, capacity and roster validation.

Tenant = Frappe site (ADR-0001), so a tenant's subdomain becomes a real
DNS label, a real site directory and — critically — an argument to
``bench new-site``. Everything that reaches a shell is validated here
against an allowlist pattern rather than sanitised after the fact:
rejecting anything that is not already a valid DNS label makes shell
metacharacters, path traversal and argument injection unrepresentable.

No ``frappe`` imports, so the validation that guards a subprocess call is
unit-testable without a site.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

# --- Subdomain / site naming -------------------------------------------------

# RFC 1123 DNS label: lowercase alphanumeric plus hyphen, no leading or
# trailing hyphen, 1-63 chars. Deliberately an allowlist.
SUBDOMAIN_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

MIN_SUBDOMAIN_LENGTH = 3
MAX_SUBDOMAIN_LENGTH = 63

# Names that must never become a tenant: they collide with the platform's
# own hosts (§8.7), with common infrastructure records, or with routes the
# app already serves.
RESERVED_SUBDOMAINS = frozenset(
	{
		"admin",
		"api",
		"app",
		"assets",
		"autodiscover",
		"cdn",
		"dashboard",
		"dev",
		"files",
		"ftp",
		"imap",
		"landing",
		"localhost",
		"mail",
		"mx",
		"ns",
		"ns1",
		"ns2",
		"owner",
		"pop",
		"private",
		"prod",
		"smtp",
		"socketio",
		"staging",
		"static",
		"status",
		"support",
		"teacher",
		"test",
		"vod",
		"web",
		"webmail",
		"www",
	}
)


class TenantNameError(ValueError):
	"""Raised when a subdomain cannot be used for a tenant site."""


def validate_subdomain(subdomain: str) -> str:
	"""Return the normalised subdomain, or raise ``TenantNameError``.

	Normalisation is limited to case and surrounding whitespace — anything
	else that does not already match the DNS label pattern is rejected
	rather than repaired, because a value that needs repairing is a value
	that should never have reached provisioning.
	"""
	if not isinstance(subdomain, str):
		raise TenantNameError("Subdomain must be a string.")

	candidate = subdomain.strip().lower()

	if not candidate:
		raise TenantNameError("Subdomain is required.")
	if len(candidate) < MIN_SUBDOMAIN_LENGTH:
		raise TenantNameError(
			f"Subdomain must be at least {MIN_SUBDOMAIN_LENGTH} characters."
		)
	if len(candidate) > MAX_SUBDOMAIN_LENGTH:
		raise TenantNameError(f"Subdomain must be at most {MAX_SUBDOMAIN_LENGTH} characters.")
	if not SUBDOMAIN_PATTERN.match(candidate):
		raise TenantNameError(
			"Subdomain may contain only lowercase letters, digits and hyphens, "
			"and cannot start or end with a hyphen."
		)
	# "xn--" is the punycode prefix; allowing it lets a tenant claim a name
	# that renders as somebody else's brand in a browser.
	if candidate.startswith("xn--"):
		raise TenantNameError("Punycode subdomains are not allowed.")
	if candidate in RESERVED_SUBDOMAINS:
		raise TenantNameError(f"'{candidate}' is reserved by the platform.")

	return candidate


def build_site_name(subdomain: str, base_domain: str) -> str:
	"""Compose the Frappe site name (= the tenant's hostname, §3.2)."""
	subdomain = validate_subdomain(subdomain)
	base = (base_domain or "").strip().lower().strip(".")
	if not base:
		raise TenantNameError("Base domain is required.")
	if not all(SUBDOMAIN_PATTERN.match(label) for label in base.split(".") if label):
		raise TenantNameError(f"Invalid base domain: {base_domain}")
	return f"{subdomain}.{base}"


# --- Capacity ---------------------------------------------------------------


def seats_available(seat_limit: int, active_students: int) -> int:
	"""Remaining seats; 0 seat_limit means unlimited (returns -1 as 'no cap')."""
	if not seat_limit or seat_limit <= 0:
		return -1
	return max(0, int(seat_limit) - int(active_students or 0))


def check_seat_capacity(seat_limit: int, active_students: int, adding: int) -> None:
	"""Raise when an import would exceed the plan's seat limit."""
	available = seats_available(seat_limit, active_students)
	if available == -1:
		return
	if adding > available:
		raise ValueError(
			f"Seat limit exceeded: {available} seat(s) available, {adding} requested. "
			"Increase the plan's seat limit before importing."
		)


# --- Archival and offboarding ---------------------------------------------------
#
# Offboarding separates two things that are usually conflated:
#
#   Archived — the site is backed up and offline, and can be brought back.
#   Purged   — the site is gone. There is no undo beyond restoring the
#              backup into a fresh site, which is a different operation
#              with a different runbook.
#
# The grace period between them exists because offboarding decisions get
# reversed: contracts get renewed, the wrong tenant gets named, a bursar
# resolves a payment dispute. Deleting on the day of the request removes
# the chance to notice.

DEFAULT_GRACE_DAYS = 90


class PurgeNotAllowed(Exception):
	"""Raised when a purge is attempted before its guards are satisfied."""


def purge_after_date(archived_at: datetime, grace_days: int = DEFAULT_GRACE_DAYS) -> datetime:
	"""Earliest moment a tenant may be purged."""
	if not isinstance(archived_at, datetime):
		raise ValueError("archived_at must be a datetime.")
	return archived_at + timedelta(days=max(0, int(grace_days)))


def purge_eligibility(
	archived_at: datetime | None,
	grace_days: int = DEFAULT_GRACE_DAYS,
	now: datetime | None = None,
) -> dict:
	"""Report whether the grace period has elapsed, and how much remains.

	Returns a dict rather than a bool so callers can *show* the operator
	how long is left instead of only refusing.
	"""
	if archived_at is None:
		return {"eligible": False, "days_remaining": None, "reason": "Tenant is not archived."}

	now = now or datetime.now()
	purge_at = purge_after_date(archived_at, grace_days)
	remaining = (purge_at - now).total_seconds() / 86400

	if remaining > 0:
		return {
			"eligible": False,
			"days_remaining": max(1, int(remaining + 0.999)),  # round up: partial day still waits
			"purge_after": purge_at,
			"reason": f"Grace period ends {purge_at:%Y-%m-%d}.",
		}

	return {"eligible": True, "days_remaining": 0, "purge_after": purge_at, "reason": None}


def validate_purge_confirmation(typed_value: str, subdomain: str) -> None:
	"""Require the operator to type the subdomain exactly.

	Copied from how hosting providers gate repository deletion: the point
	is not authentication (that already happened) but interrupting
	autopilot. Someone who types the name has read which tenant they are
	about to destroy.
	"""
	if not isinstance(typed_value, str) or typed_value.strip() != (subdomain or "").strip():
		raise PurgeNotAllowed(
			f"Confirmation does not match. Type the subdomain exactly: {subdomain}"
		)


def check_purge_preconditions(
	status: str,
	archived_at: datetime | None,
	backup_verified: bool,
	export_location: str | None,
	grace_days: int = DEFAULT_GRACE_DAYS,
	now: datetime | None = None,
) -> None:
	"""Every guard that must hold before a site may be destroyed.

	Collected in one pure function so the rules are testable and so no
	caller can satisfy three of the four and proceed.
	"""
	if status != "Archived":
		raise PurgeNotAllowed(
			f"Only archived tenants can be purged; this one is '{status}'. Archive it first."
		)

	if not export_location:
		raise PurgeNotAllowed(
			"No data export recorded. The institution's data must be returned before deletion."
		)

	if not backup_verified:
		raise PurgeNotAllowed(
			"Backup has not been verified. Purging without a checked backup makes the "
			"deletion unrecoverable."
		)

	eligibility = purge_eligibility(archived_at, grace_days, now)
	if not eligibility["eligible"]:
		raise PurgeNotAllowed(eligibility["reason"])


# --- Roster import ------------------------------------------------------------

ROSTER_REQUIRED_COLUMNS = ("email", "first_name")
ROSTER_OPTIONAL_COLUMNS = ("last_name", "class_name", "student_id", "level")

# Intentionally permissive: this rejects obvious junk, and the identity
# provider remains the authority on deliverability.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

VALID_LEVELS = frozenset({"A1", "A2", "B1", "B2", "C1", "C2"})


def normalize_roster_row(row: dict) -> dict:
	"""Trim and lowercase the fields that need it, keeping names as typed."""
	normalized = {}
	for key, value in (row or {}).items():
		if key is None:
			continue
		key = str(key).strip().lower().replace(" ", "_")
		value = "" if value is None else str(value).strip()
		normalized[key] = value

	if normalized.get("email"):
		normalized["email"] = normalized["email"].lower()
	if normalized.get("level"):
		normalized["level"] = normalized["level"].upper()
	return normalized


def validate_roster_row(row: dict) -> list[str]:
	"""Return a list of human-readable problems with a single roster row."""
	errors = []

	for column in ROSTER_REQUIRED_COLUMNS:
		if not row.get(column):
			errors.append(f"'{column}' is required")

	email = row.get("email", "")
	if email and not EMAIL_PATTERN.match(email):
		errors.append(f"'{email}' is not a valid email address")

	level = row.get("level", "")
	if level and level not in VALID_LEVELS:
		errors.append(f"'{level}' is not a CEFR level (A1-C2)")

	return errors


def parse_roster(rows: list[dict]) -> tuple[list[dict], list[dict]]:
	"""Split a roster into importable rows and rejected rows.

	Duplicate emails *within the file* are rejected rather than silently
	deduplicated: a roster listing the same student twice usually means two
	different people were conflated, and importing one of them quietly
	would hide that.

	Returns ``(valid_rows, errors)`` where each error carries the 1-based
	row number so an admin can find it in their spreadsheet.
	"""
	valid: list[dict] = []
	errors: list[dict] = []
	seen_emails: set[str] = set()

	for index, raw in enumerate(rows or [], start=1):
		row = normalize_roster_row(raw)
		problems = validate_roster_row(row)

		email = row.get("email", "")
		if email and email in seen_emails:
			problems.append(f"duplicate of an earlier row ('{email}')")
		elif email:
			seen_emails.add(email)

		if problems:
			errors.append({"row": index, "email": email, "errors": problems})
		else:
			valid.append(row)

	return valid, errors

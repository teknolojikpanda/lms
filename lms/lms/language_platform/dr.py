# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""DR readiness reporting (§6.1, §8.18).

§8.18 makes a periodic restore test a MUST and asks for at least one
drill a year. Both are the kind of obligation that quietly lapses, so
this records when they last happened and reports how close the platform
actually is to its stated RPO — turning two documentation requirements
into something the owner ops dashboard shows and an alarm can fire on.

The arithmetic lives in ``dr_rules``; this reads state and writes the
record of an exercise having been done.
"""

from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from lms.lms.language_platform.dr_rules import (
	DR_DRILL_INTERVAL_DAYS,
	RESTORE_TEST_INTERVAL_DAYS,
	RPO_TARGET_MINUTES,
	RTO_TARGET_HOURS,
	evaluate_overdue,
	evaluate_rpo,
	overall_status,
	rto_budget,
)


def _settings():
	return frappe.get_cached_doc("LMS Language Settings")


BACKUP_DUMP_GLOB = "*-database.sql.gz"
GZIP_MAGIC = b"\x1f\x8b"
# A 10-byte header and an 8-byte trailer: nothing smaller is a gzip file
# at all, let alone a database dump.
GZIP_MINIMUM_BYTES = 18


def _is_plausible_dump(path, size):
	"""Cheap structural checks on a dump. Not a restorability guarantee.

	A zero-byte or stub file is a failed `bench backup` and a worthless
	artefact — and a failed backup is precisely when one appears, with a
	fresh mtime. Counting it would report the platform safe at the moment
	its backups stopped working, and would let a truncated fresh file
	mask an older dump that is actually restorable.
	`provision_tenant._verify_backup` refuses these before a purge.

	What this deliberately does not do is prove the dump restores. A gzip
	truncated mid-stream cannot be told from a complete one without
	decompressing it, and decompressing a multi-gigabyte dump on every
	dashboard poll is not an option. Two cases stay undetected: a backup
	still being written (frappe streams `mysqldump | gzip` straight to the
	final filename, so it is visible while in flight) and one killed
	outright — an ordinary failure is cleaned up by frappe itself, but
	SIGKILL or power loss is not. Both resolve at the next backup.

	Proving a backup restores is what the §8.18 restore test exists for;
	this measures freshness. See `record_restore_test`.
	"""
	if size < GZIP_MINIMUM_BYTES:
		return False
	with open(path, "rb") as handle:
		return handle.read(len(GZIP_MAGIC)) == GZIP_MAGIC


def _backup_directory():
	"""Where this site's dumps land, honouring a relocated ``backup_path``.

	Asking frappe rather than hardcoding ``private/backups``: a site that
	moves its backups via site config would otherwise look unbacked-up,
	which is the same class of mistake as reading File rows.
	"""
	from frappe.utils.backups import get_backup_path

	return Path(get_backup_path())


def _latest_backup_on_disk():
	"""Newest database dump `bench backup` actually wrote.

	This is where backups live. `bench backup` writes to the site's backup
	directory and creates no File document, which is why
	`provisioning/provision_tenant._verify_backup` looks there too.
	Reading File rows instead found nothing on a correctly backed-up site
	and reported the RPO as unknown — a panel that says "no idea" no
	matter how healthy the platform is tells an operator nothing, and
	trains them to ignore it.
	"""
	try:
		dumps = sorted(
			_backup_directory().glob(BACKUP_DUMP_GLOB),
			key=lambda path: path.stat().st_mtime,
			reverse=True,
		)
		for path in dumps:
			stat = path.stat()
			if not _is_plausible_dump(path, stat.st_size):
				continue
			# Age is elapsed seconds, then expressed against the same naive
			# site-local clock the evaluator reads. Converting the mtime to
			# a local wall-clock time instead would leave `evaluate_rpo`
			# subtracting two ambiguous timestamps across a DST transition:
			# at a fall-back, a 100-minute-old backup reads as 40 minutes
			# old and hides a breach. Elapsed time has no such ambiguity.
			# max(..., 0) keeps a clock-skewed future mtime from reading as
			# a backup taken later than now.
			elapsed = max(time.time() - stat.st_mtime, 0)
			return now_datetime() - timedelta(seconds=elapsed)
		return None
	except Exception:
		# Broad on purpose: this feeds a dashboard panel and a scheduled
		# check, and neither should fail because the backup directory is
		# unreadable. It reads as unknown, which is the honest answer —
		# but an unreadable directory and an empty one are very different
		# problems, and only the log can tell them apart. Not an Error
		# Log: this runs on every dashboard poll.
		frappe.logger("lms.dr").warning("could not read the backup directory", exc_info=True)
		return None


def _latest_backup_file_doc():
	"""A dump recorded as a File, for deployments that attach them."""
	try:
		row = frappe.db.get_value(
			"File",
			{"file_name": ["like", "%-database.sql.gz"]},
			["creation"],
			order_by="creation desc",
		)
		return get_datetime(row) if row else None
	except Exception:
		frappe.logger("lms.dr").warning("could not query File rows for backups", exc_info=True)
		return None


def _last_backup_time():
	"""Most recent backup this site can see, from whichever source has one.

	Returns None when neither has anything — reported as unknown, never as
	healthy. Note that a snapshot-based strategy (AWS Backup, RDS
	automated backups) writes neither a file here nor a File row, so a
	platform relying solely on those reads as unknown. That is honest
	rather than wrong: this process genuinely cannot see them, and saying
	so is better than reporting a freshness it did not measure.
	"""
	candidates = [_latest_backup_on_disk(), _latest_backup_file_doc()]
	found = [stamp for stamp in candidates if stamp]
	return max(found) if found else None


@frappe.whitelist()
def get_dr_readiness() -> dict:
	"""DR posture for the owner ops dashboard (§8.15 SLO panel)."""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only platform owners can view DR readiness."), frappe.PermissionError)

	settings = _settings()
	now = now_datetime()

	rpo = evaluate_rpo(
		_last_backup_time(),
		now,
		target_minutes=settings.rpo_target_minutes or RPO_TARGET_MINUTES,
	)
	rpo["check"] = "RPO (backup freshness)"

	restore_test = evaluate_overdue(
		get_datetime(settings.last_restore_test_at) if settings.last_restore_test_at else None,
		settings.restore_test_interval_days or RESTORE_TEST_INTERVAL_DAYS,
		"Restore test",
		now,
	)
	restore_test["check"] = "Restore test (§8.18 MUST)"

	drill = evaluate_overdue(
		get_datetime(settings.last_dr_drill_at) if settings.last_dr_drill_at else None,
		DR_DRILL_INTERVAL_DAYS,
		"DR drill",
		now,
	)
	drill["check"] = "DR drill (annual)"

	checks = [rpo, restore_test, drill]

	return {
		"status": overall_status(checks),
		"checks": checks,
		"targets": {
			"rpo_minutes": settings.rpo_target_minutes or RPO_TARGET_MINUTES,
			"rto_hours": settings.rto_target_hours or RTO_TARGET_HOURS,
			"dr_region": settings.dr_region or None,
		},
		"rto_plan": rto_budget(settings.rto_target_hours or RTO_TARGET_HOURS),
		"replication_configured": bool(settings.dr_region),
	}


@frappe.whitelist()
def record_restore_test(outcome: str, notes: str | None = None) -> dict:
	"""Record that a backup was actually restored and verified.

	Deliberately requires an outcome rather than defaulting to success: a
	restore test that failed is the single most valuable thing this
	record can contain, and a one-click "done" button would quietly lose
	it. A failed test still resets nothing — the clock only moves on a
	pass.
	"""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only platform owners can record restore tests."), frappe.PermissionError)

	outcome = (outcome or "").strip().lower()
	if outcome not in ("pass", "fail"):
		frappe.throw(_("Outcome must be 'pass' or 'fail'."))

	settings = frappe.get_single("LMS Language Settings")
	if outcome == "pass":
		settings.last_restore_test_at = now_datetime()
		settings.save(ignore_permissions=True)

	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "LMS Language Settings",
			"reference_name": "LMS Language Settings",
			"content": _("Restore test {0} by {1}. {2}").format(
				outcome.upper(), frappe.session.user, notes or ""
			),
		}
	).insert(ignore_permissions=True)

	frappe.logger("lms.dr").info(
		"Restore test %s by %s: %s", outcome, frappe.session.user, notes or ""
	)

	return {
		"outcome": outcome,
		"recorded_at": str(now_datetime()),
		"clock_reset": outcome == "pass",
	}


@frappe.whitelist()
def record_dr_drill(notes: str | None = None) -> dict:
	"""Record a completed DR drill (§8.18: at least one a year)."""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only platform owners can record DR drills."), frappe.PermissionError)

	if not (notes or "").strip():
		# A drill with no notes teaches nothing at the next one.
		frappe.throw(_("Drill notes are required: what was exercised, and what broke."))

	settings = frappe.get_single("LMS Language Settings")
	settings.last_dr_drill_at = now_datetime()
	settings.save(ignore_permissions=True)

	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "LMS Language Settings",
			"reference_name": "LMS Language Settings",
			"content": _("DR drill completed by {0}. {1}").format(frappe.session.user, notes.strip()),
		}
	).insert(ignore_permissions=True)

	return {"recorded_at": str(now_datetime())}


def check_dr_readiness_daily():
	"""Scheduled check that logs an error when a DR obligation lapses.

	Logged as an Error Log rather than merely displayed, so it reaches the
	same alerting path as everything else operational (§8.15) instead of
	waiting for somebody to open a dashboard.
	"""
	try:
		settings = _settings()
		now = now_datetime()

		checks = [
			evaluate_rpo(
				_last_backup_time(), now, target_minutes=settings.rpo_target_minutes or RPO_TARGET_MINUTES
			),
			evaluate_overdue(
				get_datetime(settings.last_restore_test_at) if settings.last_restore_test_at else None,
				settings.restore_test_interval_days or RESTORE_TEST_INTERVAL_DAYS,
				"Restore test",
				now,
			),
			evaluate_overdue(
				get_datetime(settings.last_dr_drill_at) if settings.last_dr_drill_at else None,
				DR_DRILL_INTERVAL_DAYS,
				"DR drill",
				now,
			),
		]

		breaches = [c for c in checks if c["status"] in ("breach", "unknown")]
		if breaches:
			frappe.log_error(
				"\n".join(c["message"] for c in breaches),
				"DR readiness breach",
			)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "DR readiness check failed")

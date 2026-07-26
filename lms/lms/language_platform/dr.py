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


def _last_backup_time():
	"""Most recent site backup Frappe knows about.

	Reads the backup the bench actually produced rather than a value
	someone typed in, so the RPO figure reflects reality. Returns None
	when nothing is discoverable — reported as unknown, never as healthy.
	"""
	try:
		row = frappe.db.get_value(
			"File",
			{"file_name": ["like", "%-database.sql.gz"]},
			["creation"],
			order_by="creation desc",
		)
		return get_datetime(row) if row else None
	except Exception:
		return None


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

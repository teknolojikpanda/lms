# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Disaster recovery policy (§6.1 RPO/RTO, §8.18 backup and continuity).

The agreement states targets — RPO 15 minutes to 1 hour, RTO 1 to 4
hours — and requires a *periodic restore test* as a MUST plus at least
one drill a year. Targets nobody measures are aspirations, and a backup
nobody has restored is a hypothesis, so these rules turn both into
something a dashboard can show and an alarm can fire on.

No ``frappe`` imports, so the compliance arithmetic is testable without
a site.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# §6.1 targets.
RPO_TARGET_MINUTES = 60  # worst acceptable data loss (the 1-hour end)
RPO_IDEAL_MINUTES = 15  # the 15-minute end of the stated range
RTO_TARGET_HOURS = 4  # worst acceptable time to restore service

# §8.18: "periyodik restore testi (MUST)" and "yilda en az 1 tatbikat".
# A restore test proves the backup is usable; a drill proves the *people*
# and the runbook are. They are different exercises on different clocks.
RESTORE_TEST_INTERVAL_DAYS = 30
DR_DRILL_INTERVAL_DAYS = 365

STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_BREACH = "breach"
STATUS_UNKNOWN = "unknown"


class DRError(ValueError):
	"""Raised for an unusable DR evaluation request."""


def evaluate_rpo(
	last_backup_at: datetime | None,
	now: datetime | None = None,
	target_minutes: int = RPO_TARGET_MINUTES,
	ideal_minutes: int = RPO_IDEAL_MINUTES,
) -> dict:
	"""Compare backup age against the RPO target.

	``None`` is reported as *unknown*, never as ok. A missing backup
	timestamp usually means the reporting path broke — and silently
	showing green while backups have stopped is the failure this check
	exists to prevent.
	"""
	if last_backup_at is None:
		return {
			"status": STATUS_UNKNOWN,
			"age_minutes": None,
			"message": "No backup timestamp available — verify the backup job is running.",
		}

	now = now or datetime.now()
	age_minutes = (now - last_backup_at).total_seconds() / 60

	if age_minutes < 0:
		# A future timestamp means clock skew somewhere; treat as unknown
		# rather than as an impossibly fresh backup.
		return {
			"status": STATUS_UNKNOWN,
			"age_minutes": round(age_minutes, 1),
			"message": "Backup timestamp is in the future — check clock synchronisation.",
		}

	if age_minutes <= ideal_minutes:
		status = STATUS_OK
		message = f"Backup is {int(age_minutes)} min old, within the {ideal_minutes} min target."
	elif age_minutes <= target_minutes:
		# Inside the agreement's range but past its better end: worth
		# showing, not worth waking anyone.
		status = STATUS_WARNING
		message = (
			f"Backup is {int(age_minutes)} min old — inside the {target_minutes} min RPO "
			f"but past the {ideal_minutes} min goal."
		)
	else:
		status = STATUS_BREACH
		message = f"Backup is {int(age_minutes)} min old, exceeding the {target_minutes} min RPO."

	return {"status": status, "age_minutes": round(age_minutes, 1), "message": message}


def evaluate_overdue(
	last_performed_at: datetime | None,
	interval_days: int,
	label: str,
	now: datetime | None = None,
) -> dict:
	"""Shared clock for the restore test and the annual drill."""
	if interval_days <= 0:
		raise DRError("interval_days must be positive.")

	if last_performed_at is None:
		return {
			"status": STATUS_BREACH,
			"days_since": None,
			"due_in_days": None,
			"message": f"{label} has never been recorded.",
		}

	now = now or datetime.now()
	days_since = (now - last_performed_at).total_seconds() / 86400
	due_in = interval_days - days_since

	if due_in >= interval_days * 0.2:
		status = STATUS_OK
	elif due_in >= 0:
		status = STATUS_WARNING
	else:
		status = STATUS_BREACH

	return {
		"status": status,
		"days_since": int(days_since),
		"due_in_days": int(due_in),
		"message": (
			f"{label} last performed {int(days_since)} days ago; "
			+ (f"due in {int(due_in)} days." if due_in >= 0 else f"overdue by {abs(int(due_in))} days.")
		),
	}


def rto_budget(target_hours: int = RTO_TARGET_HOURS) -> list[dict]:
	"""Break the RTO target into the phases a failover actually spends time in.

	A single "4 hours" number tells an operator nothing on the day. The
	split is what makes it possible to notice, mid-incident, that you are
	already over budget on bootstrap and should stop optimising restore.
	"""
	if target_hours <= 0:
		raise DRError("target_hours must be positive.")

	total_minutes = target_hours * 60
	phases = [
		("Decide and declare", 0.10, "Confirm the region is genuinely lost, not a transient fault."),
		("Bootstrap the DR stack", 0.30, "Terraform apply in the DR region; pilot light has no running compute."),
		("Restore data", 0.35, "Latest cross-region snapshot; S3 content is already replicated."),
		("Switch DNS and verify", 0.25, "Repoint, warm caches, smoke-test login/exam/video."),
	]

	return [
		{
			"phase": name,
			"budget_minutes": int(total_minutes * share),
			"note": note,
		}
		for name, share, note in phases
	]


def overall_status(checks: list[dict]) -> str:
	"""Worst status wins.

	Averaging or majority-voting DR checks would let a breached RPO hide
	behind three green rows.
	"""
	statuses = {check.get("status") for check in checks or []}
	for status in (STATUS_BREACH, STATUS_UNKNOWN, STATUS_WARNING):
		if status in statuses:
			return status
	return STATUS_OK if statuses else STATUS_UNKNOWN

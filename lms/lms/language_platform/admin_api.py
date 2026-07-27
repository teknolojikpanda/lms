# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Aggregations for the institution (Ek-4.4) and owner (Ek-4.3) dashboards.

Tenant = Frappe site (ADR-0001), so the "institution dashboard" reports on
this site's data and the "owner dashboard" reports deployment-level usage,
operational signals and an application-metered cost estimate. The
CUR/Athena-based cost panel (Ek-6.6) replaces the estimate once the FinOps
infrastructure lands; until then estimates use the example unit prices of
Ek-6.3 and are labelled as such in the UI.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, getdate, now_datetime

from lms.lms.utils import has_moderator_role

# ---------------------------------------------------------------------------
# Ek-6.3 example unit prices (USD). Estimates only — see module docstring.
# ---------------------------------------------------------------------------
CLOUDFRONT_USD_PER_GB = 0.085
CLOUDFRONT_USD_PER_10K_REQUESTS = 0.012
TRANSCRIBE_USD_PER_MINUTE = 0.024
BEDROCK_USD_PER_1K_TOKENS = 0.00018
AVERAGE_BITRATE_MBPS = 2.0
GB_PER_MINUTE_PER_MBPS = 0.006985  # Ek-6.1 calculation hint
HLS_REQUESTS_PER_MINUTE = 15
BEDROCK_TOKENS_PER_ATTEMPT = 1600  # ~1200 input + ~400 output (Ek-6.1)

RISKY_INACTIVE_DAYS = 14
RISKY_SCORE_THRESHOLD = 50


def _require_moderator():
	if not has_moderator_role():
		frappe.throw(_("Only institution admins can access this dashboard."), frappe.PermissionError)


def _require_system_manager():
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only platform owners can access this dashboard."), frappe.PermissionError)


def _count_enabled_users_with_role(role: str) -> int:
	return frappe.db.sql(
		"""
		SELECT COUNT(DISTINCT hr.parent)
		FROM `tabHas Role` hr
		JOIN `tabUser` u ON u.name = hr.parent
		WHERE hr.role = %s AND u.enabled = 1
			AND u.name NOT IN ('Administrator', 'Guest')
		""",
		role,
	)[0][0]


def _daily_counts(doctype: str, days: int, value_expr: str = "COUNT(*)", filters: str = "") -> list[dict]:
	"""Per-day series for the last ``days`` days based on ``creation``."""
	since = add_days(getdate(), -days)
	rows = frappe.db.sql(
		f"""
		SELECT DATE(creation) AS date, {value_expr} AS value
		FROM `tab{doctype}`
		WHERE DATE(creation) >= %s {filters}
		GROUP BY DATE(creation)
		ORDER BY date
		""",
		since,
		as_dict=True,
	)
	by_date = {str(row.date): float(row.value or 0) for row in rows}
	series = []
	for offset in range(days, -1, -1):
		day = str(add_days(getdate(), -offset))
		series.append({"date": day, "value": by_date.get(day, 0)})
	return series


# ---------------------------------------------------------------------------
# Institution dashboard (Ek-4.4)
# ---------------------------------------------------------------------------


def get_admin_dashboard() -> dict:
	_require_moderator()
	return {
		"kpis": _admin_kpis(),
		"placement_distribution": _placement_distribution(),
		"activity": {
			"lesson_progress": _daily_counts("LMS Course Progress", 14),
			"quiz_submissions": _daily_counts("LMS Quiz Submission", 14),
		},
		"risky_students": _risky_students(),
		"pending_grading": _pending_grading(),
	}


def _admin_kpis() -> dict:
	return {
		"active_students": _count_enabled_users_with_role("LMS Student"),
		"active_teachers": _count_enabled_users_with_role("Course Creator"),
		"batches": frappe.db.count("LMS Batch"),
		"published_courses": frappe.db.count("LMS Course", {"published": 1}),
		"enrollments": frappe.db.count("LMS Enrollment"),
	}


def _placement_distribution() -> list[dict]:
	rows = frappe.db.sql(
		"""
		SELECT COALESCE(NULLIF(override_level, ''), result_level) AS level,
			COUNT(*) AS value
		FROM `tabLMS Placement Attempt`
		WHERE status = 'Completed'
			AND COALESCE(NULLIF(override_level, ''), result_level) IS NOT NULL
		GROUP BY level
		""",
		as_dict=True,
	)
	order = {"A1": 0, "A2": 1, "B1": 2, "B2": 3, "C1": 4, "C2": 5}
	return sorted(rows, key=lambda row: order.get(row.level, 99))


def _risky_students() -> list[dict]:
	"""§4.8 heuristic: low recent activity + low quiz scores."""
	cutoff = add_days(now_datetime(), -RISKY_INACTIVE_DAYS)

	# The aggregates are computed in correlated subqueries and filtered in
	# an outer WHERE rather than a HAVING over joined tables. Two reasons,
	# both found by running this against MariaDB:
	#
	#   * MariaDB rejects a column alias that refers to a group function
	#     inside HAVING (error 1247), so `HAVING avg_score < ...` fails.
	#   * Joining progress AND quiz rows in one query multiplies them into
	#     a cartesian product per member — wasted work that grows with a
	#     student's history.
	rows = frappe.db.sql(
		"""
		SELECT * FROM (
			SELECT
				e.member,
				u.full_name,
				(
					SELECT MAX(p.creation)
					FROM `tabLMS Course Progress` p
					WHERE p.member = e.member
				) AS last_activity,
				(
					SELECT AVG(q.percentage)
					FROM `tabLMS Quiz Submission` q
					WHERE q.member = e.member
				) AS avg_score
			FROM `tabLMS Enrollment` e
			JOIN `tabUser` u ON u.name = e.member AND u.enabled = 1
			GROUP BY e.member, u.full_name
		) AS student
		WHERE (student.last_activity IS NULL OR student.last_activity < %s)
			OR (student.avg_score IS NOT NULL AND student.avg_score < %s)
		ORDER BY COALESCE(student.avg_score, 0) ASC, student.last_activity ASC
		LIMIT 10
		""",
		(cutoff, RISKY_SCORE_THRESHOLD),
		as_dict=True,
	)
	risky = []
	for row in rows:
		reasons = []
		if not row.last_activity or row.last_activity < cutoff:
			reasons.append(_("inactive for {0}+ days").format(RISKY_INACTIVE_DAYS))
		if row.avg_score is not None and row.avg_score < RISKY_SCORE_THRESHOLD:
			reasons.append(_("average quiz score {0}%").format(round(row.avg_score)))
		risky.append(
			{
				"member": row.member,
				"full_name": row.full_name,
				"last_activity": str(row.last_activity) if row.last_activity else None,
				"avg_score": round(row.avg_score, 1) if row.avg_score is not None else None,
				"reasons": reasons,
			}
		)
	return risky


def _pending_grading() -> dict:
	return {
		"speaking": frappe.db.count(
			"LMS Speaking Submission", {"status": "Ready", "is_overridden": 0}
		),
		"assignments": frappe.db.count("LMS Assignment Submission", {"status": "Not Graded"}),
	}


# ---------------------------------------------------------------------------
# Owner dashboard (Ek-4.3)
# ---------------------------------------------------------------------------


def get_owner_dashboard() -> dict:
	_require_system_manager()
	usage = _usage_totals()
	return {
		"kpis": usage,
		"trends": {
			"new_users": _daily_counts("User", 30, filters="AND name NOT IN ('Administrator','Guest')"),
			"watch_minutes": _daily_counts(
				"LMS Video Watch Duration",
				30,
				value_expr="COALESCE(SUM(CAST(watch_time AS DECIMAL(14,2))), 0) / 60",
			),
			"speaking_minutes": _daily_counts(
				"LMS Speaking Submission",
				30,
				value_expr="COALESCE(SUM(duration_seconds), 0) / 60",
			),
		},
		"ops": _ops_signals(),
		"cost_estimate": _cost_estimate(usage),
	}


def _usage_totals() -> dict:
	watch_seconds = (
		frappe.db.sql(
			"SELECT COALESCE(SUM(CAST(watch_time AS DECIMAL(14,2))), 0) FROM `tabLMS Video Watch Duration`"
		)[0][0]
		or 0
	)
	speaking_seconds = (
		frappe.db.sql("SELECT COALESCE(SUM(duration_seconds), 0) FROM `tabLMS Speaking Submission`")[0][0]
		or 0
	)
	storage_bytes = frappe.db.sql("SELECT COALESCE(SUM(file_size), 0) FROM `tabFile`")[0][0] or 0
	active_users_30d = frappe.db.count(
		"User",
		{
			"enabled": 1,
			"last_active": [">=", add_days(now_datetime(), -30)],
			"name": ["not in", ("Administrator", "Guest")],
		},
	)
	return {
		"total_users": frappe.db.count(
			"User", {"enabled": 1, "name": ["not in", ("Administrator", "Guest")]}
		),
		"active_users_30d": active_users_30d,
		"published_courses": frappe.db.count("LMS Course", {"published": 1}),
		"watch_minutes_total": round(float(watch_seconds) / 60, 1),
		"speaking_minutes_total": round(float(speaking_seconds) / 60, 1),
		"speaking_attempts_total": frappe.db.count("LMS Speaking Submission"),
		"storage_gb": round(float(storage_bytes) / (1024**3), 2),
	}


def _ops_signals() -> dict:
	return {
		"speaking_failed": frappe.db.count("LMS Speaking Submission", {"status": "Failed"}),
		"speaking_in_flight": frappe.db.count(
			"LMS Speaking Submission",
			{"status": ["in", ["Queued", "Transcribing", "Scoring"]]},
		),
		"pending_error_logs_24h": frappe.db.count(
			"Error Log", {"creation": [">=", add_days(now_datetime(), -1)]}
		),
	}


def _cost_estimate(usage: dict) -> dict:
	"""Application-metered estimate per Ek-6.3 example unit prices.

	Not a bill: the authoritative cost panel is CUR/Athena-based (Ek-6.6)
	and lands with the FinOps infrastructure increment.
	"""
	watch_minutes = usage["watch_minutes_total"]
	speaking_minutes = usage["speaking_minutes_total"]
	attempts = usage["speaking_attempts_total"]

	video_gb = watch_minutes * AVERAGE_BITRATE_MBPS * GB_PER_MINUTE_PER_MBPS
	cdn_transfer = video_gb * CLOUDFRONT_USD_PER_GB
	cdn_requests = watch_minutes * HLS_REQUESTS_PER_MINUTE / 10000 * CLOUDFRONT_USD_PER_10K_REQUESTS
	transcribe = speaking_minutes * TRANSCRIBE_USD_PER_MINUTE
	bedrock = attempts * BEDROCK_TOKENS_PER_ATTEMPT / 1000 * BEDROCK_USD_PER_1K_TOKENS

	line_items = [
		{"item": "CloudFront data transfer (video)", "usd": round(cdn_transfer, 2)},
		{"item": "CloudFront HTTPS requests", "usd": round(cdn_requests, 2)},
		{"item": "Transcribe (speaking)", "usd": round(transcribe, 2)},
		{"item": "Bedrock (rubric scoring)", "usd": round(bedrock, 2)},
	]
	return {
		"line_items": line_items,
		"total_usd": round(sum(row["usd"] for row in line_items), 2),
		"disclaimer": _(
			"Estimate from application-metered usage and Ek-6.3 example unit prices. "
			"The CUR-based FinOps panel replaces this once the AWS infrastructure is connected."
		),
	}

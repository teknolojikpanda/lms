# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Session watermarking (§1.1 "kopyalama caydiricilik", §4.4.3).

Issues a per-viewer code drawn over the player, and records it so a
recording that surfaces later can be traced back to whoever was watching.
The registry is what makes this forensic rather than decorative: without
it the overlay is just a number on the screen.

Policy (code shape, position schedule, what may be displayed) lives in
``watermark_rules``; this module handles issuance, lookup and retention.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import now_datetime

from lms.lms.language_platform.privacy_rules import retention_cutoff
from lms.lms.language_platform.watermark_rules import (
	WatermarkError,
	build_overlay_config,
	generate_session_code,
	normalize_code,
)
from lms.lms.permissions import can_access_lesson
from lms.lms.utils import has_moderator_role

PURGE_BATCH_SIZE = 500


def _settings():
	return frappe.get_cached_doc("LMS Language Settings")


def watermark_enabled() -> bool:
	try:
		return bool(_settings().watermark_enabled)
	except Exception:
		return False


@frappe.whitelist()
@rate_limit(limit=600, seconds=60 * 60)
def get_watermark(lesson: str) -> dict:
	"""Issue (or re-issue) this viewer's watermark for a lesson.

	Reuses an existing session for the same viewer and lesson so a reload
	does not mint a second identity — two codes for one viewing makes a
	later trace ambiguous for no benefit.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please login to play this lesson."), frappe.PermissionError)

	if not can_access_lesson(lesson):
		frappe.throw(_("You do not have access to this lesson."), frappe.PermissionError)

	if not watermark_enabled():
		return {"enabled": False}

	settings = _settings()
	existing = frappe.db.get_value(
		"LMS Watermark Session",
		{"member": frappe.session.user, "lesson": lesson},
		"code",
	)
	code = existing or _issue_session(lesson)

	try:
		config = build_overlay_config(
			code,
			label_prefix=settings.watermark_label_prefix or "",
			move_interval_seconds=settings.watermark_move_interval_seconds or 20,
			opacity=(settings.watermark_opacity or 35) / 100.0,
		)
	except WatermarkError as e:
		frappe.log_error(f"Watermark config failed for {lesson}: {e}", "Watermark Error")
		return {"enabled": False}

	config["enabled"] = True
	return config


def _issue_session(lesson: str) -> str:
	"""Create a new session code, retrying on the (unlikely) collision."""
	member = frappe.session.user

	for attempt in range(5):
		seed = f"{member}|{lesson}|{frappe.generate_hash(length=16)}|{attempt}"
		code = generate_session_code(seed)
		if frappe.db.exists("LMS Watermark Session", code):
			continue

		doc = frappe.get_doc(
			{
				"doctype": "LMS Watermark Session",
				"code": code,
				"member": member,
				"lesson": lesson,
				"issued_at": now_datetime(),
				"ip_address": frappe.local.request_ip,
				"user_agent": (frappe.get_request_header("User-Agent") or "")[:500],
			}
		)
		doc.insert(ignore_permissions=True)
		return code

	frappe.throw(_("Could not issue a watermark session. Please try again."))


@frappe.whitelist()
def trace_watermark(code: str) -> dict:
	"""Resolve a code seen in leaked footage back to its viewer.

	Moderator-only and audit-logged: this is the one operation that turns
	an anonymous-looking overlay into a person, so who asked and when is
	part of the record.
	"""
	if not has_moderator_role():
		frappe.throw(_("Only institution admins can trace watermarks."), frappe.PermissionError)

	try:
		code = normalize_code(code)
	except WatermarkError as e:
		frappe.throw(str(e))

	session = frappe.db.get_value(
		"LMS Watermark Session",
		code,
		["code", "member", "lesson", "course", "issued_at", "ip_address", "user_agent"],
		as_dict=True,
	)

	if not session:
		return {
			"found": False,
			"code": code,
			"note": _(
				"No session matches this code. It may predate watermarking, or have been "
				"removed under the retention policy."
			),
		}

	member_name = frappe.db.get_value("User", session.member, "full_name")

	# The lookup is itself a privacy-relevant act; record it against the
	# viewer whose identity was revealed.
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "User",
			"reference_name": session.member,
			"content": _("Watermark {0} traced to this user by {1}.").format(
				code, frappe.session.user
			),
		}
	).insert(ignore_permissions=True)

	frappe.logger("lms.security").info(
		"Watermark traced: code=%s member=%s by=%s", code, session.member, frappe.session.user
	)

	return {
		"found": True,
		"code": code,
		"member": session.member,
		"member_name": member_name,
		"lesson": session.lesson,
		"course": session.course,
		"issued_at": str(session.issued_at) if session.issued_at else None,
		"ip_address": session.ip_address,
		"user_agent": session.user_agent,
	}


def purge_expired_watermark_sessions():
	"""Retention for watermark sessions (Ek-2 class: session/PII data).

	These rows hold an IP address and user agent per viewing, so they are
	personal data and cannot be kept indefinitely just because they are
	small. The window is separate from exam records because their purpose
	is different: tracing a leak is useful for months, not years.
	"""
	retention_days = _settings().watermark_retention_days
	cutoff = retention_cutoff(retention_days)
	if cutoff is None:
		return

	names = frappe.get_all(
		"LMS Watermark Session",
		filters={"creation": ["<", cutoff]},
		pluck="name",
		limit_page_length=PURGE_BATCH_SIZE,
	)
	for name in names:
		frappe.delete_doc(
			"LMS Watermark Session", name, ignore_permissions=True, delete_permanently=True
		)

	if names:
		frappe.db.commit()
		frappe.logger("lms.retention").info(f"Purged {len(names)} expired watermark sessions")

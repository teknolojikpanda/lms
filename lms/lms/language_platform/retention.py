# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Ek-2 retention matrix enforcement (§6.4 "Retention ve silme akislari
teknik olarak uygulanir").

| Data type         | Default | Applied here                          |
|-------------------|---------|---------------------------------------|
| Audio submissions | 30 d    | ``speaking_pipeline.purge_expired_audio`` |
| Transcripts       | 1 y     | ``purge_expired_transcripts``         |
| Exam results      | 2 y     | ``purge_expired_attempts``            |
| Audit logs        | 2 y     | Frappe Version log + S3 export (infra)|
| App logs          | 90 d    | CloudWatch retention (infra)          |

Every window is configurable in **LMS Language Settings**; a value of 0
disables that purge, which institutions with longer statutory obligations
need. Purges run in bounded batches so a backlog cannot lock the table
for the whole scheduler slot.
"""

from __future__ import annotations

import frappe

from lms.lms.language_platform.privacy_rules import retention_cutoff
from lms.lms.language_platform.search import remove_document

PURGE_BATCH_SIZE = 500


def _settings():
	return frappe.get_cached_doc("LMS Language Settings")


def purge_expired_transcripts():
	"""Clear speaking transcripts past the retention window (Ek-2: 1 year).

	The submission row and its scores survive — those are academic records
	on the longer exam-results window. Only the verbatim text of what the
	student said is removed.
	"""
	cutoff = retention_cutoff(_settings().transcript_retention_days)
	if cutoff is None:
		return

	names = frappe.get_all(
		"LMS Speaking Submission",
		filters={"creation": ["<", cutoff], "transcript": ["is", "set"]},
		pluck="name",
		limit_page_length=PURGE_BATCH_SIZE,
	)
	for name in names:
		frappe.db.set_value(
			"LMS Speaking Submission",
			name,
			{"transcript": None},
			update_modified=False,
		)
		# The search index holds a copy of the transcript text. Clearing
		# only the database column would leave the personal data alive in
		# the cluster past its Ek-2 window.
		remove_document("LMS Speaking Submission", name)

	if names:
		frappe.db.commit()
		frappe.logger("lms.retention").info(f"Purged {len(names)} expired transcripts")


def purge_expired_attempts():
	"""Delete placement attempts past the retention window (Ek-2: 2 years).

	Deletion (not anonymisation) is correct here: the attempt row is only
	meaningful attached to its student, and the resulting level lives on
	the enrolment/profile independently.
	"""
	cutoff = retention_cutoff(_settings().exam_retention_days)
	if cutoff is None:
		return

	names = frappe.get_all(
		"LMS Placement Attempt",
		filters={"creation": ["<", cutoff], "status": "Completed"},
		pluck="name",
		limit_page_length=PURGE_BATCH_SIZE,
	)
	for name in names:
		frappe.delete_doc(
			"LMS Placement Attempt", name, ignore_permissions=True, delete_permanently=True
		)
	if names:
		frappe.db.commit()
		frappe.logger("lms.retention").info(f"Purged {len(names)} expired placement attempts")


def run_retention_jobs():
	"""Daily entry point — one hook, isolated failures.

	A failure in one purge must not stop the others, so each is wrapped:
	losing today's transcript purge should never also skip exam results.
	"""
	from lms.lms.language_platform.watermark import purge_expired_watermark_sessions

	for job in (purge_expired_transcripts, purge_expired_attempts, purge_expired_watermark_sessions):
		try:
			job()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				frappe.get_traceback(), f"Retention job failed: {job.__name__}"
			)

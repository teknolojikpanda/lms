# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Speaking assessment pipeline — the Frappe equivalent of the agreement's
Step Functions state machine (§4.9.1, §8.13, ADR-0001).

States: Queued → Transcribing → Scoring → Ready | Failed

- transient errors: retried with backoff (MAX_RETRIES), like Step Functions
  retry policies;
- permanent errors / exhausted retries: status = Failed with the error
  recorded — the DLQ analog; failures stay queryable for the ops screens;
- idempotency (§8.11 MUST): re-processing a Ready/Failed submission is a
  no-op; each state write is committed so a crashed worker resumes cleanly.
"""

import frappe
from frappe.utils import add_days, now_datetime

from lms.lms.language_platform.speaking_metrics import compute_metrics
from lms.lms.language_platform.speaking_providers import get_provider

MAX_RETRIES = 3
RETRY_DELAYS_SECONDS = [60, 300, 900]


def enqueue_processing(submission_name: str):
	frappe.enqueue(
		process_submission,
		queue="long",
		job_id=f"speaking::{submission_name}",
		deduplicate=True,
		submission_name=submission_name,
	)


def process_submission(submission_name: str):
	submission = frappe.get_doc("LMS Speaking Submission", submission_name)

	if submission.status in ("Ready", "Failed"):
		return  # idempotency guard

	provider = get_provider()

	try:
		_set_status(submission, "Transcribing")
		transcript = provider.transcribe(submission)

		metrics = compute_metrics(transcript, submission.duration_seconds)
		submission.transcript = transcript
		submission.word_count = metrics["word_count"]
		submission.wpm = metrics["wpm"]
		submission.lexical_diversity = metrics["lexical_diversity"]
		submission.filler_ratio = metrics["filler_ratio"]
		_set_status(submission, "Scoring")

		scores = provider.score(submission, transcript, metrics)
		submission.rubric_scores = []
		for row in scores:
			submission.append("rubric_scores", row)
		submission.ai_total_score = round(sum(r["score"] for r in scores) / len(scores), 1)
		if not submission.is_overridden:
			submission.final_score = submission.ai_total_score
		_set_status(submission, "Ready")

	except Exception as e:
		frappe.db.rollback()
		submission.reload()
		_handle_failure(submission, e)


def _set_status(submission, status: str):
	submission.status = status
	submission.save(ignore_permissions=True)
	frappe.db.commit()


def _handle_failure(submission, error: Exception):
	frappe.log_error(
		f"Speaking pipeline failed for {submission.name}: {error}", "Speaking Pipeline Error"
	)
	if (submission.retry_count or 0) < MAX_RETRIES:
		delay = RETRY_DELAYS_SECONDS[min(submission.retry_count or 0, len(RETRY_DELAYS_SECONDS) - 1)]
		submission.retry_count = (submission.retry_count or 0) + 1
		submission.status = "Queued"
		submission.save(ignore_permissions=True)
		frappe.db.commit()
		# Frappe/RQ has no native delayed enqueue; the retry runs on the
		# long queue immediately and RETRY_DELAYS_SECONDS documents the
		# intended backoff for a future scheduler-based re-drive.
		frappe.enqueue(
			process_submission,
			queue="long",
			deduplicate=True,
			job_id=f"speaking::{submission.name}::retry{submission.retry_count}",
			submission_name=submission.name,
		)
	else:
		submission.status = "Failed"
		submission.error_message = str(error)[:500]
		submission.save(ignore_permissions=True)
		frappe.db.commit()


def purge_expired_audio():
	"""Daily retention job (Ek-2: audio submissions default 30 days).

	Deletes the audio file attachment once the retention window passes;
	transcript and scores are kept per the retention matrix.
	"""
	retention_days = frappe.get_cached_doc("LMS Language Settings").audio_retention_days or 30
	cutoff = add_days(now_datetime(), -int(retention_days))

	expired = frappe.get_all(
		"LMS Speaking Submission",
		filters={
			"creation": ["<", cutoff],
			"status": ["in", ["Ready", "Failed"]],
			"audio_file": ["is", "set"],
		},
		pluck="name",
	)
	for name in expired:
		submission = frappe.get_doc("LMS Speaking Submission", name)
		for file in frappe.get_all(
			"File",
			{"attached_to_doctype": "LMS Speaking Submission", "attached_to_name": name},
			pluck="name",
		):
			frappe.delete_doc("File", file, ignore_permissions=True, delete_permanently=True)
		submission.db_set("audio_file", None, update_modified=False)
		submission.add_comment("Comment", "Audio purged by retention policy.")
	if expired:
		frappe.db.commit()

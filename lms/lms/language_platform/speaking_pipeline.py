# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Speaking assessment pipeline — the Frappe equivalent of the agreement's
Step Functions state machine (§4.9.1, §8.13, ADR-0001).

States: Queued → Transcribing → Scoring → Ready | Failed

- transient errors: retried up to MAX_RETRIES, like Step Functions retry
  policies. The retry is enqueued immediately — RETRY_DELAYS_SECONDS
  records the intended backoff, which needs a scheduler to deliver;
- permanent errors / exhausted retries: status = Failed with the error
  recorded — the DLQ analog; failures stay queryable for the ops screens;
- idempotency (§8.11 MUST): the Queued → Transcribing transition is a
  locked check-and-set (see _claim), so exactly one worker processes a
  submission and re-processing one in any other state is a no-op. Each
  state write is committed, so progress survives a crashed worker.

Recording a failure never goes through the document API — see
_handle_failure for why that distinction is load-bearing.
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
	if not _claim(submission_name):
		return

	# Loaded after the claim so the in-memory timestamp matches the row.
	submission = frappe.get_doc("LMS Speaking Submission", submission_name)
	provider = get_provider()

	try:
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
		# Deliberately pass the name, not the document: when the error is a
		# timestamp mismatch the in-memory copy is stale by definition, and
		# reloading it only narrows the window rather than closing it.
		_handle_failure(submission_name, e)


def _claim(submission_name: str) -> bool:
	"""Take exclusive ownership of a submission, or decline it.

	`after_insert` enqueues a background job, but nothing stops a second
	caller — a retry, a re-drive, or a synchronous call in a test — from
	entering at the same time. A read-only status check cannot prevent
	that: two workers both read "Queued", both proceed, and their saves
	interleave into a timestamp mismatch. `deduplicate` does not help
	either, since it only suppresses a duplicate job while one is still
	*queued*, and retries deliberately use a different job id.

	So the Queued → Transcribing transition is the lock. SELECT ... FOR
	UPDATE holds the row for the check-and-set, so exactly one caller
	sees "Queued" and every other one declines. This is what makes the
	§8.11 idempotency guarantee actually hold: re-processing a submission
	in any other state — Ready, Failed, or in flight — is a no-op.

	Known gap: a worker that dies mid-run leaves the row in Transcribing
	or Scoring, and nothing re-drives it. That was equally true before,
	and closing it needs a scheduled sweep for stale in-flight rows.
	"""
	row = frappe.db.sql(
		"SELECT status FROM `tabLMS Speaking Submission` WHERE name = %s FOR UPDATE",
		submission_name,
	)
	claimed = bool(row) and row[0][0] == "Queued"
	if claimed:
		frappe.db.set_value("LMS Speaking Submission", submission_name, "status", "Transcribing")
	# Commit either way: this releases the row lock, and on the winning
	# path it publishes the claim so a rival sees it immediately.
	frappe.db.commit()
	return claimed


def _set_status(submission, status: str):
	submission.status = status
	submission.save(ignore_permissions=True)
	frappe.db.commit()


def _handle_failure(submission_name: str, error: Exception):
	"""Record a failure. Must not be able to fail in turn.

	Writes go through db.set_value rather than the document API on
	purpose. `save()` checks the document timestamp, so recording a
	timestamp-mismatch failure would raise a *second* mismatch and take
	the handler down with it — leaving the submission stuck in a
	non-terminal status with no retry queued and no error recorded, so
	it never reaches the ops screens as either failed or in flight.
	"""
	frappe.log_error(
		f"Speaking pipeline failed for {submission_name}: {error}", "Speaking Pipeline Error"
	)
	try:
		if not frappe.db.exists("LMS Speaking Submission", submission_name):
			return  # deleted while in flight; there is nothing left to record
		retry_count = (
			frappe.db.get_value("LMS Speaking Submission", submission_name, "retry_count") or 0
		)
		if retry_count < MAX_RETRIES:
			retry_count += 1
			# Back to Queued so the retry can claim it.
			frappe.db.set_value(
				"LMS Speaking Submission",
				submission_name,
				{"retry_count": retry_count, "status": "Queued"},
			)
			frappe.db.commit()
			# Frappe/RQ has no native delayed enqueue; the retry runs on the
			# long queue immediately and RETRY_DELAYS_SECONDS documents the
			# intended backoff for a future scheduler-based re-drive.
			frappe.enqueue(
				process_submission,
				queue="long",
				deduplicate=True,
				job_id=f"speaking::{submission_name}::retry{retry_count}",
				submission_name=submission_name,
			)
		else:
			frappe.db.set_value(
				"LMS Speaking Submission",
				submission_name,
				{"status": "Failed", "error_message": str(error)[:500]},
			)
			frappe.db.commit()
	except Exception:
		# The handler of last resort. Nothing above should raise, but if it
		# does the job must still end quietly rather than bubble an error
		# that hides the original one.
		frappe.db.rollback()
		frappe.log_error(
			f"Could not record the failure of {submission_name}", "Speaking Pipeline Error"
		)


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

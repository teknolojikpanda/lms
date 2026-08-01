# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Speaking assessment pipeline — the Frappe equivalent of the agreement's
Step Functions state machine (§4.9.1, §8.13, ADR-0001).

States: Queued → Transcribing → Scoring → Ready | Failed

- transient errors: retried up to MAX_RETRIES with the RETRY_DELAYS_SECONDS
  backoff, like Step Functions retry policies. Frappe has no delayed
  enqueue, so the delay is stored on the row as `retry_after` and
  dispatch_due_retries enqueues what has come due. _claim enforces it, so
  the wait holds however the submission gets enqueued;
- permanent errors / exhausted retries: status = Failed with the error
  recorded — the DLQ analog; failures stay queryable for the ops screens;
- idempotency (§8.11 MUST): the Queued → Transcribing transition is a
  locked check-and-set (see _claim), so exactly one worker processes a
  submission and re-processing one in any other state is a no-op. Each
  state write is committed, so progress survives a crashed worker;
- abandoned work: a dead worker or a lost queue message would otherwise
  strand a submission forever, since _claim admits only Queued rows.
  requeue_stale_submissions sweeps for both hourly.

Recording a failure never goes through the document API — see
_handle_failure for why that distinction is load-bearing.
"""

import frappe
from frappe.utils import add_days, add_to_date, get_datetime, now_datetime

from lms.lms.language_platform.speaking_metrics import compute_metrics
from lms.lms.language_platform.speaking_providers import get_provider

MAX_RETRIES = 3
RETRY_DELAYS_SECONDS = [60, 300, 900]

IN_FLIGHT_STATUSES = ("Transcribing", "Scoring")
DEFAULT_STALE_AFTER_MINUTES = 30
SWEEP_BATCH_SIZE = 200


def retry_delay(attempt: int) -> int:
	"""Seconds to wait before attempt number ``attempt`` (1-based).

	Attempts past the end of the table reuse the last delay rather than
	extending, since MAX_RETRIES stops the sequence anyway.
	"""
	index = min(max(int(attempt), 1) - 1, len(RETRY_DELAYS_SECONDS) - 1)
	return RETRY_DELAYS_SECONDS[index]


def _backoff_pending(retry_after, now=None) -> bool:
	"""True while a submission is still serving its retry delay."""
	if not retry_after:
		return False
	return get_datetime(retry_after) > (now or now_datetime())


def dispatch_due_retries():
	"""Enqueue retries whose backoff has elapsed. Runs every minute.

	This is what turns RETRY_DELAYS_SECONDS from documentation into
	behaviour. Frappe has no delayed enqueue, so the delay is stored on
	the row and a scheduled pass picks up whatever has come due — which
	is sturdier than a held job anyway: a queue flush or a worker restart
	loses scheduled jobs, and loses nothing here.

	Runs every minute because the first retry waits 60 seconds; a coarser
	tick would round the shortest delay up to itself. `enqueue_processing`
	deduplicates on a stable job id, so overlapping passes cannot stack
	duplicate jobs, and _claim refuses anything not actually due.
	"""
	# Oldest due first. With more submissions due than the batch takes,
	# an unordered query lets the database decide who waits — and it can
	# decide the same way every pass, so one student's recording is
	# starved indefinitely while later ones are dispatched ahead of it.
	# Ordering by when each became due makes the queue a queue.
	due = frappe.get_all(
		"LMS Speaking Submission",
		filters={"status": "Queued", "retry_after": ["<=", now_datetime()]},
		pluck="name",
		order_by="retry_after asc, creation asc",
		limit=SWEEP_BATCH_SIZE,
	)
	for name in due:
		enqueue_processing(name)
	if due:
		frappe.logger("lms.speaking").info(f"dispatched {len(due)} due retr(ies)")
	return {"dispatched": due}


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

	Admitting only Queued rows means a worker that dies mid-run leaves
	its submission unclaimable. requeue_stale_submissions is what brings
	those back, and is the only thing that does.

	A submission still inside its retry backoff is declined too, and this
	is where the backoff is actually enforced. Anything may enqueue a
	submission — the dispatcher, the stale sweep, an operator — so a
	delay honoured only by the thing that schedules the job is not a
	delay. Refusing the claim makes it hold no matter who calls.
	"""
	row = frappe.db.sql(
		"""
		SELECT status, retry_after FROM `tabLMS Speaking Submission`
		WHERE name = %s FOR UPDATE
		""",
		submission_name,
		as_dict=True,
	)
	current = row[0] if row else None
	claimed = bool(current) and current.status == "Queued" and not _backoff_pending(current.retry_after)
	if claimed:
		# retry_after is cleared as it is served, so a later failure sets a
		# fresh delay rather than inheriting a spent one.
		frappe.db.set_value(
			"LMS Speaking Submission",
			submission_name,
			{"status": "Transcribing", "retry_after": None},
		)
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
		current = frappe.db.get_value(
			"LMS Speaking Submission", submission_name, ["status", "retry_count"], as_dict=True
		)
		if current is None:
			return  # deleted while in flight; there is nothing left to record
		if current.status in ("Ready", "Failed"):
			# Someone else already finished it. This happens when the sweep
			# re-queues a stalled submission, a second worker completes it,
			# and the original then wakes up and reports the failure it hit
			# on the way out. Writing here would knock a finished submission
			# back to Queued and re-run the provider on it.
			return
		retry_count = current.retry_count or 0
		if retry_count < MAX_RETRIES:
			retry_count += 1
			# Back to Queued, but not yet runnable: retry_after carries the
			# backoff. Nothing is enqueued here — dispatch_due_retries picks
			# the submission up once the delay has elapsed.
			#
			# The delay lives on the row rather than in a job because that is
			# the only version of it that survives. A worker holding a sleep,
			# or a queue holding a scheduled job, loses the backoff the moment
			# it restarts; a timestamp in the database does not.
			frappe.db.set_value(
				"LMS Speaking Submission",
				submission_name,
				{
					"retry_count": retry_count,
					"status": "Queued",
					"retry_after": add_to_date(now_datetime(), seconds=retry_delay(retry_count)),
				},
			)
			frappe.db.commit()
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


def requeue_stale_submissions():
	"""Hourly sweep: recover submissions no worker is coming back for.

	Two ways work gets abandoned, both of which used to strand a
	submission permanently:

	  * a worker dies mid-run, leaving the row in Transcribing or
	    Scoring. _claim only admits Queued rows, so nothing would ever
	    pick it up again;
	  * the queue loses the job — a Redis restart, say — leaving the row
	    in Queued with nothing scheduled to act on it.

	The first is a genuine judgement call, because a worker that is slow
	is indistinguishable from one that is dead: neither writes to the
	row. The timeout is therefore the whole safety margin, which is why
	it is configurable and why its default (30 minutes) sits far above
	the real bound on the work — recordings are capped at a few minutes
	by §8.13, so anything approaching the timeout is not merely slow.

	Being wrong is survivable but not free: the original worker's next
	write fails the timestamp check, and _handle_failure declines to
	touch a submission another worker has since finished. The cost of
	guessing wrong is one duplicate provider call, so the timeout is set
	to make that rare rather than to make it impossible.
	"""
	minutes = (
		int(frappe.get_cached_doc("LMS Language Settings").speaking_stale_after_minutes or 0)
		or DEFAULT_STALE_AFTER_MINUTES
	)
	cutoff = add_to_date(now_datetime(), minutes=-minutes)
	summary = {"requeued": [], "failed": [], "re_enqueued": [], "stale_after_minutes": minutes}

	for row in frappe.get_all(
		"LMS Speaking Submission",
		filters={"status": ["in", IN_FLIGHT_STATUSES], "modified": ["<", cutoff]},
		pluck="name",
		limit=SWEEP_BATCH_SIZE,
	):
		outcome = _rescue_if_still_stale(row, cutoff)
		if outcome:
			summary[outcome].append(row)

	# Queued rows need no state change — only a job. enqueue_processing
	# deduplicates on a stable id, so re-running the sweep while one is
	# genuinely still waiting in the queue does not pile up duplicates,
	# and a row that a worker has since claimed is no longer Queued.
	#
	# This is also the backstop for a stalled dispatcher: a retry whose
	# backoff elapsed but which was never picked up eventually ages past
	# the threshold and is enqueued here instead. It cannot fire early —
	# the longest backoff is far under the staleness threshold, and _claim
	# refuses anything still inside its delay regardless.
	for name in frappe.get_all(
		"LMS Speaking Submission",
		filters={"status": "Queued", "modified": ["<", cutoff]},
		pluck="name",
		limit=SWEEP_BATCH_SIZE,
	):
		enqueue_processing(name)
		summary["re_enqueued"].append(name)

	for name in summary["requeued"]:
		enqueue_processing(name)

	if summary["requeued"] or summary["failed"] or summary["re_enqueued"]:
		frappe.logger("lms.speaking").info(
			f"stale sweep: requeued={len(summary['requeued'])} "
			f"failed={len(summary['failed'])} re_enqueued={len(summary['re_enqueued'])} "
			f"(threshold {minutes}m)"
		)
	return summary


def _rescue_if_still_stale(submission_name: str, cutoff) -> str | None:
	"""Re-queue one abandoned submission, or leave it alone.

	The staleness test is repeated under a row lock rather than trusted
	from the listing above. Between the two, the worker may have written
	— which both proves it is alive and moves `modified` past the cutoff
	— and re-queueing then would hand a live submission to a second
	worker. Re-reading inside the lock makes this a compare-and-swap.
	"""
	locked = frappe.db.sql(
		"""
		SELECT status, modified, retry_count
		FROM `tabLMS Speaking Submission`
		WHERE name = %s FOR UPDATE
		""",
		submission_name,
		as_dict=True,
	)
	row = locked[0] if locked else None
	if not row or row.status not in IN_FLIGHT_STATUSES or get_datetime(row.modified) >= cutoff:
		frappe.db.commit()  # release the lock; it progressed or vanished
		return None

	retry_count = (row.retry_count or 0) + 1
	if retry_count > MAX_RETRIES:
		# Shares the retry budget with _handle_failure on purpose: a
		# submission that hangs every time must eventually stop.
		frappe.db.set_value(
			"LMS Speaking Submission",
			submission_name,
			{
				"status": "Failed",
				"error_message": (
					f"Abandoned in {row.status} with no progress for over "
					f"{MAX_RETRIES} recovery attempts."
				)[:500],
			},
		)
		outcome = "failed"
	else:
		frappe.db.set_value(
			"LMS Speaking Submission",
			submission_name,
			{"status": "Queued", "retry_count": retry_count},
		)
		outcome = "requeued"
	frappe.db.commit()
	return outcome


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

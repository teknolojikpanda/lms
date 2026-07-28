from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime

from lms.lms.language_platform import speaking_pipeline
from lms.lms.language_platform.speaking_pipeline import (
	DEFAULT_STALE_AFTER_MINUTES,
	MAX_RETRIES,
	_claim,
	_handle_failure,
	dispatch_due_retries,
	process_submission,
	requeue_stale_submissions,
	retry_delay,
)


class TestSpeakingPipelineFailure(IntegrationTestCase):
	"""The pipeline's failure path and its exclusion between workers.

	Both were found on a real bench: two callers processed one submission
	concurrently, and the resulting timestamp mismatch took down the very
	handler meant to record it — so the submission was left in flight
	forever, invisible to the ops screens as either failed or pending.

	The code under test commits, which the enclosing test transaction
	cannot roll back, so everything created here is removed explicitly.
	"""

	def setUp(self):
		super().setUp()
		self.hash = frappe.generate_hash(length=6)
		self.submissions = []
		# Inserting a submission enqueues a real job via after_insert. Left
		# alone, a worker claims these rows and races the test for them —
		# which surfaced as a lock-wait timeout in teardown. Capture the
		# enqueue instead of running it; that also lets the retry case
		# assert the retry was scheduled rather than infer it.
		self._enqueue_patcher = patch("frappe.enqueue")
		self.enqueue = self._enqueue_patcher.start()
		# Registered before any document is created, so cleanups (which run
		# after tearDown) keep the patch active while teardown deletes.
		self.addCleanup(self._enqueue_patcher.stop)
		self.member = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"spk-{self.hash}@example.com",
				"first_name": "Spk",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		self.prompt = frappe.get_doc(
			{
				"doctype": "LMS Speaking Prompt",
				"title": f"Pipeline Prompt {self.hash}",
				"scenario": "Describe your morning.",
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	def tearDown(self):
		for name in self.submissions:
			frappe.delete_doc(
				"LMS Speaking Submission", name, force=True, ignore_permissions=True, ignore_missing=True
			)
		frappe.delete_doc("LMS Speaking Prompt", self.prompt.name, force=True, ignore_permissions=True)
		frappe.delete_doc("User", self.member.name, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDown()

	def _submission(self, status="Queued", retry_count=0):
		doc = frappe.get_doc(
			{
				"doctype": "LMS Speaking Submission",
				"member": self.member.name,
				"prompt": self.prompt.name,
				"audio_file": f"/private/files/pipeline-{self.hash}.webm",
				"duration_seconds": 30,
				"status": "Queued",
			}
		).insert(ignore_permissions=True)
		self.submissions.append(doc.name)
		# after_insert enqueues the real job; put the row into the state
		# this case needs without racing it.
		frappe.db.set_value(
			"LMS Speaking Submission",
			doc.name,
			{"status": status, "retry_count": retry_count},
		)
		frappe.db.commit()
		return doc.name

	def _row(self, name):
		return frappe.db.get_value(
			"LMS Speaking Submission", name, ["status", "retry_count", "error_message"], as_dict=True
		)

	# --- the failure handler must not fail -------------------------------

	def test_records_failure_from_a_stale_document(self):
		"""The regression: a stale in-memory copy must not break recording.

		Reproduces what happened on the bench — the row is modified behind
		the caller's back, so anything writing through save() raises a
		second timestamp mismatch and never records the first.
		"""
		name = self._submission(status="Transcribing", retry_count=MAX_RETRIES)
		stale = frappe.get_doc("LMS Speaking Submission", name)
		frappe.db.set_value("LMS Speaking Submission", name, "transcript", "written by someone else")
		frappe.db.commit()

		# Proof the document really is stale: the document API rejects it.
		with self.assertRaises(frappe.TimestampMismatchError):
			stale.save(ignore_permissions=True)

		_handle_failure(name, RuntimeError("provider exploded"))

		row = self._row(name)
		self.assertEqual(row.status, "Failed")
		self.assertIn("provider exploded", row.error_message)

	def test_retries_before_giving_up(self):
		name = self._submission(status="Transcribing", retry_count=0)
		self.enqueue.reset_mock()
		before = now_datetime()
		_handle_failure(name, RuntimeError("transient"))

		row = self._row(name)
		self.assertEqual(row.status, "Queued", "a retryable failure must return to the claimable state")
		self.assertEqual(row.retry_count, 1)

		# The backoff is recorded, not slept through and not enqueued now.
		self.assertEqual(
			self.enqueue.call_count, 0, "a retry must wait for its backoff, not run immediately"
		)
		retry_after = frappe.db.get_value("LMS Speaking Submission", name, "retry_after")
		self.assertIsNotNone(retry_after, "no backoff recorded")
		delay = (get_datetime(retry_after) - before).total_seconds()
		self.assertAlmostEqual(delay, retry_delay(1), delta=5)

	def test_backoff_lengthens_with_each_attempt(self):
		"""Each failure waits longer, per RETRY_DELAYS_SECONDS."""
		seen = []
		for attempt in range(1, MAX_RETRIES + 1):
			name = self._submission(status="Transcribing", retry_count=attempt - 1)
			before = now_datetime()
			_handle_failure(name, RuntimeError("transient"))
			retry_after = frappe.db.get_value("LMS Speaking Submission", name, "retry_after")
			seen.append(round((get_datetime(retry_after) - before).total_seconds()))

		self.assertEqual(seen, [retry_delay(n) for n in range(1, MAX_RETRIES + 1)])
		self.assertEqual(seen, sorted(seen), f"backoff must not shrink: {seen}")

	def test_a_submission_inside_its_backoff_cannot_be_claimed(self):
		"""The wait is enforced at the claim, so it holds however the job arrives."""
		name = self._submission(status="Transcribing", retry_count=0)
		_handle_failure(name, RuntimeError("transient"))
		self.assertEqual(self._row(name).status, "Queued")

		self.assertFalse(_claim(name), "claimed while still inside the retry backoff")
		self.assertEqual(self._row(name).status, "Queued", "a refused claim must not change state")

	def test_the_claim_succeeds_once_the_backoff_elapses(self):
		name = self._submission(status="Transcribing", retry_count=0)
		_handle_failure(name, RuntimeError("transient"))

		frappe.db.set_value(
			"LMS Speaking Submission", name, "retry_after", add_to_date(now_datetime(), seconds=-1)
		)
		frappe.db.commit()

		self.assertTrue(_claim(name))
		self.assertIsNone(
			frappe.db.get_value("LMS Speaking Submission", name, "retry_after"),
			"a served backoff must be cleared, not inherited by the next failure",
		)

	# --- the dispatcher --------------------------------------------------

	def test_dispatcher_ignores_a_submission_still_waiting(self):
		name = self._submission(status="Transcribing", retry_count=0)
		_handle_failure(name, RuntimeError("transient"))
		self.enqueue.reset_mock()

		self.assertNotIn(name, dispatch_due_retries()["dispatched"])
		self.assertEqual(self.enqueue.call_count, 0)

	def test_dispatcher_enqueues_a_submission_whose_backoff_elapsed(self):
		name = self._submission(status="Transcribing", retry_count=0)
		_handle_failure(name, RuntimeError("transient"))
		frappe.db.set_value(
			"LMS Speaking Submission", name, "retry_after", add_to_date(now_datetime(), seconds=-1)
		)
		frappe.db.commit()
		self.enqueue.reset_mock()

		self.assertIn(name, dispatch_due_retries()["dispatched"])
		self.assertIn(name, [c.kwargs.get("submission_name") for c in self.enqueue.call_args_list])

	def test_dispatcher_leaves_finished_submissions_alone(self):
		for status in ("Ready", "Failed"):
			with self.subTest(status=status):
				name = self._submission(status=status)
				frappe.db.set_value(
					"LMS Speaking Submission",
					name,
					"retry_after",
					add_to_date(now_datetime(), seconds=-60),
				)
				frappe.db.commit()
				self.assertNotIn(name, dispatch_due_retries()["dispatched"])

	def test_gives_up_once_retries_are_exhausted(self):
		name = self._submission(status="Transcribing", retry_count=MAX_RETRIES)
		_handle_failure(name, RuntimeError("permanent"))
		self.assertEqual(self._row(name).status, "Failed")

	def test_never_raises_even_if_the_submission_is_gone(self):
		"""Last-resort guard: the handler runs because something already
		went wrong, so it must not add a second exception on top."""
		name = self._submission()
		frappe.delete_doc("LMS Speaking Submission", name, force=True, ignore_permissions=True)
		frappe.db.commit()
		self.submissions.remove(name)
		self.enqueue.reset_mock()

		_handle_failure(name, RuntimeError("boom"))  # must not raise

		self.assertEqual(
			self.enqueue.call_count, 0, "a deleted submission must not be queued for retry"
		)

	# --- exclusion between workers ---------------------------------------

	def test_only_one_caller_claims_a_submission(self):
		name = self._submission()
		self.assertTrue(_claim(name))
		self.assertFalse(_claim(name), "a second worker must not claim a submission in flight")
		self.assertEqual(self._row(name).status, "Transcribing")

	def test_claim_declines_terminal_and_in_flight_states(self):
		for status in ("Ready", "Failed", "Transcribing", "Scoring"):
			with self.subTest(status=status):
				self.assertFalse(_claim(self._submission(status=status)))

	def test_processing_a_claimed_submission_is_a_no_op(self):
		"""§8.11: the second caller must not re-run the provider.

		Without this the provider runs twice for one recording — wasted
		spend on Transcribe and Bedrock, on top of the write race.
		"""
		name = self._submission()
		self.assertTrue(_claim(name))

		calls = []
		original = speaking_pipeline.get_provider

		def counting_provider():
			calls.append(1)
			return original()

		speaking_pipeline.get_provider = counting_provider
		try:
			process_submission(name)
		finally:
			speaking_pipeline.get_provider = original

		self.assertEqual(calls, [], "an already-claimed submission must not reach the provider")

	# --- the sweep for abandoned work ------------------------------------

	@property
	def threshold(self):
		"""The site's configured staleness window, not the constant.

		Read rather than assumed (or overwritten) so these cases hold on a
		site whose administrator has tuned the setting.
		"""
		configured = frappe.get_cached_doc("LMS Language Settings").speaking_stale_after_minutes
		return int(configured or 0) or DEFAULT_STALE_AFTER_MINUTES

	def _age(self, name, minutes):
		"""Backdate `modified` so the row looks abandoned that long ago."""
		frappe.db.sql(
			"UPDATE `tabLMS Speaking Submission` SET modified = %s WHERE name = %s",
			(add_to_date(now_datetime(), minutes=-minutes), name),
		)
		frappe.db.commit()

	def test_sweep_requeues_a_submission_abandoned_in_flight(self):
		name = self._submission(status="Scoring")
		self._age(name, self.threshold + 5)
		self.enqueue.reset_mock()

		summary = requeue_stale_submissions()

		self.assertIn(name, summary["requeued"])
		row = self._row(name)
		self.assertEqual(row.status, "Queued", "a rescued submission must become claimable again")
		self.assertEqual(row.retry_count, 1)
		self.assertTrue(_claim(name), "and a worker must then be able to take it")

	def test_sweep_leaves_a_submission_that_is_still_progressing(self):
		"""The margin that keeps a slow worker from being treated as dead."""
		name = self._submission(status="Transcribing")
		self._age(name, self.threshold - 5)

		summary = requeue_stale_submissions()

		self.assertNotIn(name, summary["requeued"])
		self.assertEqual(self._row(name).status, "Transcribing")

	def test_sweep_gives_up_once_the_retry_budget_is_spent(self):
		"""A submission that hangs every time must not cycle forever."""
		name = self._submission(status="Scoring", retry_count=MAX_RETRIES)
		self._age(name, self.threshold + 5)

		summary = requeue_stale_submissions()

		self.assertIn(name, summary["failed"])
		row = self._row(name)
		self.assertEqual(row.status, "Failed")
		self.assertIn("Abandoned", row.error_message)

	def test_sweep_re_enqueues_a_queued_row_whose_job_was_lost(self):
		name = self._submission(status="Queued")
		self._age(name, self.threshold + 5)
		self.enqueue.reset_mock()

		summary = requeue_stale_submissions()

		self.assertIn(name, summary["re_enqueued"])
		self.assertEqual(
			self._row(name).status, "Queued", "re-driving a queued row must not change its state"
		)
		self.assertIn(name, [c.kwargs.get("submission_name") for c in self.enqueue.call_args_list])

	def test_sweep_ignores_finished_submissions(self):
		for status in ("Ready", "Failed"):
			with self.subTest(status=status):
				name = self._submission(status=status)
				self._age(name, self.threshold * 10)
				summary = requeue_stale_submissions()
				self.assertNotIn(name, summary["requeued"] + summary["failed"] + summary["re_enqueued"])
				self.assertEqual(self._row(name).status, status)

	def test_a_resurrected_worker_cannot_undo_a_finished_submission(self):
		"""The hazard the sweep introduces, and the guard that closes it.

		Sweep re-queues a stalled submission, a second worker finishes it,
		then the original wakes and reports the error it hit on its way
		out. Recording that would knock a completed submission back to
		Queued and re-run the provider on it.
		"""
		name = self._submission(status="Scoring")
		self._age(name, self.threshold + 5)
		requeue_stale_submissions()  # -> Queued, retry_count 1

		# A second worker takes it and finishes.
		self.assertTrue(_claim(name))
		frappe.db.set_value(
			"LMS Speaking Submission", name, {"status": "Ready", "final_score": 77}
		)
		frappe.db.commit()

		# Now the original worker reports the failure it hit.
		_handle_failure(name, RuntimeError("lost the claim"))

		row = self._row(name)
		self.assertEqual(row.status, "Ready", "a finished submission must not be reopened")
		self.assertIsNone(row.error_message)

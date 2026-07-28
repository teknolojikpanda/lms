from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms.lms.language_platform import speaking_pipeline
from lms.lms.language_platform.speaking_pipeline import (
	MAX_RETRIES,
	_claim,
	_handle_failure,
	process_submission,
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
		_handle_failure(name, RuntimeError("transient"))

		row = self._row(name)
		self.assertEqual(row.status, "Queued", "a retryable failure must return to the claimable state")
		self.assertEqual(row.retry_count, 1)

		self.assertEqual(self.enqueue.call_count, 1, "the retry must actually be scheduled")
		self.assertEqual(self.enqueue.call_args.kwargs["submission_name"], name)

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

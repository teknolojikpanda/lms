"""Two values that were being discarded rather than honoured.

A zero-day grace period read as "unset" and became the 90-day default,
so an offboarding an operator had explicitly authorised to proceed at
once was refused for three months. And the retry dispatcher took its
batch with no ordering, letting the database decide who waits — which it
can decide the same way every pass, starving one submission indefinitely.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from lms.lms.doctype.lms_tenant.lms_tenant import (
	_effective_grace_days,
	archive_tenant,
	get_purge_readiness,
)
from lms.lms.language_platform.speaking_pipeline import dispatch_due_retries
from lms.lms.language_platform.tenant_rules import DEFAULT_GRACE_DAYS


class TestZeroDayGrace(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.subdomain = f"grace-{frappe.generate_hash(length=6)}".lower()
		frappe.get_doc(
			{
				"doctype": "LMS Tenant",
				"tenant_name": "Grace Koleji",
				"subdomain": self.subdomain,
				"status": "Active",
				"seat_limit": 10,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	def tearDown(self):
		frappe.delete_doc(
			"LMS Tenant", self.subdomain, force=True, ignore_permissions=True, ignore_missing=True
		)
		frappe.db.commit()
		super().tearDown()

	def test_zero_is_a_value_not_an_absence(self):
		"""The defect: `if doc.grace_days` is False for a stored 0."""
		self.assertEqual(_effective_grace_days(frappe._dict(grace_days=0)), 0)

	def test_an_unset_period_still_falls_back(self):
		"""Tenants archived before the field existed keep their old judgement."""
		self.assertEqual(
			_effective_grace_days(frappe._dict(grace_days=None)), DEFAULT_GRACE_DAYS
		)

	def test_an_explicit_override_survives(self):
		self.assertEqual(_effective_grace_days(frappe._dict(grace_days=7)), 7)

	def test_a_zero_day_archival_is_purgeable_at_once(self):
		"""What the operator asked for: no waiting period.

		An export is already recorded and the backup verified, so the only
		question is the grace clock — and they set it to zero.
		"""
		archive_tenant(self.subdomain, "Contract ended, data already handed over", grace_days=0)
		frappe.db.set_value(
			"LMS Tenant",
			self.subdomain,
			{
				"export_location": "s3://exports/grace-test.tar.gz",
				"backup_verified": 1,
			},
		)
		frappe.db.commit()

		readiness = get_purge_readiness(self.subdomain)
		self.assertTrue(
			readiness["ready"],
			f"a zero-day grace was still holding the purge: {readiness['blockers']}",
		)

	def test_a_ninety_day_default_still_holds_a_fresh_archival(self):
		"""The guard this must not weaken."""
		archive_tenant(self.subdomain, "Contract ended")
		frappe.db.set_value(
			"LMS Tenant",
			self.subdomain,
			{"export_location": "s3://exports/x.tar.gz", "backup_verified": 1},
		)
		frappe.db.commit()

		self.assertFalse(get_purge_readiness(self.subdomain)["ready"])


class TestRetryDispatchOrder(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.hash = frappe.generate_hash(length=6)
		self.created = []

		enqueue = patch("frappe.enqueue")
		enqueue.start()
		self.addCleanup(enqueue.stop)

		self.prompt = self._make(
			{
				"doctype": "LMS Speaking Prompt",
				"title": f"Order prompt {self.hash}",
				"scenario": "Say something.",
				"language_level": "A1",
				"max_duration_seconds": 60,
			}
		)

	def tearDown(self):
		for doctype, name in reversed(self.created):
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDown()

	def _make(self, payload):
		doc = frappe.get_doc(payload).insert(ignore_permissions=True)
		self.created.append((doc.doctype, doc.name))
		return doc

	def _queued(self, minutes_overdue):
		doc = self._make(
			{
				"doctype": "LMS Speaking Submission",
				"prompt": self.prompt.name,
				"member": "Administrator",
				"audio_file": "/files/x.webm",
				"duration_seconds": 5,
			}
		)
		frappe.db.set_value(
			"LMS Speaking Submission",
			doc.name,
			{
				"status": "Queued",
				"retry_after": add_to_date(now_datetime(), minutes=-minutes_overdue),
			},
			update_modified=False,
		)
		frappe.db.commit()
		return doc.name

	def test_the_longest_waiting_submission_is_dispatched_first(self):
		"""Otherwise the database chooses, and it can choose the same way every pass.

		A batch smaller than the backlog then dispatches the same newer
		submissions each minute while an older one waits for good.
		"""
		newest = self._queued(minutes_overdue=1)
		oldest = self._queued(minutes_overdue=90)
		middle = self._queued(minutes_overdue=30)

		with patch(
			"lms.lms.language_platform.speaking_pipeline.enqueue_processing"
		) as dispatched:
			dispatch_due_retries()

		ordered = [call.args[0] for call in dispatched.call_args_list]
		mine = [name for name in ordered if name in (newest, oldest, middle)]
		self.assertEqual(
			mine,
			[oldest, middle, newest],
			"due retries were not dispatched oldest first",
		)

	def test_a_backlog_larger_than_the_batch_makes_progress(self):
		"""The starvation case the ordering exists for.

		With three due and a batch of two, an unordered query can return
		the same two every pass — so the third waits for good. Ordering
		makes each pass take the oldest that remain, so the backlog
		drains.
		"""
		oldest = self._queued(minutes_overdue=90)
		middle = self._queued(minutes_overdue=60)
		newest = self._queued(minutes_overdue=30)
		mine = {oldest, middle, newest}

		dispatched = []
		with patch(
			"lms.lms.language_platform.speaking_pipeline.SWEEP_BATCH_SIZE", 2
		), patch(
			"lms.lms.language_platform.speaking_pipeline.enqueue_processing"
		) as enqueue:
			dispatch_due_retries()
			first_pass = [c.args[0] for c in enqueue.call_args_list if c.args[0] in mine]
			dispatched.extend(first_pass)

			# The dispatcher does not change status — _claim does, in the
			# worker — so simulate the first pass having been taken up,
			# which is what lets the next pass see the remainder.
			for name in first_pass:
				frappe.db.set_value(
					"LMS Speaking Submission", name, "status", "Scoring", update_modified=False
				)
			frappe.db.commit()

			enqueue.reset_mock()
			dispatch_due_retries()
			dispatched.extend(c.args[0] for c in enqueue.call_args_list if c.args[0] in mine)

		self.assertEqual(
			dispatched[:2], [oldest, middle], "the first pass did not take the two oldest"
		)
		self.assertIn(newest, dispatched, "the newest was never reached — the backlog stalled")

	def test_submissions_due_at_the_same_moment_go_oldest_created_first(self):
		"""`retry_after asc, creation asc` — the tie-breaker is load-bearing.

		A batch of failures retried together share a `retry_after` to the
		second, so without the secondary key their order is arbitrary
		again for exactly the group most likely to exceed the batch.
		"""
		same_moment = add_to_date(now_datetime(), minutes=-10)
		first = self._queued(minutes_overdue=10)
		second = self._queued(minutes_overdue=10)
		for name in (first, second):
			frappe.db.set_value(
				"LMS Speaking Submission", name, "retry_after", same_moment, update_modified=False
			)
		frappe.db.commit()

		with patch(
			"lms.lms.language_platform.speaking_pipeline.enqueue_processing"
		) as enqueue:
			dispatch_due_retries()

		ordered = [c.args[0] for c in enqueue.call_args_list if c.args[0] in (first, second)]
		self.assertEqual(ordered, [first, second], "the earlier-created submission did not go first")

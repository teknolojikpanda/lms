# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Unit tests for DR policy (§6.1 RPO/RTO, §8.18 continuity).

The cases that matter are the ones where a naive implementation reports
green: no backup at all, a clock-skewed timestamp, and one breached check
among several healthy ones.

Pure tests — no Frappe site required:
	python -m unittest lms.tests.language_platform.test_dr_rules
"""

import unittest
from datetime import datetime, timedelta

from lms.lms.language_platform.dr_rules import (
	DR_DRILL_INTERVAL_DAYS,
	RESTORE_TEST_INTERVAL_DAYS,
	RPO_IDEAL_MINUTES,
	RPO_TARGET_MINUTES,
	STATUS_BREACH,
	STATUS_OK,
	STATUS_UNKNOWN,
	STATUS_WARNING,
	DRError,
	evaluate_overdue,
	evaluate_rpo,
	overall_status,
	rto_budget,
)

NOW = datetime(2026, 7, 26, 12, 0, 0)


class TestRPO(unittest.TestCase):
	def test_fresh_backup_is_ok(self):
		result = evaluate_rpo(NOW - timedelta(minutes=5), NOW)
		self.assertEqual(result["status"], STATUS_OK)
		self.assertEqual(result["age_minutes"], 5.0)

	def test_inside_range_but_past_ideal_warns(self):
		# 30 min: within the 1-hour RPO, past the 15-minute goal.
		self.assertEqual(evaluate_rpo(NOW - timedelta(minutes=30), NOW)["status"], STATUS_WARNING)

	def test_beyond_target_breaches(self):
		self.assertEqual(evaluate_rpo(NOW - timedelta(minutes=61), NOW)["status"], STATUS_BREACH)

	def test_boundaries(self):
		self.assertEqual(
			evaluate_rpo(NOW - timedelta(minutes=RPO_IDEAL_MINUTES), NOW)["status"], STATUS_OK
		)
		self.assertEqual(
			evaluate_rpo(NOW - timedelta(minutes=RPO_TARGET_MINUTES), NOW)["status"], STATUS_WARNING
		)

	def test_missing_backup_is_unknown_not_ok(self):
		# Silently reporting green while backups have stopped is the exact
		# failure this check exists to catch.
		result = evaluate_rpo(None, NOW)
		self.assertEqual(result["status"], STATUS_UNKNOWN)
		self.assertIsNone(result["age_minutes"])
		self.assertIn("verify the backup job", result["message"])

	def test_future_timestamp_is_unknown_not_fresh(self):
		result = evaluate_rpo(NOW + timedelta(minutes=10), NOW)
		self.assertEqual(result["status"], STATUS_UNKNOWN)
		self.assertIn("clock", result["message"].lower())

	def test_custom_targets_respected(self):
		result = evaluate_rpo(NOW - timedelta(minutes=20), NOW, target_minutes=15, ideal_minutes=5)
		self.assertEqual(result["status"], STATUS_BREACH)


class TestOverdueChecks(unittest.TestCase):
	def test_recent_is_ok(self):
		result = evaluate_overdue(NOW - timedelta(days=1), 30, "Restore test", NOW)
		self.assertEqual(result["status"], STATUS_OK)

	def test_approaching_due_date_warns(self):
		# Inside the last 20% of the interval.
		result = evaluate_overdue(NOW - timedelta(days=28), 30, "Restore test", NOW)
		self.assertEqual(result["status"], STATUS_WARNING)

	def test_overdue_breaches(self):
		result = evaluate_overdue(NOW - timedelta(days=45), 30, "Restore test", NOW)
		self.assertEqual(result["status"], STATUS_BREACH)
		self.assertIn("overdue by", result["message"])

	def test_never_performed_breaches(self):
		# §8.18 makes the restore test a MUST; "never done" is the worst
		# case, not an absence of data.
		result = evaluate_overdue(None, 30, "Restore test", NOW)
		self.assertEqual(result["status"], STATUS_BREACH)
		self.assertIn("never been recorded", result["message"])

	def test_annual_drill_interval(self):
		result = evaluate_overdue(NOW - timedelta(days=400), DR_DRILL_INTERVAL_DAYS, "DR drill", NOW)
		self.assertEqual(result["status"], STATUS_BREACH)

	def test_agreement_intervals(self):
		self.assertEqual(RESTORE_TEST_INTERVAL_DAYS, 30)
		self.assertEqual(DR_DRILL_INTERVAL_DAYS, 365)

	def test_invalid_interval_rejected(self):
		for interval in (0, -1):
			with self.assertRaises(DRError):
				evaluate_overdue(NOW, interval, "x", NOW)


class TestRTOBudget(unittest.TestCase):
	def test_phases_sum_within_target(self):
		phases = rto_budget(4)
		total = sum(phase["budget_minutes"] for phase in phases)
		self.assertLessEqual(total, 4 * 60)

	def test_every_phase_has_guidance(self):
		for phase in rto_budget():
			self.assertTrue(phase["phase"])
			self.assertTrue(phase["note"])
			self.assertGreater(phase["budget_minutes"], 0)

	def test_scales_with_target(self):
		short = sum(p["budget_minutes"] for p in rto_budget(1))
		long = sum(p["budget_minutes"] for p in rto_budget(4))
		self.assertGreater(long, short)

	def test_invalid_target_rejected(self):
		for hours in (0, -2):
			with self.assertRaises(DRError):
				rto_budget(hours)


class TestOverallStatus(unittest.TestCase):
	def test_worst_status_wins(self):
		# A breached RPO must not hide behind healthy neighbours.
		checks = [{"status": STATUS_OK}, {"status": STATUS_OK}, {"status": STATUS_BREACH}]
		self.assertEqual(overall_status(checks), STATUS_BREACH)

	def test_unknown_outranks_warning(self):
		checks = [{"status": STATUS_WARNING}, {"status": STATUS_UNKNOWN}]
		self.assertEqual(overall_status(checks), STATUS_UNKNOWN)

	def test_all_ok(self):
		self.assertEqual(overall_status([{"status": STATUS_OK}]), STATUS_OK)

	def test_empty_is_unknown(self):
		self.assertEqual(overall_status([]), STATUS_UNKNOWN)
		self.assertEqual(overall_status(None), STATUS_UNKNOWN)


if __name__ == "__main__":
	unittest.main()

# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Unit tests for the deterministic blueprint selection engine.

Covers the mandatory test scenarios of Technical Agreement v1.3 §10.1:
"question selection respects blueprint; no duplicates; deterministic
seed stored" plus retake exposure control (§4.7.2).

Pure tests — no Frappe site required:
	python -m unittest lms.tests.language_platform.test_exam_engine
"""

import unittest

from lms.lms.language_platform.exam_engine import (
	BlueprintError,
	Segment,
	derive_seed,
	map_score_to_level,
	select_questions,
)


def make_pool():
	pool = []
	for skill in ("Grammar", "Vocabulary", "Reading", "Listening"):
		for level in ("A1", "A2", "B1"):
			for i in range(5):
				pool.append(
					{
						"name": f"{skill[:3].upper()}-{level}-{i}",
						"language_skill": skill,
						"language_level": level,
						"topic": f"topic-{i % 2}",
					}
				)
	return pool


SEGMENTS = [
	Segment(count=3, skill="Grammar", level="A1"),
	Segment(count=3, skill="Vocabulary", level="A2"),
	Segment(count=2, skill="Reading"),
	Segment(count=2, skill="Listening", level="B1"),
]


class TestSelectQuestions(unittest.TestCase):
	def test_blueprint_is_respected(self):
		pool = make_pool()
		selected = select_questions(SEGMENTS, pool, seed="seed-1")
		self.assertEqual(len(selected), 10)

		by_name = {q["name"]: q for q in pool}
		grammar_a1 = [n for n in selected if by_name[n]["language_skill"] == "Grammar"
		              and by_name[n]["language_level"] == "A1"]
		vocab_a2 = [n for n in selected if by_name[n]["language_skill"] == "Vocabulary"
		            and by_name[n]["language_level"] == "A2"]
		reading = [n for n in selected if by_name[n]["language_skill"] == "Reading"]
		listening_b1 = [n for n in selected if by_name[n]["language_skill"] == "Listening"
		                and by_name[n]["language_level"] == "B1"]

		self.assertEqual(len(grammar_a1), 3)
		self.assertEqual(len(vocab_a2), 3)
		self.assertEqual(len(reading), 2)
		self.assertEqual(len(listening_b1), 2)

	def test_no_duplicates_within_attempt(self):
		selected = select_questions(SEGMENTS, make_pool(), seed="seed-2")
		self.assertEqual(len(selected), len(set(selected)))

	def test_same_seed_same_selection(self):
		a = select_questions(SEGMENTS, make_pool(), seed="stable-seed")
		b = select_questions(SEGMENTS, make_pool(), seed="stable-seed")
		self.assertEqual(a, b)

	def test_selection_independent_of_pool_order(self):
		pool = make_pool()
		a = select_questions(SEGMENTS, pool, seed="order-seed")
		b = select_questions(SEGMENTS, list(reversed(pool)), seed="order-seed")
		self.assertEqual(a, b)

	def test_different_seed_changes_selection(self):
		a = select_questions(SEGMENTS, make_pool(), seed="seed-a")
		b = select_questions(SEGMENTS, make_pool(), seed="seed-b")
		self.assertNotEqual(a, b)

	def test_exposure_control_prefers_unseen(self):
		pool = make_pool()
		segments = [Segment(count=3, skill="Grammar", level="A1")]
		first = select_questions(segments, pool, seed="attempt-1")
		second = select_questions(segments, pool, seed="attempt-2", seen=set(first))
		# 5 Grammar/A1 questions exist, 3 were seen — the 2 unseen ones
		# must both be picked before any repeat.
		unseen_picked = [n for n in second if n not in first]
		self.assertEqual(len(unseen_picked), 2)

	def test_exposure_control_still_fills_small_pool(self):
		pool = make_pool()
		segments = [Segment(count=4, skill="Grammar", level="A1")]
		seen = {q["name"] for q in pool if q["language_skill"] == "Grammar"}
		selected = select_questions(segments, pool, seed="x", seen=seen)
		self.assertEqual(len(selected), 4)  # repeats allowed rather than failing

	def test_insufficient_pool_raises(self):
		segments = [Segment(count=99, skill="Grammar", level="A1")]
		with self.assertRaises(BlueprintError):
			select_questions(segments, make_pool(), seed="x")

	def test_duplicate_names_in_pool_rejected(self):
		pool = make_pool() + [make_pool()[0]]
		with self.assertRaises(BlueprintError):
			select_questions(SEGMENTS, pool, seed="x")

	def test_extra_count_distributed_without_duplicates(self):
		selected = select_questions(SEGMENTS, make_pool(), seed="x", extra_count=5)
		self.assertEqual(len(selected), 15)
		self.assertEqual(len(set(selected)), 15)

	def test_topic_filter(self):
		pool = make_pool()
		segments = [Segment(count=2, skill="Grammar", topic="topic-0")]
		selected = select_questions(segments, pool, seed="x")
		by_name = {q["name"]: q for q in pool}
		for name in selected:
			self.assertEqual(by_name[name]["topic"], "topic-0")


class TestDeriveSeed(unittest.TestCase):
	def test_stable_and_distinct(self):
		self.assertEqual(derive_seed("u@x.com", "PLB-1", 1), derive_seed("u@x.com", "PLB-1", 1))
		self.assertNotEqual(derive_seed("u@x.com", "PLB-1", 1), derive_seed("u@x.com", "PLB-1", 2))


class TestLevelMapping(unittest.TestCase):
	MAPPING = [
		{"min_score": 0, "level": "A1"},
		{"min_score": 30, "level": "A2"},
		{"min_score": 50, "level": "B1"},
		{"min_score": 70, "level": "B2"},
		{"min_score": 85, "level": "C1"},
	]

	def test_thresholds(self):
		self.assertEqual(map_score_to_level(0, self.MAPPING), "A1")
		self.assertEqual(map_score_to_level(29.9, self.MAPPING), "A1")
		self.assertEqual(map_score_to_level(30, self.MAPPING), "A2")
		self.assertEqual(map_score_to_level(69.9, self.MAPPING), "B1")
		self.assertEqual(map_score_to_level(100, self.MAPPING), "C1")

	def test_below_all_thresholds_returns_none(self):
		mapping = [{"min_score": 50, "level": "B1"}]
		self.assertIsNone(map_score_to_level(10, mapping))


if __name__ == "__main__":
	unittest.main()

# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Unit tests for the deterministic blueprint selection engine.

Covers the mandatory test scenarios of Technical Agreement v1.3 §10.1:
"question selection respects blueprint; no duplicates; deterministic
seed stored" plus retake exposure control (§4.7.2).

Pure tests — no Frappe site required:
	python -m unittest lms.tests.language_platform.test_exam_engine
"""

import random
import sys
import unittest

from lms.lms.language_platform.exam_engine import (
	BlueprintError,
	Segment,
	_assign_slots,
	derive_seed,
	map_score_to_level,
	select_questions,
)


def legacy_select(segments, pool, seed, seen=frozenset()):
	"""The pre-matching algorithm, as an independent oracle.

	Reimplemented here rather than imported, so this keeps testing what
	"fill the segments in configured order" produced even if the
	production code stops containing that shape. Returns ``None`` where
	the old code raised.
	"""
	rng = random.Random(seed)
	ordered = sorted(pool, key=lambda q: str(q["name"]))
	selected: list[str] = []
	chosen: set[str] = set()

	for segment in segments:
		if segment.count <= 0:
			continue
		candidates = [q for q in ordered if segment.matches(q)]
		available = [q for q in candidates if str(q["name"]) not in chosen]
		if len(available) < segment.count:
			return None
		unseen = [q for q in available if str(q["name"]) not in seen]
		exposed = [q for q in available if str(q["name"]) in seen]
		rng.shuffle(unseen)
		rng.shuffle(exposed)
		for q in (unseen + exposed)[: segment.count]:
			selected.append(str(q["name"]))
			chosen.add(str(q["name"]))

	return selected


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
		"""One row asking for more than exists: say which, and by how much.

		This is the refusal an administrator can act on directly, so the
		counts in it are part of the behaviour, not decoration.
		"""
		segments = [Segment(count=99, skill="Grammar", level="A1")]
		with self.assertRaises(BlueprintError) as caught:
			select_questions(segments, make_pool(), seed="x")

		message = str(caught.exception)
		self.assertIn("Grammar/A1", message, f"the failing segment is not named: {message}")
		self.assertIn("99 question(s)", message, f"what was asked for is not stated: {message}")
		self.assertIn("only 5", message, f"what exists is not stated: {message}")

	def test_duplicate_names_in_pool_rejected(self):
		pool = make_pool() + [make_pool()[0]]
		with self.assertRaises(BlueprintError):
			select_questions(SEGMENTS, pool, seed="x")

	def test_overlapping_segments_do_not_fail_on_unlucky_seeds(self):
		"""A satisfiable pool must never intermittently refuse to start.

		A broad Grammar segment and a Grammar/A1 segment draw on the same
		questions. Filling them in configured order let the broad one
		consume the only A1 question on about half the seeds, so the same
		blueprint and the same pool refused some students and not others
		— and refused the same student on one attempt number but not the
		next.
		"""
		segments = [
			Segment(count=1, skill="Grammar"),
			Segment(count=1, skill="Grammar", level="A1"),
		]
		pool = [
			{"name": "G-A1", "language_skill": "Grammar", "language_level": "A1", "topic": "t"},
			{"name": "G-A2", "language_skill": "Grammar", "language_level": "A2", "topic": "t"},
		]

		for i in range(200):
			with self.subTest(seed=i):
				selected = select_questions(segments, pool, seed=f"overlap-{i}")
				# Only one assignment works: the constrained segment must
				# get A1, so the broad one has to take A2.
				self.assertEqual(selected, ["G-A2", "G-A1"])

	def test_a_chain_of_reassignments_is_followed(self):
		"""Picking the most constrained segment first is not enough.

		Here every segment has a free choice in isolation and only one
		assignment satisfies all three, reachable only by moving an
		already-placed segment onto a different question and moving the
		one it displaces in turn.
		"""
		segments = [
			Segment(count=1, skill="Grammar"),
			Segment(count=1, level="A1"),
			Segment(count=1, skill="Grammar", level="A2"),
		]
		pool = [
			{"name": "GR-A1", "language_skill": "Grammar", "language_level": "A1", "topic": "t"},
			{"name": "GR-A2", "language_skill": "Grammar", "language_level": "A2", "topic": "t"},
			{"name": "VO-A1", "language_skill": "Vocabulary", "language_level": "A1", "topic": "t"},
		]

		for i in range(200):
			with self.subTest(seed=i):
				self.assertEqual(
					select_questions(segments, pool, seed=f"chain-{i}"),
					["GR-A1", "VO-A1", "GR-A2"],
				)

	def test_an_impossible_blueprint_is_still_refused(self):
		"""Matching must not paper over a pool that genuinely cannot work.

		Both rows match on their own — there is one Grammar/A1 question
		and each wants one — so the shortage only exists between them.
		That is the refusal that has to name the row and the shortfall,
		because a per-segment count cannot explain it.
		"""
		segments = [
			Segment(count=1, skill="Grammar", level="A1"),
			Segment(count=1, skill="Grammar", level="A1"),
		]
		pool = [
			{"name": "GR-A1", "language_skill": "Grammar", "language_level": "A1", "topic": "t"},
			{"name": "GR-A2", "language_skill": "Grammar", "language_level": "A2", "topic": "t"},
		]

		with self.assertRaises(BlueprintError) as caught:
			select_questions(segments, pool, seed="x")

		message = str(caught.exception)
		self.assertIn("Grammar/A1", message, f"the failing segment is not named: {message}")
		self.assertIn("short by 1", message, f"the shortfall is not quantified: {message}")

	def test_a_refusal_names_the_segment_that_could_not_be_filled(self):
		"""An administrator has to be told which row to fix.

		Each segment here has enough matches on its own; they are only
		short between them, which is exactly the case a per-segment count
		cannot explain.
		"""
		segments = [
			Segment(count=2, skill="Grammar"),
			Segment(count=1, skill="Grammar", level="A1"),
		]
		pool = [
			{"name": "GR-A1", "language_skill": "Grammar", "language_level": "A1", "topic": "t"},
			{"name": "GR-A2", "language_skill": "Grammar", "language_level": "A2", "topic": "t"},
		]

		with self.assertRaises(BlueprintError) as caught:
			select_questions(segments, pool, seed="x")

		message = str(caught.exception)
		self.assertIn("Grammar/A1", message, f"the failing segment is not named: {message}")
		self.assertIn("overlap", message, f"the reason is not explained: {message}")

	def test_a_displaced_segment_still_prefers_an_unseen_question(self):
		"""Making room for a constrained segment must not cost exposure control.

		The broad segment has to give up A1 so the A1 segment can have
		it. Where it goes instead is the test: A2 is unseen and B1 is
		not, so a retake must land on A2.
		"""
		segments = [
			Segment(count=1, skill="Grammar"),
			Segment(count=1, skill="Grammar", level="A1"),
		]
		pool = [
			{"name": "GR-A1", "language_skill": "Grammar", "language_level": "A1", "topic": "t"},
			{"name": "GR-A2", "language_skill": "Grammar", "language_level": "A2", "topic": "t"},
			{"name": "GR-B1", "language_skill": "Grammar", "language_level": "B1", "topic": "t"},
		]

		for i in range(200):
			with self.subTest(seed=i):
				selected = select_questions(
					segments, pool, seed=f"exp-{i}", seen={"GR-B1"}
				)
				self.assertEqual(selected, ["GR-A2", "GR-A1"])

	def test_a_question_is_never_reused_across_overlapping_segments(self):
		"""§4.7.2: a question never repeats within one attempt.

		Across many seeds, not one: which slots displace which depends on
		the shuffle, so a single seed exercises a single arrangement.
		"""
		segments = [
			Segment(count=2, skill="Grammar"),
			Segment(count=2, skill="Grammar", level="A1"),
			Segment(count=1, level="A1"),
		]

		for i in range(200):
			with self.subTest(seed=i):
				selected = select_questions(segments, make_pool(), seed=f"reuse-{i}")

				self.assertEqual(len(selected), 5)
				self.assertEqual(
					len(set(selected)), 5, "a question was placed in two segments"
				)

	# Two identical broad segments over four Grammar questions: nothing
	# here is hard to satisfy, which is the point. The old algorithm
	# always managed it, so every one of these seeds has a recorded answer
	# that has to keep re-deriving.
	SATISFIABLE_SEGMENTS = [
		Segment(count=1, skill="Grammar"),
		Segment(count=1, skill="Grammar"),
	]
	FOUR_GRAMMAR = [
		{"name": "G-A1", "language_skill": "Grammar", "language_level": "A1", "topic": "t"},
		{"name": "G-A2", "language_skill": "Grammar", "language_level": "A2", "topic": "t"},
		{"name": "G-B1", "language_skill": "Grammar", "language_level": "B1", "topic": "t"},
		{"name": "G-B2", "language_skill": "Grammar", "language_level": "B2", "topic": "t"},
	]

	def test_a_seed_the_old_order_could_satisfy_still_re_derives(self):
		"""§4.7.2 persists the seed so an attempt can be re-derived for audit.

		Matching shuffles each segment's whole match list; filling in
		order shuffled only what earlier segments had left. Different
		lengths mean different rng draws, so from the first overlapping
		segment onwards the streams diverge and the same seed produces a
		different question set — silently, for attempts already sat and
		recorded. An audit trail that no longer reproduces has stopped
		meaning anything.
		"""
		preserved = 0
		for i in range(300):
			seed = f"audit-{i}"
			legacy = legacy_select(self.SATISFIABLE_SEGMENTS, self.FOUR_GRAMMAR, seed)
			if legacy is None:
				continue
			preserved += 1
			with self.subTest(seed=seed):
				self.assertEqual(
					select_questions(self.SATISFIABLE_SEGMENTS, self.FOUR_GRAMMAR, seed),
					legacy,
					"an attempt recorded under this seed no longer re-derives",
				)
		self.assertGreater(preserved, 0, "no seed exercised the preserved path")

	def test_exposure_control_re_derives_too(self):
		"""Retakes are the case where the two shuffles differ most."""
		seen = {"G-B1", "G-B2"}
		preserved = 0
		for i in range(200):
			seed = f"retake-{i}"
			legacy = legacy_select(
				self.SATISFIABLE_SEGMENTS, self.FOUR_GRAMMAR, seed, seen=seen
			)
			if legacy is None:
				continue
			preserved += 1
			with self.subTest(seed=seed):
				self.assertEqual(
					select_questions(
						self.SATISFIABLE_SEGMENTS, self.FOUR_GRAMMAR, seed, seen=seen
					),
					legacy,
				)
		self.assertGreater(preserved, 0, "no seed exercised the preserved path")

	def test_extras_re_derive_as_well(self):
		"""Pass 2 draws from the same rng, so it moves if pass 1 does."""
		segments = [Segment(count=1, skill="Grammar")]
		for i in range(100):
			seed = f"extra-{i}"
			legacy = legacy_select(segments, self.FOUR_GRAMMAR, seed)
			self.assertIsNotNone(legacy)
			with self.subTest(seed=seed):
				selected = select_questions(
					segments, self.FOUR_GRAMMAR, seed, extra_count=2
				)
				self.assertEqual(selected[:1], legacy, "the blueprint pick moved")
				self.assertEqual(len(set(selected)), 3)

	def test_the_fallback_is_deterministic_in_the_seed(self):
		"""When the old order fails, the answer must still be reproducible.

		The fallback runs on a fresh rng rather than whatever the failed
		attempt left behind, so it depends on the seed and not on how far
		the first pass got before giving up.
		"""
		segments = [
			Segment(count=1, skill="Grammar"),
			Segment(count=1, skill="Grammar", level="A1"),
		]
		pool = [
			{"name": "G-A1", "language_skill": "Grammar", "language_level": "A1", "topic": "t"},
			{"name": "G-A2", "language_skill": "Grammar", "language_level": "A2", "topic": "t"},
		]
		fell_back = 0
		for i in range(200):
			seed = f"fallback-{i}"
			if legacy_select(segments, pool, seed) is not None:
				continue
			fell_back += 1
			with self.subTest(seed=seed):
				first = select_questions(segments, pool, seed)
				self.assertEqual(first, select_questions(segments, pool, seed))
		self.assertGreater(fell_back, 0, "no seed exercised the fallback")

	def test_the_matcher_survives_a_chain_longer_than_the_recursion_limit(self):
		"""A blueprint is operator data; it must not be able to blow the stack.

		Each slot here accepts its own question and the next one, so the
		greedy pass fills them in order. The final slot then wants the
		first question, and satisfying it means displacing every slot in
		turn — one augmenting chain as long as the blueprint.
		"""
		depth = sys.getrecursionlimit() + 100
		candidates = [[f"q{i}", f"q{i + 1}"] for i in range(depth)]
		candidates.append(["q0"])

		assignment: dict[int, str] = {}
		_assign_slots(candidates, assignment, {})

		self.assertEqual(len(assignment), depth + 1, "the chain was not followed to the end")
		self.assertEqual(
			len(set(assignment.values())), depth + 1, "a question was placed twice"
		)

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

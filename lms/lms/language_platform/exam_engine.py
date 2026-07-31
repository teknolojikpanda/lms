# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Deterministic, blueprint-driven question selection.

Implements Technical Agreement v1.3 §4.7.2 (MUST):

- the exam/placement blueprint is satisfied first: every (skill, level[, topic])
  segment receives its configured number of questions;
- remaining questions (if a total larger than the blueprint sum is requested)
  are distributed from the leftover pool;
- a question never repeats within one attempt;
- on retakes, previously seen questions are avoided when enough unseen
  candidates exist (exposure control);
- selection is reproducible: the RNG is seeded with a persisted seed so the
  exact question set can be re-derived later for audit.

This module is intentionally free of ``frappe`` imports so the selection rules
can be unit-tested without a running site.
"""

from __future__ import annotations

import hashlib
import random
from collections import Counter
from dataclasses import dataclass

__all__ = [
	"BlueprintError",
	"Segment",
	"derive_seed",
	"select_questions",
	"map_score_to_level",
]


class BlueprintError(Exception):
	"""Raised when the question pool cannot satisfy the blueprint."""


@dataclass(frozen=True)
class Segment:
	"""One blueprint row: *count* questions matching the given filters."""

	count: int
	skill: str | None = None
	level: str | None = None
	topic: str | None = None

	def matches(self, question: dict) -> bool:
		if self.skill and (question.get("language_skill") or "") != self.skill:
			return False
		if self.level and (question.get("language_level") or "") != self.level:
			return False
		if self.topic and (question.get("topic") or "") != self.topic:
			return False
		return True

	def label(self) -> str:
		parts = [p for p in (self.skill, self.level, self.topic) if p]
		return "/".join(parts) or "any"


def derive_seed(*parts: object) -> str:
	"""Build a stable seed string from identifying parts (user, exam, attempt no)."""
	raw = "|".join(str(p) for p in parts)
	return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _assign_slots(
	candidates: list[list[str]],
	assignment: dict[int, str],
	taken: dict[str, int],
) -> None:
	"""Place as many slots as possible, one question each (Kuhn's algorithm).

	``candidates[slot]`` is that slot's question names in preference
	order. ``assignment`` and ``taken`` are mutated in place and may
	already hold placements from an earlier call, which this extends.

	The greedy pass is not merely an optimisation. Trying the augmenting
	search first would reassign an already-placed slot whenever a later
	slot wanted the same question, so a blueprint with no competition
	between its segments would come out in a different order than it
	always has. Greedy leaves those alone and the search only runs where
	the straightforward answer failed.
	"""

	def augment(slot: int, visited: set[str]) -> bool:
		for name in candidates[slot]:
			if name in visited:
				continue
			visited.add(name)
			holder = taken.get(name)
			if holder is None or augment(holder, visited):
				taken[name] = slot
				assignment[slot] = name
				return True
		return False

	for slot in range(len(candidates)):
		if slot in assignment:
			continue
		for name in candidates[slot]:
			if name not in taken:
				taken[name] = slot
				assignment[slot] = name
				break
		else:
			augment(slot, set())


def select_questions(
	segments: list[Segment],
	pool: list[dict],
	seed: str,
	seen: set[str] | None = None,
	extra_count: int = 0,
) -> list[str]:
	"""Return an ordered list of question names satisfying the blueprint.

	``pool`` rows need ``name`` plus the metadata fields used by the segments
	(``language_skill``, ``language_level``, ``topic``). ``seen`` holds question
	names the student met in earlier attempts; they are deprioritised, not
	excluded, so a small pool still fills the blueprint (§4.7.2 "minimuma
	indir").
	"""
	seen = seen or set()
	rng = random.Random(seed)

	# Determinism must not depend on caller's pool ordering.
	ordered_pool = sorted(pool, key=lambda q: str(q.get("name")))
	names = [str(q.get("name")) for q in ordered_pool]
	if len(set(names)) != len(names):
		raise BlueprintError("Question pool contains duplicate names.")

	# Pass 1 — fill every blueprint segment.
	#
	# Segments are not filled one after another. Overlapping segments
	# compete for the same questions, and taking them in configured order
	# rejects pools that can in fact be satisfied: with a broad
	# one-question Grammar segment followed by a one-question Grammar/A1
	# segment, the broad one consumes the only A1 question on some seeds
	# and the test refuses to start — intermittently, for the same
	# blueprint and pool, depending on the student and attempt number.
	#
	# So this is a bipartite matching: every segment is expanded into one
	# slot per question it needs, and slots are matched against questions
	# they accept. A maximum matching fills every slot whenever any
	# assignment can.
	slot_segment: list[int] = []
	unseen_only: list[list[str]] = []
	all_candidates: list[list[str]] = []

	for index, segment in enumerate(segments):
		if segment.count <= 0:
			continue

		matches = [str(q["name"]) for q in ordered_pool if segment.matches(q)]
		if len(matches) < segment.count:
			# Nothing to negotiate: this segment cannot be filled by any
			# assignment. Worth saying plainly rather than as part of a
			# matching failure.
			raise BlueprintError(
				f"Segment '{segment.label()}' needs {segment.count} question(s) "
				f"but only {len(matches)} are available."
			)

		# Exposure control: unseen questions first; deterministic shuffle
		# within each exposure class. Shuffled once per segment rather
		# than once per slot, so the rng is consumed exactly as it was
		# before slots existed.
		unseen = [name for name in matches if name not in seen]
		exposed = [name for name in matches if name in seen]
		rng.shuffle(unseen)
		rng.shuffle(exposed)

		for _ in range(segment.count):
			slot_segment.append(index)
			unseen_only.append(unseen)
			all_candidates.append(unseen + exposed)

	assignment: dict[int, str] = {}
	taken: dict[str, int] = {}
	# Unseen questions first, as a whole: matching against them alone
	# places as many as can ever be placed at once, and the second pass
	# can only add questions, never unmatch one. Running a single pass
	# over the combined list would let a slot fall back to a seen
	# question that a later reassignment made unnecessary.
	_assign_slots(unseen_only, assignment, taken)
	_assign_slots(all_candidates, assignment, taken)

	unfilled = Counter(slot_segment[slot] for slot in range(len(slot_segment)) if slot not in assignment)
	if unfilled:
		detail = "; ".join(
			f"'{segments[index].label()}' short by {count}"
			for index, count in sorted(unfilled.items())
		)
		raise BlueprintError(
			"No assignment of questions satisfies every segment without reusing "
			f"one ({detail}). Segments that overlap draw on the same questions, "
			"so each having enough matches on its own is not sufficient."
		)

	selected: list[str] = [assignment[slot] for slot in range(len(slot_segment))]
	selected_set: set[str] = set(selected)

	# Pass 2 — distribute any requested extras from the whole pool.
	if extra_count > 0:
		available = [q for q in ordered_pool if str(q["name"]) not in selected_set]
		if len(available) < extra_count:
			raise BlueprintError(
				f"Segment 'extra' needs {extra_count} question(s) "
				f"but only {len(available)} are available."
			)
		unseen = [q for q in available if str(q["name"]) not in seen]
		exposed = [q for q in available if str(q["name"]) in seen]
		rng.shuffle(unseen)
		rng.shuffle(exposed)
		for q in (unseen + exposed)[:extra_count]:
			name = str(q["name"])
			selected.append(name)
			selected_set.add(name)

	return selected


def map_score_to_level(score_percentage: float, mapping: list[dict]) -> str | None:
	"""Map a 0-100 score to a level using configurable thresholds (§4.6.2).

	``mapping`` rows: ``{"min_score": <number>, "level": <str>}``. The row with
	the highest ``min_score`` that is <= score wins. Returns ``None`` when the
	score is below every threshold (caller decides the floor level).
	"""
	best: dict | None = None
	for row in mapping:
		min_score = float(row.get("min_score") or 0)
		if score_percentage >= min_score and (best is None or min_score > float(best["min_score"])):
			best = {"min_score": min_score, "level": row.get("level")}
	return best["level"] if best else None

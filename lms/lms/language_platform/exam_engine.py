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

	selected: list[str] = []
	selected_set: set[str] = set()

	def take(candidates: list[dict], count: int, segment_label: str) -> None:
		available = [q for q in candidates if str(q["name"]) not in selected_set]
		if len(available) < count:
			raise BlueprintError(
				f"Segment '{segment_label}' needs {count} question(s) "
				f"but only {len(available)} are available."
			)
		# Exposure control: unseen questions first; deterministic shuffle
		# within each exposure class.
		unseen = [q for q in available if str(q["name"]) not in seen]
		exposed = [q for q in available if str(q["name"]) in seen]
		rng.shuffle(unseen)
		rng.shuffle(exposed)
		for q in (unseen + exposed)[:count]:
			name = str(q["name"])
			selected.append(name)
			selected_set.add(name)

	# Pass 1 — fill every blueprint segment.
	for segment in segments:
		if segment.count <= 0:
			continue
		take([q for q in ordered_pool if segment.matches(q)], segment.count, segment.label())

	# Pass 2 — distribute any requested extras from the whole pool.
	if extra_count > 0:
		take(ordered_pool, extra_count, "extra")

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

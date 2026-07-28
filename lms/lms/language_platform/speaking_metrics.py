# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Objective speech metrics computed from a transcript (§4.9.1 step 3).

Pure module — no ``frappe`` imports — so the numbers feeding the AI rubric
score are unit-testable and reproducible. Subjective rubric dimensions come
from the scoring provider; these metrics are the deterministic part.
"""

from __future__ import annotations

import re

FILLER_WORDS = {
	"uh",
	"um",
	"er",
	"erm",
	"hmm",
	"uhm",
	"like",
	"basically",
	"actually",
	"literally",
}

_WORD_RE = re.compile(r"[a-zA-Z']+")


def tokenize(transcript: str) -> list[str]:
	return [w.lower() for w in _WORD_RE.findall(transcript or "")]


def compute_metrics(transcript: str, duration_seconds: float) -> dict:
	"""Return word_count, wpm, lexical_diversity (TTR) and filler_ratio.

	``duration_seconds`` <= 0 yields wpm 0 rather than raising — a failed
	duration probe must not fail the whole pipeline.
	"""
	words = tokenize(transcript)
	word_count = len(words)
	minutes = duration_seconds / 60 if duration_seconds and duration_seconds > 0 else 0

	wpm = round(word_count / minutes, 1) if minutes else 0.0
	lexical_diversity = round(len(set(words)) / word_count, 3) if word_count else 0.0
	filler_count = sum(1 for w in words if w in FILLER_WORDS)
	filler_ratio = round(filler_count / word_count, 3) if word_count else 0.0

	return {
		"word_count": word_count,
		"wpm": wpm,
		"lexical_diversity": lexical_diversity,
		"filler_ratio": filler_ratio,
	}

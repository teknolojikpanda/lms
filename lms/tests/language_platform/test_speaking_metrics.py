# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Unit tests for objective speaking metrics (§4.9.1 step 3).

Pure tests — no Frappe site required:
	python -m unittest lms.tests.language_platform.test_speaking_metrics
"""

import unittest

from lms.lms.language_platform.speaking_metrics import compute_metrics, tokenize


class TestSpeakingMetrics(unittest.TestCase):
	def test_word_count_and_wpm(self):
		transcript = " ".join(["word"] * 120)
		metrics = compute_metrics(transcript, duration_seconds=60)
		self.assertEqual(metrics["word_count"], 120)
		self.assertEqual(metrics["wpm"], 120.0)

	def test_lexical_diversity(self):
		metrics = compute_metrics("one two three four", duration_seconds=10)
		self.assertEqual(metrics["lexical_diversity"], 1.0)

		metrics = compute_metrics("one one one one", duration_seconds=10)
		self.assertEqual(metrics["lexical_diversity"], 0.25)

	def test_filler_ratio(self):
		metrics = compute_metrics("um I uh think basically yes", duration_seconds=10)
		# fillers: um, uh, basically → 3 of 6 words
		self.assertEqual(metrics["filler_ratio"], 0.5)

	def test_zero_duration_does_not_crash(self):
		metrics = compute_metrics("hello world", duration_seconds=0)
		self.assertEqual(metrics["wpm"], 0.0)
		self.assertEqual(metrics["word_count"], 2)

	def test_empty_transcript(self):
		metrics = compute_metrics("", duration_seconds=30)
		self.assertEqual(metrics["word_count"], 0)
		self.assertEqual(metrics["lexical_diversity"], 0.0)
		self.assertEqual(metrics["filler_ratio"], 0.0)

	def test_tokenize_handles_punctuation_and_case(self):
		self.assertEqual(tokenize("Hello, WORLD! It's fine."), ["hello", "world", "it's", "fine"])


if __name__ == "__main__":
	unittest.main()

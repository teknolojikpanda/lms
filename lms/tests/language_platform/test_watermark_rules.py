# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Unit tests for session watermarking (§1.1 copying deterrence).

The traceability and non-disclosure properties are the point of the
feature, so they are tested as such rather than as formatting.

Pure tests — no Frappe site required:
	python -m unittest lms.tests.language_platform.test_watermark_rules
"""

import unittest

from lms.lms.language_platform.watermark_rules import (
	CODE_ALPHABET,
	CODE_LENGTH,
	POSITION_MAX_PERCENT,
	POSITION_MIN_PERCENT,
	WatermarkError,
	build_overlay_config,
	format_code,
	generate_session_code,
	normalize_code,
	position_schedule,
)


class TestSessionCode(unittest.TestCase):
	def test_deterministic_for_a_seed(self):
		# A viewer who reloads must keep one identity.
		self.assertEqual(generate_session_code("session-1"), generate_session_code("session-1"))

	def test_distinct_per_session(self):
		self.assertNotEqual(generate_session_code("session-1"), generate_session_code("session-2"))

	def test_uses_only_unambiguous_characters(self):
		# A code read off a blurry recording is useless if O/0 or I/1 are
		# guesswork.
		code = generate_session_code("session-1")
		for char in code:
			self.assertIn(char, CODE_ALPHABET)
		for confusable in "O0I1LUV":
			self.assertNotIn(confusable, CODE_ALPHABET)

	def test_fixed_length(self):
		self.assertEqual(len(generate_session_code("x")), CODE_LENGTH)

	def test_does_not_disclose_the_seed(self):
		# The overlay is visible to anyone watching over a shoulder, so it
		# must not carry the viewer's identity (§6.4 data minimisation).
		code = generate_session_code("student@school.edu|LESSON-1")
		self.assertNotIn("student", code.lower())
		self.assertNotIn("school", code.lower())
		self.assertNotIn("lesson", code.lower())

	def test_requires_a_seed(self):
		for seed in ("", None):
			with self.assertRaises(WatermarkError):
				generate_session_code(seed)

	def test_codes_are_well_distributed(self):
		# Collisions would make a leak trace to two viewers at once.
		codes = {generate_session_code(f"session-{i}") for i in range(2000)}
		self.assertEqual(len(codes), 2000)


class TestCodeFormatting(unittest.TestCase):
	def test_grouped_for_reading(self):
		code = generate_session_code("s")
		self.assertEqual(format_code(code), f"{code[:4]}-{code[4:]}")

	def test_rejects_invalid_code(self):
		for bad in ("SHORT", "", "AAAA-BBBB", "AAAAAAA0"):
			with self.assertRaises(WatermarkError):
				format_code(bad)


class TestCodeNormalization(unittest.TestCase):
	"""Someone reporting a leak types what they see."""

	def test_accepts_the_displayed_form(self):
		code = generate_session_code("s")
		self.assertEqual(normalize_code(format_code(code)), code)

	def test_tolerates_case_and_spacing(self):
		code = generate_session_code("s")
		display = format_code(code)
		for variant in (display.lower(), display.replace("-", " "), f"  {display}  "):
			self.assertEqual(normalize_code(variant), code)

	def test_rejects_rather_than_guesses(self):
		for bad in ("NOTACODE!", "12", "", "AAAA-BBB"):
			with self.assertRaises(WatermarkError):
				normalize_code(bad)

	def test_rejects_non_string(self):
		for bad in (None, 123, []):
			with self.assertRaises(WatermarkError):
				normalize_code(bad)


class TestPositionSchedule(unittest.TestCase):
	def test_deterministic_for_a_code(self):
		code = generate_session_code("s")
		self.assertEqual(position_schedule(code), position_schedule(code))

	def test_differs_between_codes(self):
		a = position_schedule(generate_session_code("a"))
		b = position_schedule(generate_session_code("b"))
		self.assertNotEqual(a, b)

	def test_positions_stay_inside_the_safe_band(self):
		# Outside this band the mark can be cropped away without losing
		# picture, which defeats the whole mechanism.
		for position in position_schedule(generate_session_code("s"), steps=40):
			self.assertGreaterEqual(position["x"], POSITION_MIN_PERCENT)
			self.assertLess(position["x"], POSITION_MAX_PERCENT)
			self.assertGreaterEqual(position["y"], POSITION_MIN_PERCENT)
			self.assertLess(position["y"], POSITION_MAX_PERCENT)

	def test_mark_actually_moves(self):
		positions = position_schedule(generate_session_code("s"), steps=12)
		self.assertGreater(len({(p["x"], p["y"]) for p in positions}), 1)

	def test_rejects_non_positive_steps(self):
		for steps in (0, -1):
			with self.assertRaises(WatermarkError):
				position_schedule(generate_session_code("s"), steps=steps)


class TestOverlayConfig(unittest.TestCase):
	def test_includes_display_and_schedule(self):
		config = build_overlay_config(generate_session_code("s"), label_prefix="ID")
		self.assertTrue(config["display"].startswith("ID "))
		self.assertTrue(config["positions"])

	def test_opacity_is_clamped(self):
		# Too faint is unreadable in a recording; too strong and the lesson
		# is unpleasant to watch.
		self.assertEqual(build_overlay_config(generate_session_code("s"), opacity=0.001)["opacity"], 0.15)
		self.assertEqual(build_overlay_config(generate_session_code("s"), opacity=5)["opacity"], 0.8)

	def test_move_interval_is_clamped(self):
		code = generate_session_code("s")
		self.assertEqual(build_overlay_config(code, move_interval_seconds=1)["move_interval_seconds"], 5)
		self.assertEqual(
			build_overlay_config(code, move_interval_seconds=99999)["move_interval_seconds"], 300
		)

	def test_no_prefix_leaves_a_clean_label(self):
		config = build_overlay_config(generate_session_code("s"))
		self.assertFalse(config["display"].startswith(" "))

	def test_rejects_invalid_code(self):
		with self.assertRaises(WatermarkError):
			build_overlay_config("nope")


if __name__ == "__main__":
	unittest.main()

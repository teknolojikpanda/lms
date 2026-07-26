# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Unit tests for accessibility policy (§6.3, Ek-4.1).

The contrast tests **compute** ratios against the WCAG 2.1 formula rather
than asserting a palette is fine. A colour changed without meeting the
threshold fails here instead of surfacing in an audit.

Pure tests — no Frappe site required:
	python -m unittest lms.tests.language_platform.test_accessibility_rules
"""

import unittest

from lms.lms.language_platform.accessibility_rules import (
	AA_LARGE_TEXT,
	AA_NORMAL_TEXT,
	AAA_NORMAL_TEXT,
	DEFAULT_FONT_STEP,
	FONT_STEPS,
	HIGH_CONTRAST_PAIRS,
	HIGH_CONTRAST_PALETTE,
	MIN_TARGET_PX,
	WHITEBOARD_TARGET_PX,
	AccessibilityError,
	audit_palette,
	contrast_ratio,
	default_preferences,
	font_scale_css,
	meets_wcag,
	parse_hex,
	relative_luminance,
	target_size_for,
	validate_font_step,
	validate_preferences,
)


class TestContrastMaths(unittest.TestCase):
	"""Verified against the reference values in WCAG 2.1."""

	def test_black_on_white_is_maximum(self):
		self.assertEqual(contrast_ratio("#000000", "#ffffff"), 21.0)

	def test_identical_colours_have_no_contrast(self):
		self.assertEqual(contrast_ratio("#777777", "#777777"), 1.0)

	def test_order_does_not_matter(self):
		self.assertEqual(
			contrast_ratio("#111111", "#ffffff"), contrast_ratio("#ffffff", "#111111")
		)

	def test_luminance_endpoints(self):
		self.assertAlmostEqual(relative_luminance("#000000"), 0.0, places=4)
		self.assertAlmostEqual(relative_luminance("#ffffff"), 1.0, places=4)

	def test_channels_are_linearised_not_averaged(self):
		# Mid grey has luminance ~0.216, not 0.5. Averaging raw sRGB is the
		# common shortcut and it passes palettes that genuinely fail.
		self.assertAlmostEqual(relative_luminance("#808080"), 0.2159, places=3)

	def test_known_pair(self):
		# #767676 on white is the canonical "just passes AA" grey.
		self.assertGreaterEqual(contrast_ratio("#767676", "#ffffff"), AA_NORMAL_TEXT)
		self.assertLess(contrast_ratio("#777777", "#ffffff"), 4.6)

	def test_shorthand_hex(self):
		self.assertEqual(contrast_ratio("#fff", "#000"), 21.0)

	def test_hex_without_hash(self):
		self.assertEqual(parse_hex("ffffff"), (255, 255, 255))

	def test_invalid_colour_rejected(self):
		for value in ("", None, "#12", "#zzzzzz", "rgb(0,0,0)", 123):
			with self.assertRaises(AccessibilityError):
				parse_hex(value)


class TestWCAGThresholds(unittest.TestCase):
	def test_aa_normal_text(self):
		self.assertTrue(meets_wcag("#595959", "#ffffff"))  # ~7:1
		self.assertFalse(meets_wcag("#999999", "#ffffff"))  # ~2.8:1

	def test_large_text_has_a_lower_bar(self):
		# ~3.5:1 fails as body text but passes as large text.
		self.assertFalse(meets_wcag("#8c8c8c", "#ffffff", large_text=False))
		self.assertTrue(meets_wcag("#8c8c8c", "#ffffff", large_text=True))

	def test_aaa_is_stricter_than_aa(self):
		# ~5:1 clears AA, not AAA.
		self.assertTrue(meets_wcag("#6f6f6f", "#ffffff", level="AA"))
		self.assertFalse(meets_wcag("#6f6f6f", "#ffffff", level="AAA"))

	def test_thresholds_match_the_standard(self):
		self.assertEqual(AA_NORMAL_TEXT, 4.5)
		self.assertEqual(AA_LARGE_TEXT, 3.0)
		self.assertEqual(AAA_NORMAL_TEXT, 7.0)


class TestHighContrastPalette(unittest.TestCase):
	"""The palette shipped in accessibility.css must earn its name."""

	def test_every_pair_reaches_aaa(self):
		# A student enabling high contrast has already said the default is
		# not working for them, so AA is not an ambitious enough target.
		results = audit_palette(HIGH_CONTRAST_PAIRS, level="AAA")
		failures = [r for r in results if not r["passes"]]
		self.assertEqual(
			failures,
			[],
			"high-contrast pairs below AAA: "
			+ ", ".join(f"{r['name']} ({r['ratio']}:1)" for r in failures),
		)

	def test_audit_reports_every_pair_not_just_failures(self):
		results = audit_palette(HIGH_CONTRAST_PAIRS)
		self.assertEqual(len(results), len(HIGH_CONTRAST_PAIRS))
		for result in results:
			self.assertIn("ratio", result)
			self.assertIn("required", result)

	def test_palette_colours_are_valid_hex(self):
		for name, colour in HIGH_CONTRAST_PALETTE.items():
			parse_hex(colour)  # raises if malformed

	def test_border_is_not_a_faint_grey(self):
		# Borders carry meaning (§1.4.11 non-text contrast); a 1.2:1 hairline
		# is decoration, not a boundary.
		self.assertGreaterEqual(
			contrast_ratio(HIGH_CONTRAST_PALETTE["border"], HIGH_CONTRAST_PALETTE["background"]),
			3.0,
		)

	def test_empty_audit_is_empty(self):
		self.assertEqual(audit_palette([]), [])
		self.assertEqual(audit_palette(None), [])


class TestFontScale(unittest.TestCase):
	def test_six_steps_exactly(self):
		# §6.3 says six; five or seven is a different requirement.
		self.assertEqual(len(FONT_STEPS), 6)
		self.assertEqual(sorted(FONT_STEPS), [1, 2, 3, 4, 5, 6])

	def test_scale_is_monotonic(self):
		sizes = [FONT_STEPS[step]["base_px"] for step in sorted(FONT_STEPS)]
		self.assertEqual(sizes, sorted(sizes))
		self.assertEqual(len(set(sizes)), len(sizes))

	def test_default_allows_shrinking_and_growing(self):
		# A scale that only grows is a zoom control, not accessibility.
		self.assertGreater(DEFAULT_FONT_STEP, min(FONT_STEPS))
		self.assertLess(DEFAULT_FONT_STEP, max(FONT_STEPS))

	def test_css_variables_produced(self):
		css = font_scale_css(3)
		self.assertEqual(css["--lms-font-base"], "16px")
		self.assertEqual(css["--lms-font-step"], "3")
		self.assertIn("--lms-font-2xl", css)

	def test_headings_scale_with_the_step(self):
		small = font_scale_css(1)
		large = font_scale_css(6)
		self.assertLess(
			float(small["--lms-font-2xl"].rstrip("px")),
			float(large["--lms-font-2xl"].rstrip("px")),
		)

	def test_invalid_step_rejected(self):
		for step in (0, 7, -1, "big", None, 2.5):
			with self.assertRaises(AccessibilityError):
				validate_font_step(step)

	def test_numeric_string_accepted(self):
		# Preferences arrive from a form as strings.
		self.assertEqual(validate_font_step("4"), 4)


class TestPreferences(unittest.TestCase):
	def test_defaults_are_complete(self):
		defaults = default_preferences()
		for key in ("font_step", "contrast_mode", "whiteboard_mode", "reduce_motion"):
			self.assertIn(key, defaults)

	def test_defaults_validate(self):
		self.assertEqual(validate_preferences(default_preferences()), default_preferences())

	def test_empty_payload_fills_defaults(self):
		self.assertEqual(validate_preferences({}), default_preferences())
		self.assertEqual(validate_preferences(None), default_preferences())

	def test_booleans_coerced(self):
		result = validate_preferences({"whiteboard_mode": 1, "reduce_motion": "yes"})
		self.assertIs(result["whiteboard_mode"], True)
		self.assertIs(result["reduce_motion"], True)

	def test_invalid_contrast_rejected(self):
		with self.assertRaises(AccessibilityError):
			validate_preferences({"contrast_mode": "extreme"})

	def test_invalid_font_step_rejected(self):
		with self.assertRaises(AccessibilityError):
			validate_preferences({"font_step": 9})


class TestTargetSize(unittest.TestCase):
	def test_default_meets_wcag_target_size(self):
		self.assertEqual(target_size_for(False), MIN_TARGET_PX)
		self.assertGreaterEqual(MIN_TARGET_PX, 44)  # WCAG 2.5.5

	def test_whiteboard_targets_are_larger(self):
		# Ek-4.1 "buyuk tik hedefleri": a board is touched from across a
		# classroom, so the 44px minimum is not enough.
		self.assertEqual(target_size_for(True), WHITEBOARD_TARGET_PX)
		self.assertGreater(WHITEBOARD_TARGET_PX, MIN_TARGET_PX)


if __name__ == "__main__":
	unittest.main()

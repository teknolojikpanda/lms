# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Accessibility policy (§6.3, Ek-4.1).

The agreement asks for three things: six font steps applied through the
whole design system via CSS variables, a smart-board mode with large
targets and no hover-dependent interaction, and WCAG 2.1 AA as the
target for contrast, keyboard and focus.

"AA target" is the kind of claim that is usually asserted and never
checked, so the contrast maths lives here and the tests **compute** the
ratios of the palette rather than trusting them. A palette that fails is
then a failing test, not a discovery during an audit.

No ``frappe`` imports, so the scale and the contrast checker are testable
without a site — and the checker is reusable against any future palette.
"""

from __future__ import annotations

import re

# --- Font scale (§6.3 "6 font kademe") -----------------------------------------
#
# Applied by setting the root font size; everything downstream is sized in
# rem, so one variable scales the entire design system rather than each
# component opting in. Step 3 is the default so a student can go smaller
# as well as larger — a scale that only grows is a zoom control, not an
# accessibility feature.
FONT_STEPS = {
	1: {"label": "Extra small", "base_px": 13},
	2: {"label": "Small", "base_px": 14},
	3: {"label": "Default", "base_px": 16},
	4: {"label": "Large", "base_px": 18},
	5: {"label": "Extra large", "base_px": 21},
	6: {"label": "Maximum", "base_px": 24},
}

DEFAULT_FONT_STEP = 3

CONTRAST_MODES = ("normal", "high")

# WCAG 2.1 minimum contrast ratios (1.4.3 AA, 1.4.6 AAA).
AA_NORMAL_TEXT = 4.5
AA_LARGE_TEXT = 3.0
AAA_NORMAL_TEXT = 7.0
AAA_LARGE_TEXT = 4.5

# WCAG 2.5.5 Target Size (AAA) is 44x44 CSS px. A smart board is read and
# touched from across a classroom, so Ek-4.1's "buyuk tik hedefleri" needs
# more than the minimum.
MIN_TARGET_PX = 44
WHITEBOARD_TARGET_PX = 56

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class AccessibilityError(ValueError):
	"""Raised for an invalid preference or colour."""


# --- Font scale ----------------------------------------------------------------


def validate_font_step(step) -> int:
	"""Coerce a font step, rejecting anything that is not a whole 1-6.

	Numeric strings are accepted because preferences arrive from a form.
	Fractions and booleans are not: silently truncating 2.5 to 2, or
	reading ``True`` as step 1, hides a caller bug rather than surfacing
	it.
	"""
	if isinstance(step, bool):
		raise AccessibilityError("Font step must be a number between 1 and 6.")

	try:
		parsed = int(step)
	except (TypeError, ValueError):
		raise AccessibilityError("Font step must be a number between 1 and 6.")

	if isinstance(step, float) and step != parsed:
		raise AccessibilityError("Font step must be a whole number between 1 and 6.")

	if parsed not in FONT_STEPS:
		raise AccessibilityError("Font step must be between 1 and 6.")
	return parsed


def font_scale_css(step: int) -> dict:
	"""CSS custom properties for a font step.

	Returns the root size plus a derived heading scale, so components can
	reference named steps instead of hard-coding sizes and drifting apart.
	"""
	step = validate_font_step(step)
	base = FONT_STEPS[step]["base_px"]

	return {
		"--lms-font-base": f"{base}px",
		"--lms-font-step": str(step),
		# A modular scale (1.2) keeps headings proportional at every step;
		# fixed px headings would stop scaling exactly when a user needs
		# them to.
		"--lms-font-sm": f"{round(base / 1.2, 2)}px",
		"--lms-font-md": f"{base}px",
		"--lms-font-lg": f"{round(base * 1.2, 2)}px",
		"--lms-font-xl": f"{round(base * 1.44, 2)}px",
		"--lms-font-2xl": f"{round(base * 1.728, 2)}px",
	}


# --- Contrast (WCAG 2.1 §1.4.3) --------------------------------------------------


def parse_hex(colour: str) -> tuple[int, int, int]:
	"""Parse #rgb or #rrggbb into 8-bit channels."""
	if not isinstance(colour, str) or not _HEX.match(colour.strip()):
		raise AccessibilityError(f"Not a hex colour: {colour!r}")

	value = colour.strip().lstrip("#")
	if len(value) == 3:
		value = "".join(char * 2 for char in value)
	return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def relative_luminance(colour: str) -> float:
	"""WCAG 2.1 relative luminance.

	Channels are linearised before weighting — averaging the raw sRGB
	values (the common shortcut) overstates the contrast of mid tones and
	passes palettes that genuinely fail.
	"""
	channels = []
	for value in parse_hex(colour):
		c = value / 255
		channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)

	r, g, b = channels
	return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(foreground: str, background: str) -> float:
	"""Contrast ratio between two colours, 1.0 to 21.0."""
	l1 = relative_luminance(foreground)
	l2 = relative_luminance(background)
	lighter, darker = max(l1, l2), min(l1, l2)
	return round((lighter + 0.05) / (darker + 0.05), 2)


def meets_wcag(
	foreground: str, background: str, large_text: bool = False, level: str = "AA"
) -> bool:
	"""Whether a colour pair passes WCAG 2.1 at the given level."""
	ratio = contrast_ratio(foreground, background)

	if level.upper() == "AAA":
		threshold = AAA_LARGE_TEXT if large_text else AAA_NORMAL_TEXT
	else:
		threshold = AA_LARGE_TEXT if large_text else AA_NORMAL_TEXT

	return ratio >= threshold


def audit_palette(pairs: list[dict], level: str = "AA") -> list[dict]:
	"""Check a list of foreground/background pairs, reporting each result.

	Returns every pair rather than only failures, so a report shows what
	was actually checked — "no failures" is meaningless without knowing
	the coverage.
	"""
	results = []
	for pair in pairs or []:
		ratio = contrast_ratio(pair["foreground"], pair["background"])
		large = bool(pair.get("large_text"))
		threshold = (
			(AAA_LARGE_TEXT if large else AAA_NORMAL_TEXT)
			if level.upper() == "AAA"
			else (AA_LARGE_TEXT if large else AA_NORMAL_TEXT)
		)
		results.append(
			{
				"name": pair.get("name", ""),
				"foreground": pair["foreground"],
				"background": pair["background"],
				"ratio": ratio,
				"required": threshold,
				"passes": ratio >= threshold,
			}
		)
	return results


# --- High-contrast palette ----------------------------------------------------------
#
# Source of truth for the values in `frontend/src/styles/accessibility.css`.
# Kept here so the test suite computes their ratios instead of taking the
# palette on trust; changing a colour without meeting the threshold fails
# a test rather than shipping.
#
# High-contrast mode targets **AAA** (7:1), not merely the AA the rest of
# the interface must clear. A student who turns it on has already told you
# the default is not working for them.
HIGH_CONTRAST_PALETTE = {
	"background": "#ffffff",
	"surface": "#ffffff",
	"text": "#111111",
	"text_muted": "#3d3d3d",
	"border": "#111111",
	"link": "#0b3d91",
	"danger": "#8a0000",
	"success": "#0d4f1c",
	"focus": "#0b3d91",
}

HIGH_CONTRAST_PAIRS = [
	{"name": "body text", "foreground": "#111111", "background": "#ffffff"},
	{"name": "muted text", "foreground": "#3d3d3d", "background": "#ffffff"},
	{"name": "links", "foreground": "#0b3d91", "background": "#ffffff"},
	{"name": "error text", "foreground": "#8a0000", "background": "#ffffff"},
	{"name": "success text", "foreground": "#0d4f1c", "background": "#ffffff"},
]


# --- Preferences ------------------------------------------------------------------


def validate_preferences(preferences: dict) -> dict:
	"""Normalise a preference payload, filling defaults for anything absent."""
	preferences = preferences or {}

	contrast = str(preferences.get("contrast_mode") or "normal").lower()
	if contrast not in CONTRAST_MODES:
		raise AccessibilityError(f"Contrast mode must be one of: {', '.join(CONTRAST_MODES)}")

	return {
		"font_step": validate_font_step(preferences.get("font_step") or DEFAULT_FONT_STEP),
		"contrast_mode": contrast,
		"whiteboard_mode": bool(preferences.get("whiteboard_mode")),
		"reduce_motion": bool(preferences.get("reduce_motion")),
	}


def default_preferences() -> dict:
	return {
		"font_step": DEFAULT_FONT_STEP,
		"contrast_mode": "normal",
		"whiteboard_mode": False,
		"reduce_motion": False,
	}


def target_size_for(whiteboard_mode: bool) -> int:
	"""Minimum interactive target size in CSS pixels."""
	return WHITEBOARD_TARGET_PX if whiteboard_mode else MIN_TARGET_PX

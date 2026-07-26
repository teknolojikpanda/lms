# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Accessibility preferences (§6.3, Ek-4.6 /student/profile).

Preferences are stored server-side rather than only in the browser: a
student using a school computer in the morning and their own phone in the
evening is the same student, and having to re-find the font control every
time defeats the point of having one.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from lms.lms.language_platform.accessibility_rules import (
	FONT_STEPS,
	HIGH_CONTRAST_PAIRS,
	AccessibilityError,
	audit_palette,
	default_preferences,
	font_scale_css,
	validate_preferences,
)

PREFERENCE_FIELDS = ("font_step", "contrast_mode", "whiteboard_mode", "reduce_motion")


@frappe.whitelist()
def get_accessibility_preferences() -> dict:
	"""This user's settings, or the defaults if they have never set any.

	Never throws for a missing record — a student who has not touched the
	panel must still get a working interface.
	"""
	if frappe.session.user == "Guest":
		return {**default_preferences(), "font_steps": _font_step_options()}

	stored = frappe.db.get_value(
		"LMS Accessibility Preference",
		{"member": frappe.session.user},
		PREFERENCE_FIELDS,
		as_dict=True,
	)

	preferences = default_preferences()
	if stored:
		preferences = validate_preferences(
			{
				"font_step": stored.font_step,
				"contrast_mode": stored.contrast_mode,
				"whiteboard_mode": stored.whiteboard_mode,
				"reduce_motion": stored.reduce_motion,
			}
		)

	return {
		**preferences,
		"font_steps": _font_step_options(),
		"css_variables": font_scale_css(preferences["font_step"]),
	}


def _font_step_options() -> list[dict]:
	return [
		{"step": step, "label": meta["label"], "base_px": meta["base_px"]}
		for step, meta in sorted(FONT_STEPS.items())
	]


@frappe.whitelist()
@rate_limit(limit=300, seconds=60 * 60)
def save_accessibility_preferences(
	font_step=None,
	contrast_mode: str | None = None,
	whiteboard_mode=None,
	reduce_motion=None,
) -> dict:
	"""Persist settings for the current user."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please login to save accessibility settings."), frappe.PermissionError)

	try:
		preferences = validate_preferences(
			{
				"font_step": font_step,
				"contrast_mode": contrast_mode,
				"whiteboard_mode": whiteboard_mode,
				"reduce_motion": reduce_motion,
			}
		)
	except AccessibilityError as e:
		frappe.throw(str(e))

	name = frappe.db.get_value(
		"LMS Accessibility Preference", {"member": frappe.session.user}, "name"
	)
	if name:
		doc = frappe.get_doc("LMS Accessibility Preference", name)
	else:
		doc = frappe.get_doc(
			{"doctype": "LMS Accessibility Preference", "member": frappe.session.user}
		)

	doc.update(preferences)
	doc.save(ignore_permissions=True)

	return {
		**preferences,
		"css_variables": font_scale_css(preferences["font_step"]),
	}


@frappe.whitelist()
def get_contrast_audit() -> dict:
	"""Contrast report for the shipped high-contrast palette (§6.3 WCAG AA).

	Exposed so the claim is inspectable rather than a line in a document:
	the ratios are computed from the same values the stylesheet uses.
	"""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Not permitted."), frappe.PermissionError)

	results = audit_palette(HIGH_CONTRAST_PAIRS, level="AAA")
	return {
		"level": "AAA",
		"results": results,
		"all_pass": all(r["passes"] for r in results),
		"note": _(
			"High-contrast mode targets AAA. The rest of the interface targets AA and "
			"has not been audited end to end with assistive technology."
		),
	}

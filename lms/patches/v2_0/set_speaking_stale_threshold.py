import frappe

from lms.lms.language_platform.speaking_pipeline import DEFAULT_STALE_AFTER_MINUTES


def execute():
	"""Backfill the stalled-submission threshold on existing sites.

	A new field's default only applies when the Single is first created,
	so migrating an existing site leaves this NULL. The sweep already
	falls back to the same number, but a blank field in Settings reads as
	"unset" when the behaviour is anything but — write it out so what an
	administrator sees matches what runs.
	"""
	if frappe.db.get_single_value("LMS Language Settings", "speaking_stale_after_minutes"):
		return
	frappe.db.set_single_value(
		"LMS Language Settings", "speaking_stale_after_minutes", DEFAULT_STALE_AFTER_MINUTES
	)

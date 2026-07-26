# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Public REST surface of the language-platform module.

Every endpoint returns the §7.3 envelope::

	{"ok": true, "data": ..., "meta": {"correlationId": "..."}}

Call as ``/api/method/lms.lms.language_platform.api.<name>``. The
underlying business logic lives with its doctype controller; these
wrappers only add the response contract.
"""

import frappe

from lms.lms.doctype.lms_data_request import lms_data_request as data_request
from lms.lms.doctype.lms_placement_attempt import lms_placement_attempt as placement
from lms.lms.doctype.lms_speaking_submission import lms_speaking_submission as speaking
from lms.lms.doctype.lms_tenant import lms_tenant as tenant
from lms.lms.doctype.lms_video_overlay import lms_video_overlay as overlay
from lms.lms.language_platform import admin_api, tenant_setup
from lms.lms.language_platform import drm as drm_module
from lms.lms.language_platform import search as search_module
from lms.lms.language_platform.envelope import envelope


@frappe.whitelist()
@envelope
def start_placement(blueprint: str):
	return placement.start_placement(blueprint)


@frappe.whitelist()
@envelope
def save_placement_answer(attempt: str, question: str, answer: str):
	return placement.save_placement_answer(attempt, question, answer)


@frappe.whitelist()
@envelope
def submit_placement(attempt: str, answers: str | None = None):
	return placement.submit_placement(attempt, answers)


@frappe.whitelist()
@envelope
def get_placement_result(attempt: str):
	return placement.get_placement_result(attempt)


@frappe.whitelist()
@envelope
def override_placement_level(attempt: str, level: str, reason: str):
	return placement.override_placement_level(attempt, level, reason)


@frappe.whitelist()
@envelope
def get_lesson_overlays(lesson: str, batch: str | None = None):
	return overlay.get_lesson_overlays(lesson, batch)


@frappe.whitelist()
@envelope
def save_overlay(overlay_data: str, expected_version: int | str | None = None):
	return overlay.save_overlay(overlay_data, expected_version)


@frappe.whitelist()
@envelope
def submit_overlay_answer(overlay_name: str, answer: str):
	return overlay.submit_overlay_answer(overlay_name, answer)


@frappe.whitelist()
@envelope
def create_speaking_submission(prompt: str, audio_file: str, duration_seconds: float):
	return speaking.create_speaking_submission(prompt, audio_file, duration_seconds)


@frappe.whitelist()
@envelope
def get_speaking_result(submission: str):
	return speaking.get_speaking_result(submission)


@frappe.whitelist()
@envelope
def get_grading_queue():
	return speaking.get_grading_queue()


@frappe.whitelist()
@envelope
def override_speaking_score(submission: str, final_score: float, reason: str):
	return speaking.override_speaking_score(submission, final_score, reason)


@frappe.whitelist()
@envelope
def get_admin_dashboard():
	return admin_api.get_admin_dashboard()


@frappe.whitelist()
@envelope
def get_owner_dashboard():
	return admin_api.get_owner_dashboard()


@frappe.whitelist()
@envelope
def create_data_request(subject_user: str, request_type: str, reason: str):
	return data_request.create_data_request(subject_user, request_type, reason)


@frappe.whitelist()
@envelope
def approve_data_request(request: str):
	return data_request.approve_data_request(request)


@frappe.whitelist()
@envelope
def reject_data_request(request: str, reason: str):
	return data_request.reject_data_request(request, reason)


@frappe.whitelist()
@envelope
def export_my_data():
	return data_request.export_my_data()


@frappe.whitelist()
@envelope
def register_tenant(
	tenant_name: str,
	subdomain: str,
	tenant_type: str = "Dershane",
	plan: str = "Pilot",
	seat_limit: int = 500,
	contact_person: str | None = None,
	contact_email: str | None = None,
):
	return tenant.register_tenant(
		tenant_name, subdomain, tenant_type, plan, seat_limit, contact_person, contact_email
	)


@frappe.whitelist()
@envelope
def get_tenants():
	return tenant.get_tenants()


@frappe.whitelist()
@envelope
def suspend_tenant(tenant_name: str, reason: str):
	return tenant.suspend_tenant(tenant_name, reason)


@frappe.whitelist()
@envelope
def activate_tenant(tenant_name: str):
	return tenant.activate_tenant(tenant_name)


@frappe.whitelist()
@envelope
def check_tenant_capacity(tenant_name: str, adding: int):
	return tenant.check_tenant_capacity(tenant_name, adding)


@frappe.whitelist()
@envelope
def archive_tenant(tenant_name: str, reason: str, grace_days: int = 90):
	return tenant.archive_tenant(tenant_name, reason, grace_days)


@frappe.whitelist()
@envelope
def restore_tenant(tenant_name: str):
	return tenant.restore_tenant(tenant_name)


@frappe.whitelist()
@envelope
def get_purge_readiness(tenant_name: str):
	return tenant.get_purge_readiness(tenant_name)


@frappe.whitelist()
@envelope
def import_roster_csv(csv_content: str):
	return tenant_setup.import_roster_csv(csv_content)


@frappe.whitelist()
@envelope
def search(query: str, doctype: str, limit: int = 20):
	return search_module.search(query, doctype, limit)


@frappe.whitelist()
@envelope
def get_searchable_sources():
	return search_module.get_searchable_sources()


@frappe.whitelist()
@envelope
def rebuild_search_index(doctype: str | None = None):
	return search_module.rebuild_index(doctype)


@frappe.whitelist()
@envelope
def get_playback_config(lesson: str):
	return drm_module.get_playback_config(lesson)


@frappe.whitelist()
def drm_license(playback_token: str, license_request: str):
	"""Not envelope-wrapped: the player's CDM expects the licence payload
	shape, not the platform's API envelope."""
	return drm_module.drm_license(playback_token, license_request)


@frappe.whitelist()
@envelope
def get_drm_status():
	return drm_module.get_drm_status()

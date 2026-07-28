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
from lms.lms.doctype.lms_video_overlay import lms_video_overlay as overlay
from lms.lms.language_platform import admin_api
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

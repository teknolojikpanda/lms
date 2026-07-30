# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Speaking submissions (§4.9): student uploads audio → async pipeline
transcribes, computes metrics and proposes rubric scores → teacher can
override with the original AI score preserved (§4.9.2, human has the
final word per §6.7).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.rate_limiter import rate_limit
from frappe.utils import get_datetime, now_datetime

from lms.lms.language_platform.speaking_pipeline import enqueue_processing
from lms.lms.utils import (
	has_course_instructor_role,
	has_evaluator_role,
	has_moderator_role,
)


class LMSSpeakingSubmission(Document):
	def validate(self):
		if self.is_new():
			self.validate_prompt_enabled()
			self.validate_duration_limit()
			self.validate_daily_quota()

	def after_insert(self):
		enqueue_processing(self.name)

	def validate_prompt_enabled(self):
		if not frappe.db.get_value("LMS Speaking Prompt", self.prompt, "enabled"):
			frappe.throw(_("This speaking prompt is not available."))

	def validate_duration_limit(self):
		"""§8.13 MUST: max recording duration."""
		prompt_limit = frappe.db.get_value("LMS Speaking Prompt", self.prompt, "max_duration_seconds")
		settings_limit = frappe.get_cached_doc("LMS Language Settings").max_practice_duration_seconds
		limit = prompt_limit or settings_limit or 120
		if self.duration_seconds and self.duration_seconds > limit:
			frappe.throw(
				_("Recording exceeds the maximum duration of {0} seconds for this prompt.").format(limit)
			)

	def validate_daily_quota(self):
		"""§8.13 MUST: daily speaking minutes quota per student."""
		quota_minutes = frappe.get_cached_doc("LMS Language Settings").daily_speaking_quota_minutes
		if not quota_minutes:
			return
		today_start = get_datetime(now_datetime().date())
		used_seconds = (
			frappe.db.sql(
				"""
				SELECT COALESCE(SUM(duration_seconds), 0)
				FROM `tabLMS Speaking Submission`
				WHERE member = %s AND creation >= %s AND name != %s
				""",
				(self.member, today_start, self.name or ""),
			)[0][0]
			or 0
		)
		if (used_seconds + (self.duration_seconds or 0)) > quota_minutes * 60:
			frappe.throw(
				_("Daily speaking practice quota of {0} minutes reached. Try again tomorrow.").format(
					quota_minutes
				)
			)


def get_permission_query_conditions(user=None):
	"""Students see only their own submissions; graders see all."""
	user = user or frappe.session.user
	if (
		user == "Administrator"
		or has_moderator_role(user)
		or has_course_instructor_role(user)
		or has_evaluator_role(user)
	):
		return ""
	return f"""(`tabLMS Speaking Submission`.`member` = {frappe.db.escape(user)})"""


def has_permission(doc, ptype="read", user=None):
	"""Per-document gate mirroring the list filter above.

	Matters more here than elsewhere: these rows carry the recording, the
	transcript and the rubric feedback, so a direct read by name would
	expose another student's speech and assessment.
	"""
	user = user or frappe.session.user
	if (
		user == "Administrator"
		or "System Manager" in frappe.get_roles(user)
		or has_moderator_role(user)
		or has_course_instructor_role(user)
		or has_evaluator_role(user)
	):
		return True
	return doc.member == user


def _can_grade() -> bool:
	return has_moderator_role() or has_course_instructor_role() or has_evaluator_role()


def _marshal(doc, include_transcript: bool = True) -> dict:
	return {
		"name": doc.name,
		"prompt": doc.prompt,
		"member": doc.member,
		"status": doc.status,
		"duration_seconds": doc.duration_seconds,
		"transcript": doc.transcript if include_transcript else None,
		"metrics": {
			"word_count": doc.word_count,
			"wpm": doc.wpm,
			"lexical_diversity": doc.lexical_diversity,
			"filler_ratio": doc.filler_ratio,
		},
		"rubric_scores": [
			{
				"dimension": r.dimension,
				"score": r.score,
				"feedback": r.feedback,
				"improvement_tip": r.improvement_tip,
			}
			for r in doc.rubric_scores
		],
		"ai_total_score": doc.ai_total_score,
		"final_score": doc.final_score,
		"is_overridden": doc.is_overridden,
		"error_message": doc.error_message,
	}


@frappe.whitelist()
@rate_limit(limit=60, seconds=60 * 60)
def create_speaking_submission(prompt: str, audio_file: str, duration_seconds: float) -> dict:
	"""Student entry point: register the uploaded recording and start the pipeline.

	Rate limited on top of the per-tenant daily minute quota (§8.13): the
	quota caps cost, this caps request volume against the AI pipeline.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please login to submit a recording."), frappe.PermissionError)

	doc = frappe.get_doc(
		{
			"doctype": "LMS Speaking Submission",
			"member": frappe.session.user,
			"prompt": prompt,
			"audio_file": audio_file,
			"duration_seconds": float(duration_seconds or 0),
			"status": "Queued",
		}
	)
	doc.insert(ignore_permissions=True)
	_attach_audio_file(doc, audio_file)
	return {"name": doc.name, "status": doc.status}


def _attach_audio_file(doc, audio_file: str):
	"""Link the pre-uploaded recording to the submission so the retention
	purge job (Ek-2) finds and deletes it with the document context."""
	file_name = frappe.db.get_value(
		"File",
		{"file_url": audio_file, "owner": frappe.session.user, "attached_to_doctype": ["is", "not set"]},
		"name",
	)
	if file_name:
		frappe.db.set_value(
			"File",
			file_name,
			{"attached_to_doctype": "LMS Speaking Submission", "attached_to_name": doc.name},
			update_modified=False,
		)


@frappe.whitelist()
def get_speaking_result(submission: str) -> dict:
	doc = frappe.get_doc("LMS Speaking Submission", submission)
	if doc.member != frappe.session.user and not _can_grade():
		frappe.throw(_("You cannot access this submission."), frappe.PermissionError)
	return _marshal(doc)


@frappe.whitelist()
def get_grading_queue() -> list[dict]:
	"""Pending evaluations for the grading center (Ek-4.5 /teacher/grading)."""
	if not _can_grade():
		frappe.throw(_("You are not allowed to grade submissions."), frappe.PermissionError)
	rows = frappe.get_all(
		"LMS Speaking Submission",
		filters={"status": "Ready"},
		fields=["name", "member", "prompt", "ai_total_score", "final_score", "is_overridden", "creation"],
		order_by="creation asc",
		limit_page_length=200,
	)
	return rows


@frappe.whitelist()
@rate_limit(limit=300, seconds=60 * 60)
def override_speaking_score(submission: str, final_score: float, reason: str) -> dict:
	"""Teacher override: final_score is updated, original AI score preserved (§4.9.2)."""
	if not _can_grade():
		frappe.throw(_("You are not allowed to override scores."), frappe.PermissionError)
	if not (reason or "").strip():
		frappe.throw(_("An override reason is mandatory."))

	final_score = float(final_score)
	if not (0 <= final_score <= 100):
		frappe.throw(_("Final score must be between 0 and 100."))

	doc = frappe.get_doc("LMS Speaking Submission", submission)
	if doc.status != "Ready":
		frappe.throw(_("Only processed submissions can be overridden."))

	doc.final_score = final_score
	doc.is_overridden = 1
	doc.override_by = frappe.session.user
	doc.override_reason = reason.strip()
	doc.save(ignore_permissions=True)
	doc.add_comment(
		"Comment",
		_("Speaking score overridden: AI {0} → final {1}. Reason: {2}").format(
			doc.ai_total_score, final_score, reason.strip()
		),
	)
	return _marshal(doc)

# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Placement test attempts (Technical Agreement v1.3 §4.6, Ek-4.2.5).

Flow: ``start_placement`` selects questions deterministically from the
blueprint (seed persisted for audit, §4.7.2), ``save_placement_answer``
autosaves while the student works, ``submit_placement`` grades
server-side with a server-authoritative timer. Admin level override is
audited (§4.6.2).
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.rate_limiter import rate_limit
from frappe.utils import add_to_date, get_datetime, now_datetime

from lms.lms.language_platform.question_utils import (
	AUTO_GRADABLE_TYPES,
	grade_answer,
	marshal_question,
)
from lms.lms.language_platform.exam_engine import (
	BlueprintError,
	Segment,
	derive_seed,
	map_score_to_level,
	select_questions,
)
from lms.lms.utils import has_moderator_role

# Grace period after the configured duration before late answers are
# refused (§4.7.3: reconnect grace; backend timer is authoritative).
SUBMIT_GRACE_SECONDS = 60


class LMSPlacementAttempt(Document):
	def validate(self):
		if not self.started_at:
			self.started_at = now_datetime()

	def is_expired(self) -> bool:
		duration = frappe.db.get_value("LMS Placement Blueprint", self.blueprint, "duration")
		if not duration:
			return False
		deadline = add_to_date(
			get_datetime(self.started_at), minutes=int(duration), seconds=SUBMIT_GRACE_SECONDS
		)
		return now_datetime() > deadline


def get_permission_query_conditions(user=None):
	"""Students only ever see their own attempts."""
	user = user or frappe.session.user
	if user == "Administrator" or has_moderator_role(user):
		return ""
	return f"""(`tabLMS Placement Attempt`.`member` = {frappe.db.escape(user)})"""


def _require_login():
	if frappe.session.user == "Guest":
		frappe.throw(_("Please login to take the placement test."), frappe.PermissionError)


def _get_attempt_for_member(attempt_name: str):
	attempt = frappe.get_doc("LMS Placement Attempt", attempt_name)
	if attempt.member != frappe.session.user and not has_moderator_role():
		frappe.throw(_("You cannot access this attempt."), frappe.PermissionError)
	return attempt


def _build_pool(blueprint) -> list[dict]:
	"""Fetch auto-gradable questions matching any blueprint segment."""
	pool: dict[str, dict] = {}
	fields = ["name", "language_skill", "language_level", "topic", "type"]
	for segment in blueprint.segments:
		filters = {"type": ["in", AUTO_GRADABLE_TYPES]}
		if segment.skill:
			filters["language_skill"] = segment.skill
		if segment.level:
			filters["language_level"] = segment.level
		if segment.topic:
			filters["topic"] = segment.topic
		for row in frappe.get_all("LMS Question", filters=filters, fields=fields):
			pool[row.name] = row
	return list(pool.values())


def _marshal_attempt(attempt) -> dict:
	blueprint = frappe.db.get_value(
		"LMS Placement Blueprint", attempt.blueprint, ["title", "duration"], as_dict=True
	)
	return {
		"name": attempt.name,
		"blueprint": attempt.blueprint,
		"blueprint_title": blueprint.title,
		"duration": blueprint.duration,
		"status": attempt.status,
		"attempt_number": attempt.attempt_number,
		"started_at": attempt.started_at,
		"questions": [marshal_question(row.question) for row in attempt.questions],
		"answers": {row.question: row.answer for row in attempt.questions if row.answer},
	}


@frappe.whitelist()
@rate_limit(limit=30, seconds=60 * 60)
def start_placement(blueprint: str) -> dict:
	"""Start (or resume) a placement attempt for the current user."""
	_require_login()
	member = frappe.session.user
	bp = frappe.get_doc("LMS Placement Blueprint", blueprint)

	if not bp.enabled:
		frappe.throw(_("This placement test is not available."))

	in_progress = frappe.db.get_value(
		"LMS Placement Attempt",
		{"member": member, "blueprint": blueprint, "status": "In Progress"},
		"name",
	)
	if in_progress:
		attempt = frappe.get_doc("LMS Placement Attempt", in_progress)
		if not attempt.is_expired():
			return _marshal_attempt(attempt)
		_finalize(attempt)  # timed out: grade what was autosaved
		# Committed before anything below can throw. The attempts check
		# further down raises once the limit is reached, and the envelope
		# would roll this back with it — leaving the timed-out attempt In
		# Progress and re-finalised, then rolled back again, on every
		# retry. Committing here is what stops that becoming permanent.
		frappe.db.commit()

	previous = frappe.get_all(
		"LMS Placement Attempt",
		{"member": member, "blueprint": blueprint},
		pluck="name",
	)
	if bp.max_attempts and len(previous) >= bp.max_attempts:
		frappe.throw(_("You have exhausted the maximum attempts for this placement test."))

	seen: set[str] = set(
		frappe.get_all(
			"LMS Placement Attempt Question",
			{"parent": ["in", previous]},
			pluck="question",
		)
	)

	segments = [
		Segment(count=row.question_count, skill=row.skill, level=row.level, topic=row.topic)
		for row in bp.segments
	]
	attempt_number = len(previous) + 1
	seed = derive_seed(member, blueprint, attempt_number)

	try:
		selected = select_questions(segments, _build_pool(bp), seed, seen=seen)
	except BlueprintError as e:
		frappe.log_error(f"Placement blueprint {blueprint}: {e}", "Placement Blueprint Error")
		frappe.throw(
			_("The question pool cannot satisfy this placement test blueprint. Please contact your administrator.")
		)

	attempt = frappe.get_doc(
		{
			"doctype": "LMS Placement Attempt",
			"member": member,
			"blueprint": blueprint,
			"status": "In Progress",
			"attempt_number": attempt_number,
			"seed": seed,
			"started_at": now_datetime(),
		}
	)
	for name in selected:
		meta = frappe.db.get_value(
			"LMS Question", name, ["language_skill", "language_level"], as_dict=True
		)
		attempt.append(
			"questions",
			{
				"question": name,
				"skill": meta.language_skill,
				"level": meta.language_level,
				"marks": 1,
			},
		)
	attempt.insert(ignore_permissions=True)
	return _marshal_attempt(attempt)


@frappe.whitelist()
@rate_limit(limit=2000, seconds=60 * 60)
def save_placement_answer(attempt: str, question: str, answer: str) -> dict:
	"""Autosave a single answer (Ek-4.2.5 'otomatik kaydet')."""
	_require_login()
	doc = _get_attempt_for_member(attempt)

	if doc.status != "In Progress":
		frappe.throw(_("This attempt has already been submitted."))
	if doc.is_expired():
		_finalize(doc)
		# Committed before throwing, on purpose. The envelope rolls back a
		# failed call by default, and this one has genuinely completed the
		# attempt — the throw only tells the student why their answer was
		# not accepted. Losing the finalisation would leave the attempt In
		# Progress with its time already spent.
		frappe.db.commit()
		frappe.throw(_("Time is up. The attempt was submitted automatically."))

	row = next((r for r in doc.questions if r.question == question), None)
	if not row:
		frappe.throw(_("Question not found in this attempt."))

	row.answer = answer
	doc.save(ignore_permissions=True)
	return {"saved": True}


@frappe.whitelist()
@rate_limit(limit=60, seconds=60 * 60)
def submit_placement(attempt: str, answers: str | None = None) -> dict:
	"""Grade the attempt. Late submissions only count autosaved answers."""
	_require_login()
	doc = _get_attempt_for_member(attempt)

	if doc.status != "In Progress":
		frappe.throw(_("This attempt has already been submitted."))

	if answers and not doc.is_expired():
		parsed = json.loads(answers)
		if not isinstance(parsed, dict):
			frappe.throw(_("Invalid answers payload."))
		for row in doc.questions:
			if row.question in parsed:
				value = parsed[row.question]
				row.answer = (
					json.dumps(value) if isinstance(value, list) else ("" if value is None else str(value))
				)

	_finalize(doc)
	return get_placement_result(doc.name)


def _finalize(doc):
	score = 0.0
	max_score = 0.0
	by_skill: dict[str, dict] = {}

	for row in doc.questions:
		max_score += row.marks or 1
		correct = grade_answer(row.question, row.answer)
		row.is_correct = 1 if correct else 0
		row.marks_obtained = (row.marks or 1) if correct else 0
		score += row.marks_obtained

		skill = row.skill or "General"
		bucket = by_skill.setdefault(skill, {"score": 0.0, "max_score": 0.0})
		bucket["score"] += row.marks_obtained
		bucket["max_score"] += row.marks or 1

	percentage = round(score / max_score * 100, 2) if max_score else 0.0
	blueprint = frappe.get_doc("LMS Placement Blueprint", doc.blueprint)
	mapping = [{"min_score": r.min_score, "level": r.level} for r in blueprint.level_mappings]

	doc.score = score
	doc.max_score = max_score
	doc.percentage = percentage
	doc.skill_scores = json.dumps(by_skill)
	doc.result_level = map_score_to_level(percentage, mapping) or blueprint.default_level
	doc.status = "Completed"
	doc.submitted_at = now_datetime()
	doc.save(ignore_permissions=True)


@frappe.whitelist()
def get_placement_result(attempt: str) -> dict:
	"""Result with per-skill breakdown (§4.6.2)."""
	_require_login()
	doc = _get_attempt_for_member(attempt)
	return {
		"name": doc.name,
		"status": doc.status,
		"score": doc.score,
		"max_score": doc.max_score,
		"percentage": doc.percentage,
		"result_level": doc.result_level,
		"effective_level": doc.override_level or doc.result_level,
		"skill_scores": json.loads(doc.skill_scores) if doc.skill_scores else {},
		"submitted_at": doc.submitted_at,
	}


@frappe.whitelist()
@rate_limit(limit=200, seconds=60 * 60)
def override_placement_level(attempt: str, level: str, reason: str) -> dict:
	"""Admin override of the computed level — audit is mandatory (§4.6.2)."""
	if not has_moderator_role():
		frappe.throw(_("Only moderators can override placement levels."), frappe.PermissionError)
	if not (reason or "").strip():
		frappe.throw(_("An override reason is mandatory."))

	doc = frappe.get_doc("LMS Placement Attempt", attempt)
	if doc.status != "Completed":
		frappe.throw(_("Only completed attempts can be overridden."))

	doc.override_level = level
	doc.override_reason = reason.strip()
	doc.override_by = frappe.session.user
	doc.save()  # moderator write permission; track_changes keeps the audit trail
	doc.add_comment(
		"Comment",
		_("Placement level overridden from {0} to {1}. Reason: {2}").format(
			doc.result_level, level, reason.strip()
		),
	)
	return {"effective_level": level}

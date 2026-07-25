# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Video timeline overlays — timestamped notes/questions (§4.5).

Overlay data is a metadata layer independent of the video file. Writes
use optimistic locking via the ``version`` counter (§4.5.2); visibility
follows the scope (Global / Course / Batch). The player validates the
timestamp against actual video duration client-side, per §4.5.2 — the
server cannot reliably know the duration of externally hosted media.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document

from lms.lms.language_platform.question_utils import (
	AUTO_GRADABLE_TYPES,
	grade_answer,
	marshal_question,
)
from lms.lms.utils import has_course_instructor_role, has_moderator_role


class LMSVideoOverlay(Document):
	def validate(self):
		self.validate_content()
		self.validate_scope()

	def before_save(self):
		if not self.is_new():
			# Monotonic version for optimistic concurrency (§4.5.2). The
			# stale-write check itself happens in save_overlay below.
			before = self.get_doc_before_save()
			self.version = ((before.version if before else self.version) or 1) + 1

	def validate_content(self):
		if self.type == "Question":
			if not self.question:
				frappe.throw(_("Question overlays must link an LMS Question."))
			question_type = frappe.db.get_value("LMS Question", self.question, "type")
			if question_type not in AUTO_GRADABLE_TYPES:
				frappe.throw(
					_("Overlay questions must be auto-gradable (MCQ, true/false or short answer).")
				)
			self.note_text = None
		else:
			if not self.note_text:
				frappe.throw(_("Note overlays must have note text."))
			self.question = None

	def validate_scope(self):
		if self.scope == "Batch" and not self.batch:
			frappe.throw(_("Batch-scoped overlays must specify a batch."))
		if self.scope != "Batch":
			self.batch = None


def get_permission_query_conditions(user=None):
	"""Learners see only published overlays; authors also see their drafts."""
	user = user or frappe.session.user
	if user == "Administrator" or has_moderator_role(user) or has_course_instructor_role(user):
		return ""
	return "(`tabLMS Video Overlay`.`published` = 1)"


def _is_staff() -> bool:
	return has_moderator_role() or has_course_instructor_role()


def _member_batches(member: str) -> set[str]:
	return set(frappe.get_all("LMS Batch Enrollment", {"member": member}, pluck="batch"))


def _marshal_overlay(doc, include_question: bool) -> dict:
	data = {
		"name": doc.name,
		"lesson": doc.lesson,
		"timestamp_ms": doc.timestamp_ms,
		"type": doc.type,
		"scope": doc.scope,
		"batch": doc.batch,
		"pause_video": doc.pause_video,
		"published": doc.published,
		"version": doc.version,
		"marks": doc.marks,
		"note_text": doc.note_text if doc.type == "Note" else None,
	}
	if doc.type == "Question" and include_question:
		data["question"] = marshal_question(doc.question)
	return data


@frappe.whitelist()
def get_lesson_overlays(lesson: str, batch: str | None = None) -> list[dict]:
	"""Overlays visible to the current user for a lesson, sorted by timestamp.

	Students get published Global/Course overlays plus Batch overlays of
	batches they are enrolled in (§4.5.2: CLASS scope visibility). Staff
	also get unpublished drafts.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please login to view lesson overlays."), frappe.PermissionError)

	overlays = frappe.get_all(
		"LMS Video Overlay",
		filters={"lesson": lesson},
		fields=["name"],
		ignore_permissions=True,
	)

	staff = _is_staff()
	enrolled_batches = None if staff else _member_batches(frappe.session.user)
	responses = {
		r.overlay: r
		for r in frappe.get_all(
			"LMS Overlay Response",
			{"member": frappe.session.user, "lesson": lesson},
			["overlay", "answer", "is_correct", "marks_obtained"],
		)
	}

	visible = []
	for row in overlays:
		doc = frappe.get_doc("LMS Video Overlay", row.name)
		if not staff:
			if not doc.published:
				continue
			if doc.scope == "Batch" and doc.batch not in enrolled_batches:
				continue
		payload = _marshal_overlay(doc, include_question=True)
		if response := responses.get(doc.name):
			payload["response"] = {
				"answer": response.answer,
				"is_correct": response.is_correct,
				"marks_obtained": response.marks_obtained,
			}
		visible.append(payload)

	return sorted(visible, key=lambda o: o["timestamp_ms"])


@frappe.whitelist()
def save_overlay(overlay: str, expected_version: int | str | None = None) -> dict:
	"""Create or update an overlay with optimistic locking (§4.5.2).

	``overlay`` is a JSON object of fields; include ``name`` to update.
	Updates must carry ``expected_version`` — a stale version is rejected
	so concurrent teacher edits cannot silently overwrite each other.
	"""
	if not _is_staff():
		frappe.throw(_("You are not allowed to edit overlays."), frappe.PermissionError)

	data = json.loads(overlay) if isinstance(overlay, str) else overlay
	name = data.pop("name", None)
	data.pop("version", None)  # server-managed

	if name:
		doc = frappe.get_doc("LMS Video Overlay", name)
		current = int(doc.version or 1)
		if expected_version is None or int(expected_version) != current:
			frappe.throw(
				_("This overlay was modified by someone else (version {0}). Reload and retry.").format(
					current
				),
				title="CONFLICT",
			)
		doc.update(data)
		doc.save()
	else:
		doc = frappe.get_doc({"doctype": "LMS Video Overlay", **data})
		doc.insert()

	return _marshal_overlay(doc.reload(), include_question=True)


@frappe.whitelist()
def submit_overlay_answer(overlay: str, answer: str) -> dict:
	"""Record a student's answer to a question overlay and grade it."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please login to answer."), frappe.PermissionError)

	doc = frappe.get_doc("LMS Video Overlay", overlay)
	if not doc.published or doc.type != "Question":
		frappe.throw(_("This overlay does not accept answers."))
	if doc.scope == "Batch" and doc.batch not in _member_batches(frappe.session.user):
		frappe.throw(_("You are not enrolled in the batch for this overlay."), frappe.PermissionError)

	correct = grade_answer(doc.question, answer)
	marks = (doc.marks or 1) if correct else 0

	existing = frappe.db.get_value(
		"LMS Overlay Response", {"overlay": overlay, "member": frappe.session.user}, "name"
	)
	if existing:
		response = frappe.get_doc("LMS Overlay Response", existing)
	else:
		response = frappe.get_doc(
			{
				"doctype": "LMS Overlay Response",
				"overlay": overlay,
				"member": frappe.session.user,
				"lesson": doc.lesson,
			}
		)
	response.answer = answer
	response.is_correct = 1 if correct else 0
	response.marks_obtained = marks
	if existing:
		response.save(ignore_permissions=True)
	else:
		response.insert(ignore_permissions=True)

	return {"is_correct": correct, "marks_obtained": marks}

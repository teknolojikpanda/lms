# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Shared helpers for serving and grading LMS Questions outside quizzes.

Used by the placement test (§4.6) and video overlay questions (§4.5).
Grading semantics mirror ``lms_quiz``: strict option-set equality for
Choices, fuzzy match (>85 token-sort ratio) for User Input.
"""

import json

import frappe
from fuzzywuzzy import fuzz

from lms.lms.doctype.lms_question.lms_question import (
	QUESTION_CORRECTNESS_FIELDS,
	QUESTION_OPTION_FIELDS,
	QUESTION_POSSIBILITY_FIELDS,
)

AUTO_GRADABLE_TYPES = ("Choices", "User Input")


def marshal_question(name: str) -> dict:
	"""Question payload for a learner — never includes correctness fields."""
	q = frappe.db.get_value(
		"LMS Question",
		name,
		["name", "question", "type", "multiple", *QUESTION_OPTION_FIELDS],
		as_dict=True,
	)
	options = [q[f] for f in QUESTION_OPTION_FIELDS if q.get(f)]
	return {
		"name": q.name,
		"question": q.question,
		"type": q.type,
		"multiple": q.multiple,
		"options": options if q.type == "Choices" else [],
	}


def grade_answer(question: str, answer: str | None) -> bool:
	"""Server-side grading for auto-gradable question types."""
	if not answer:
		return False

	details = frappe.db.get_value(
		"LMS Question",
		question,
		["type", *QUESTION_OPTION_FIELDS, *QUESTION_CORRECTNESS_FIELDS, *QUESTION_POSSIBILITY_FIELDS],
		as_dict=True,
	)

	if details.type == "Choices":
		try:
			selected = json.loads(answer)
		except (ValueError, TypeError):
			selected = [answer]
		if not isinstance(selected, list):
			selected = [selected]
		selected_set = {str(s).strip() for s in selected if s}
		correct_set = {
			(details[opt] or "").strip()
			for opt, chk in zip(QUESTION_OPTION_FIELDS, QUESTION_CORRECTNESS_FIELDS, strict=True)
			if details[chk]
		}
		return bool(correct_set) and selected_set == correct_set

	if details.type == "User Input":
		return any(
			details[f] and fuzz.token_sort_ratio(details[f], answer) > 85
			for f in QUESTION_POSSIBILITY_FIELDS
		)

	return False

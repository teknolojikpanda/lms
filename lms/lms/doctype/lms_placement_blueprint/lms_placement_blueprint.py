# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

CEFR_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]


class LMSPlacementBlueprint(Document):
	def validate(self):
		self.validate_segments()
		self.validate_level_mappings()

	def validate_segments(self):
		if not self.segments:
			frappe.throw(_("Add at least one blueprint segment."))
		for row in self.segments:
			if row.question_count <= 0:
				frappe.throw(_("Row {0}: question count must be greater than zero.").format(row.idx))
			if not (row.skill or row.level or row.topic):
				frappe.throw(
					_("Row {0}: set at least one filter (skill, level or topic) for the segment.").format(
						row.idx
					)
				)

	def validate_level_mappings(self):
		if not self.level_mappings:
			frappe.throw(_("Add at least one score-to-level mapping row."))

		thresholds = set()
		for row in self.level_mappings:
			if row.min_score > 100:
				frappe.throw(_("Row {0}: minimum score cannot exceed 100%.").format(row.idx))
			if row.min_score in thresholds:
				frappe.throw(_("Row {0}: duplicate minimum score threshold.").format(row.idx))
			thresholds.add(row.min_score)

	def total_question_count(self) -> int:
		return sum(row.question_count for row in self.segments)

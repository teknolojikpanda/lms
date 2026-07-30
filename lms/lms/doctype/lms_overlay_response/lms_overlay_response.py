# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from lms.lms.utils import has_course_instructor_role, has_moderator_role


class LMSOverlayResponse(Document):
	def validate(self):
		self.validate_duplicate()

	def validate_duplicate(self):
		duplicate = frappe.db.get_value(
			"LMS Overlay Response",
			{"overlay": self.overlay, "member": self.member, "name": ["!=", self.name or ""]},
			"name",
		)
		if duplicate:
			frappe.throw(_("A response for this overlay already exists for this member."))


def get_permission_query_conditions(user=None):
	"""Students read only their own responses; staff read all."""
	user = user or frappe.session.user
	if user == "Administrator" or has_moderator_role(user) or has_course_instructor_role(user):
		return ""
	return f"""(`tabLMS Overlay Response`.`member` = {frappe.db.escape(user)})"""


def has_permission(doc, ptype="read", user=None):
	"""Per-document gate mirroring the list filter above; see the note in
	lms_placement_attempt.has_permission for why a list filter alone is
	not enough."""
	user = user or frappe.session.user
	if (
		user == "Administrator"
		or "System Manager" in frappe.get_roles(user)
		or has_moderator_role(user)
		or has_course_instructor_role(user)
	):
		return True
	return doc.member == user

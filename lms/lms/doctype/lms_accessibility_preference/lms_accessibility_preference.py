# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class LMSAccessibilityPreference(Document):
	def validate(self):
		# A user may only ever hold their own preferences; the doctype is
		# writable by students, so this is the boundary that stops one
		# student editing another's row.
		if self.member != frappe.session.user and "System Manager" not in frappe.get_roles():
			frappe.throw(_("You can only change your own accessibility settings."), frappe.PermissionError)


def get_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return ""
	return f"""(`tabLMS Accessibility Preference`.`member` = {frappe.db.escape(user)})"""


def has_permission(doc, ptype="read", user=None):
	"""Per-document gate mirroring the list filter above.

	The weakest of the set without this, because `autoname: field:member`
	makes the document name the member's email — so the identifier needed
	to read someone else's record is not guessed, it is already known.
	The contents are disability-related settings, which is exactly the
	kind of inference §6.3 accessibility support should not leak.
	"""
	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return True
	return doc.member == user

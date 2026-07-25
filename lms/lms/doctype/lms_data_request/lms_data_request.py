# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""KVKK data subject requests (§9.2, Ek-4.4 /admin/compliance).

An institution admin raises the request; a **System Manager approves it**
before anything runs. That separation matters most for erasure, which is
irreversible — the four-eyes gate is the safeguard, and every transition
lands in the document's change log (``track_changes``) plus an explicit
comment.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.rate_limiter import rate_limit
from frappe.utils import now_datetime

from lms.lms.language_platform.dsar import anonymize_user, collect_user_data
from lms.lms.utils import has_moderator_role

TERMINAL_STATUSES = ("Completed", "Rejected", "Failed")


class LMSDataRequest(Document):
	def validate(self):
		if self.is_new():
			self.requested_by = frappe.session.user
			self.status = "Pending"
		self.validate_subject()

	def validate_subject(self):
		if self.subject_user in ("Administrator", "Guest"):
			frappe.throw(_("System accounts cannot be the subject of a data request."))

	def on_trash(self):
		# The request *is* the audit record for an irreversible action.
		if self.status in ("Completed", "Processing"):
			frappe.throw(_("Processed data requests cannot be deleted; they are the audit record."))


def _require_approver():
	"""Approval is a System Manager action — erasure must not be self-served."""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(
			_("Only a System Manager can approve or reject data requests."), frappe.PermissionError
		)


@frappe.whitelist()
@rate_limit(limit=60, seconds=60 * 60)
def create_data_request(subject_user: str, request_type: str, reason: str) -> dict:
	"""Raise an export or erasure request (institution admin)."""
	if not has_moderator_role():
		frappe.throw(_("Only institution admins can raise data requests."), frappe.PermissionError)
	if request_type not in ("Export", "Erasure"):
		frappe.throw(_("Invalid request type."))
	if not (reason or "").strip():
		frappe.throw(_("A reason is mandatory for data requests."))

	doc = frappe.get_doc(
		{
			"doctype": "LMS Data Request",
			"subject_user": subject_user,
			"request_type": request_type,
			"reason": reason.strip(),
		}
	)
	doc.insert()
	doc.add_comment("Comment", _("Data request raised by {0}.").format(frappe.session.user))
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
@rate_limit(limit=60, seconds=60 * 60)
def approve_data_request(request: str) -> dict:
	"""Approve and queue the request for processing (System Manager)."""
	_require_approver()

	doc = frappe.get_doc("LMS Data Request", request)
	if doc.status != "Pending":
		frappe.throw(_("Only pending requests can be approved."))
	if doc.requested_by == frappe.session.user and doc.request_type == "Erasure":
		frappe.throw(
			_("Erasure requests must be approved by someone other than the requester."),
			frappe.PermissionError,
		)

	doc.status = "Approved"
	doc.approved_by = frappe.session.user
	doc.save()
	doc.add_comment("Comment", _("Approved by {0}.").format(frappe.session.user))

	frappe.enqueue(
		process_data_request,
		queue="long",
		job_id=f"dsar::{doc.name}",
		deduplicate=True,
		request=doc.name,
	)
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
@rate_limit(limit=60, seconds=60 * 60)
def reject_data_request(request: str, reason: str) -> dict:
	_require_approver()
	if not (reason or "").strip():
		frappe.throw(_("A rejection reason is mandatory."))

	doc = frappe.get_doc("LMS Data Request", request)
	if doc.status != "Pending":
		frappe.throw(_("Only pending requests can be rejected."))

	doc.status = "Rejected"
	doc.approved_by = frappe.session.user
	doc.save()
	doc.add_comment(
		"Comment", _("Rejected by {0}. Reason: {1}").format(frappe.session.user, reason.strip())
	)
	return {"name": doc.name, "status": doc.status}


def process_data_request(request: str):
	"""Background execution of an approved request (idempotent)."""
	doc = frappe.get_doc("LMS Data Request", request)
	if doc.status != "Approved":
		return  # already processed, or never approved

	doc.db_set("status", "Processing", update_modified=False)
	frappe.db.commit()

	try:
		if doc.request_type == "Export":
			summary = _run_export(doc)
		else:
			summary = anonymize_user(doc.subject_user)

		doc.reload()
		doc.status = "Completed"
		doc.result_summary = json.dumps(summary, indent=2, default=str)
		doc.completed_at = now_datetime()
		doc.save(ignore_permissions=True)
		doc.add_comment("Comment", _("{0} completed.").format(doc.request_type))
	except Exception as e:
		frappe.db.rollback()
		doc.reload()
		doc.status = "Failed"
		doc.error_message = str(e)[:500]
		doc.save(ignore_permissions=True)
		frappe.log_error(f"DSAR {request} failed: {e}", "Data Request Error")
	finally:
		frappe.db.commit()


def _run_export(doc) -> dict:
	payload = collect_user_data(doc.subject_user)
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{doc.name}-export.json",
			"attached_to_doctype": "LMS Data Request",
			"attached_to_name": doc.name,
			"is_private": 1,  # exports contain raw PII — never public
			"content": json.dumps(payload, indent=2, default=str),
		}
	)
	file_doc.insert(ignore_permissions=True)
	doc.db_set("result_file", file_doc.file_url, update_modified=False)
	return payload["counts"]


@frappe.whitelist()
@rate_limit(limit=30, seconds=60 * 60)
def export_my_data() -> dict:
	"""Self-service export (§9.2: "ogrenci kendi verisini indirebilir").

	Runs immediately for the caller's own data — no approval gate, because
	the subject is the requester and the operation is read-only.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please login to export your data."), frappe.PermissionError)

	doc = frappe.get_doc(
		{
			"doctype": "LMS Data Request",
			"subject_user": frappe.session.user,
			"request_type": "Export",
			"reason": _("Self-service export by the data subject."),
			"approved_by": frappe.session.user,
		}
	)
	doc.insert(ignore_permissions=True)
	doc.db_set("status", "Processing", update_modified=False)

	counts = _run_export(doc)
	doc.reload()
	doc.status = "Completed"
	doc.result_summary = json.dumps(counts, indent=2, default=str)
	doc.completed_at = now_datetime()
	doc.save(ignore_permissions=True)

	# Students have no role-level access to LMS Data Request, and private
	# file access falls back to the permissions of the attached document.
	# An explicit share is what lets the subject download their own export
	# without widening the doctype's permissions for everyone.
	frappe.share.add("LMS Data Request", doc.name, frappe.session.user, read=1, flags={"ignore_share_permission": True})

	return {"name": doc.name, "file_url": doc.result_file, "counts": counts}

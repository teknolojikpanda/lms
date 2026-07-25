# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""KVKK data subject rights — export and erasure mechanisms (§9.2).

The *policy* (what is personal data, how it is anonymised) lives in
``privacy_rules``; this module only executes it against the database.

- **Export**: collect everything stored about a person into one JSON
  document.
- **Erasure**: *anonymise* rather than hard-delete. The agreement permits
  erasure "zorunlu saklama halleri haricinde" — academic records must
  survive their Ek-2 retention window, so identifying fields are scrubbed
  and the account disabled while pseudonymous rows remain. Free text the
  student wrote is deleted outright, and speaking audio is destroyed
  immediately rather than waiting for its lifecycle rule.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime

from lms.lms.language_platform.privacy_rules import (
	PERSONAL_DATA_SOURCES,
	build_export_payload,
	user_scrub_values,
)


# --- Export ---------------------------------------------------------------


def collect_user_data(user: str) -> dict:
	"""Gather every stored record about ``user`` (§9.2 data portability)."""
	subject = (
		frappe.db.get_value(
			"User",
			user,
			["name", "email", "full_name", "username", "enabled", "creation", "last_active"],
			as_dict=True,
		)
		or {}
	)

	records: dict[str, list] = {}
	for source in PERSONAL_DATA_SOURCES:
		doctype = source["doctype"]
		if not frappe.db.exists("DocType", doctype):
			continue
		rows = frappe.get_all(
			doctype,
			filters={source["owner_field"]: user},
			fields=["*"],
			ignore_permissions=True,
		)
		if rows:
			records[doctype] = rows

	return build_export_payload(
		user_doc=subject,
		records=records,
		generated_at=str(now_datetime()),
	)


# --- Erasure ---------------------------------------------------------------


def anonymize_user(user: str) -> dict:
	"""Scrub identifying data, keeping pseudonymous academic records.

	Returns a per-doctype summary so the request document records exactly
	what was touched.
	"""
	if user in ("Administrator", "Guest"):
		frappe.throw(_("System accounts cannot be anonymized."))

	summary: dict[str, int] = {}

	for source in PERSONAL_DATA_SOURCES:
		doctype = source["doctype"]
		if not frappe.db.exists("DocType", doctype):
			continue

		names = frappe.get_all(
			doctype,
			filters={source["owner_field"]: user},
			pluck="name",
			ignore_permissions=True,
		)
		if not names:
			continue

		if source.get("purge"):
			for name in names:
				frappe.delete_doc(doctype, name, ignore_permissions=True, delete_permanently=True)
			summary[f"{doctype} (deleted)"] = len(names)
			continue

		if scrub := source.get("scrub"):
			for name in names:
				frappe.db.set_value(doctype, name, scrub, update_modified=False)
			summary[f"{doctype} (scrubbed)"] = len(names)
		else:
			summary[f"{doctype} (retained, pseudonymous)"] = len(names)

	_purge_speaking_audio(user, summary)
	_scrub_user_record(user, summary)

	frappe.db.commit()
	return summary


def _purge_speaking_audio(user: str, summary: dict):
	"""Destroy voice recordings immediately (§4.9.3 — most sensitive data)."""
	submissions = frappe.get_all(
		"LMS Speaking Submission", filters={"member": user}, pluck="name", ignore_permissions=True
	)
	deleted = 0
	for submission in submissions:
		files = frappe.get_all(
			"File",
			filters={
				"attached_to_doctype": "LMS Speaking Submission",
				"attached_to_name": submission,
			},
			pluck="name",
		)
		for file_name in files:
			frappe.delete_doc("File", file_name, ignore_permissions=True, delete_permanently=True)
			deleted += 1
	if deleted:
		summary["Speaking audio files (destroyed)"] = deleted


def _scrub_user_record(user: str, summary: dict):
	"""Blank identity fields, replace the email and disable login.

	**Known residual (flagged for legal review):** the User primary key is
	the original email address, and Frappe stores it in the ``owner`` /
	``modified_by`` column of every row the person ever created. Renaming
	the User updates declared Link fields but *not* those two columns, so
	complete identifier removal needs a separate, tested migration that
	rewrites them across all doctypes. Until then this is pseudonymisation
	with login disabled — enough to stop processing — and the position must
	be confirmed by counsel, per the agreement's note that final KVKK
	interpretation requires legal advice.
	"""
	frappe.db.set_value("User", user, user_scrub_values(user), update_modified=False)
	summary["User record (anonymized, login disabled)"] = 1

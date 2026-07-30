# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""KVKK data subject rights — export and erasure mechanisms (§9.2).

The *policy* (what is personal data, how it is anonymised) lives in
``privacy_rules``; this module only executes it against the database.

- **Export**: collect everything stored about a person into one JSON
  document, loaded document by document so child tables — the answers,
  not just the wrapper around them — are included.
- **Erasure**: *anonymise* rather than hard-delete. The agreement permits
  erasure "zorunlu saklama halleri haricinde" — academic records must
  survive their Ek-2 retention window, so identifying fields are scrubbed
  and the account disabled while pseudonymous rows remain. Free text the
  student wrote is deleted outright, and speaking audio is destroyed
  immediately rather than waiting for its lifecycle rule.

  What makes the retained rows genuinely pseudonymous is renaming the
  User: Frappe rewrites declared Link fields and, via
  ``User.after_rename``, the ``owner`` and ``modified_by`` column of
  every table. Without it those rows kept the original address and only
  the summary changed.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.rename_doc import rename_doc
from frappe.utils import now_datetime

from lms.lms.language_platform.privacy_rules import (
	ERASED_MARKER,
	PERSONAL_DATA_SOURCES,
	UNIQUE_ERASED_MARKER,
	anonymized_email,
	build_export_payload,
	denormalized_scrub_values,
	user_scrub_values,
)
from lms.lms.language_platform.search import remove_document as remove_from_search_index
from lms.lms.language_platform.search_rules import SEARCH_SOURCES


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
		names = frappe.get_all(
			doctype,
			filters={source["owner_field"]: user},
			pluck="name",
			ignore_permissions=True,
		)
		# Loaded document by document rather than with fields=["*"], which
		# returns parent columns only. The answers themselves live in child
		# tables — quiz results, the questions and responses of a placement
		# attempt, speaking rubric feedback — so a column-wise export
		# returned the shell of each record and none of its content, while
		# reporting a row count that looked complete.
		rows = [frappe.get_doc(doctype, name).as_dict() for name in names]
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

		owner_field = source["owner_field"]
		names = frappe.get_all(
			doctype,
			filters={owner_field: user},
			pluck="name",
			ignore_permissions=True,
		)
		if not names:
			continue

		# Erasure has to reach every copy, and the search index is a copy.
		# Removing it here rather than relying on doc_events also covers the
		# scrub case, which updates columns without firing a delete.
		searchable = doctype in SEARCH_SOURCES

		if source.get("purge"):
			for name in names:
				if searchable:
					remove_from_search_index(doctype, name)
				frappe.delete_doc(doctype, name, ignore_permissions=True, delete_permanently=True)
			summary[f"{doctype} (deleted)"] = len(names)
			continue

		# Copies of the person's own profile, duplicated onto the row by
		# fetch_from. The rename below fixes the link; nothing fixes these.
		scrub = dict(source.get("scrub") or {})
		scrub.update(denormalized_scrub_values(user, _fetched_user_fields(doctype, owner_field)))

		if scrub:
			for name in names:
				resolved = _resolve_row_scrub(scrub)
				frappe.db.set_value(doctype, name, resolved, update_modified=False)
				name = _rename_if_named_by_a_scrubbed_field(doctype, name, resolved)
				if searchable:
					remove_from_search_index(doctype, name)
			summary[f"{doctype} (scrubbed)"] = len(names)
		else:
			summary[f"{doctype} (retained, pseudonymous)"] = len(names)

	_purge_speaking_audio(user, summary)
	# Last, because everything above finds its rows by the old user id and
	# the rename changes it everywhere at once.
	new_user = _scrub_user_record(user, summary)
	summary["Pseudonym"] = new_user

	frappe.db.commit()
	return summary


def _resolve_row_scrub(scrub: dict) -> dict:
	"""Give this row its own value for any uniquely-marked field."""
	return {
		field: (
			f"{ERASED_MARKER}-{frappe.generate_hash(length=10)}"
			if value is UNIQUE_ERASED_MARKER
			else value
		)
		for field, value in scrub.items()
	}


def _rename_if_named_by_a_scrubbed_field(doctype: str, name: str, resolved: dict) -> str:
	"""Rename a document whose primary key is a field we just scrubbed.

	With ``autoname: field:x``, the document name *is* that value — so
	scrubbing the column alone leaves the erased value sitting in the
	primary key, and in every Link that points at it.
	"""
	autoname = frappe.get_meta(doctype).autoname or ""
	if not autoname.startswith("field:"):
		return name
	new_name = resolved.get(autoname.split(":", 1)[1])
	if not new_name or new_name == name:
		return name
	rename_doc(doctype, name, new_name, force=True, ignore_permissions=True, show_alert=False)
	return new_name


def _fetched_user_fields(doctype: str, owner_field: str) -> dict:
	"""Fields on ``doctype`` that copy a value from the owner's User row.

	Read from the doctype metadata rather than listed, so a denormalised
	field added later is covered without anyone remembering to come here.
	"""
	fetched = {}
	for field in frappe.get_meta(doctype).fields:
		if not field.fetch_from:
			continue
		link_field, _, source_field = field.fetch_from.partition(".")
		if link_field == owner_field and source_field:
			fetched[field.fieldname] = source_field
	return fetched


def _unique_pseudonym(user: str) -> str:
	"""A pseudonymous address not already taken.

	`anonymized_email` is deterministic so a subject erased in batches
	resolves to one pseudonym. But an address can be registered again
	after an erasure, and erasing the new account would collide with the
	old pseudonym — the rename then fails, or is skipped and the account
	stays under its real address while the summary claims otherwise.
	Later erasures of the same address get a suffix rather than silently
	doing nothing.
	"""
	base = anonymized_email(user)
	if _pseudonym_is_free(base):
		return base
	local, _, domain = base.partition("@")
	for suffix in range(2, 1000):
		candidate = f"{local}-{suffix}@{domain}"
		if _pseudonym_is_free(candidate):
			return candidate
	raise frappe.ValidationError(_("Could not allocate a free pseudonym for this subject."))


def _pseudonym_is_free(candidate: str) -> bool:
	"""Both unique columns must be free, not just the primary key.

	The scrub writes the handle to `username`, which carries its own
	unique index. Checking only the User name would accept a candidate
	whose username another account already holds, and the erasure would
	then fail on the insert — after the scrub had begun.
	"""
	handle = candidate.partition("@")[0]
	taken = frappe.get_all(
		"User",
		or_filters={"name": candidate, "username": handle},
		limit=1,
		pluck="name",
		ignore_permissions=True,
	)
	return not taken


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


def _scrub_user_record(user: str, summary: dict) -> str:
	"""Blank identity fields, replace the email, disable login, and rename.

	The rename is what makes the retained records pseudonymous. Without
	it, every row kept for its academic value still carried the original
	address in its ``member`` link, and the summary called them
	"pseudonymous" while nothing about them had changed.

	Frappe's ``User.after_rename`` walks every table in the database and
	rewrites ``owner`` and ``modified_by`` wherever they hold the old
	name, and the rename itself updates declared Link fields. Between
	them the identifier is removed from the database, not merely from the
	User record — which is stronger than the position previously
	documented here, and was verified against this Frappe version rather
	than assumed.

	Returns the new user id so callers can report it.
	"""
	new_user = _unique_pseudonym(user)
	values = user_scrub_values(user)
	# Every identity value is derived from the pseudonym actually chosen,
	# not from the deterministic handle. `email` and `username` are both
	# unique columns, so a repeat erasure of a re-registered address would
	# otherwise collide on whichever the scrub wrote first — and it is the
	# username that trips, several statements after the email looked fine.
	handle = new_user.partition("@")[0]
	values.update({"email": new_user, "username": handle, "first_name": handle, "full_name": handle})
	frappe.db.set_value("User", user, values, update_modified=False)

	if new_user != user:
		# ignore_permissions because erasure runs under the approval of the
		# request document, not the caller's own rights over the subject.
		rename_doc(
			"User",
			user,
			new_user,
			force=True,
			ignore_permissions=True,
			show_alert=False,
		)
		summary["User record (anonymized, renamed, login disabled)"] = 1
	else:
		summary["User record (anonymized, login disabled)"] = 1
		new_user = user

	return new_user

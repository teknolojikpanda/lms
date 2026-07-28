# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""First-run setup and roster onboarding *inside* a tenant site.

Called by the provisioning CLI once the site exists, and by institution
admins afterwards for bulk imports. Targets the §1.3 success criterion:
one institution with a branch, 10 classes and 500 students onboarded and
usable within a day.
"""

from __future__ import annotations

import csv
import io

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from lms.lms.language_platform.tenant_rules import ROSTER_SOURCE_ROW, parse_roster
from lms.lms.utils import has_moderator_role

# Commit every N users so a large import neither holds one enormous
# transaction nor loses everything on a single bad row.
IMPORT_BATCH_SIZE = 50

# Guardrail for a single request; larger rosters should be split or run
# from the CLI, where there is no HTTP timeout to fight.
MAX_ROSTER_ROWS = 2000


def apply_tenant_defaults(
	tenant_name: str, admin_email: str | None = None, admin_full_name: str | None = None
) -> dict:
	"""Seed a freshly created tenant site (called by the provisioning CLI)."""
	summary = {"tenant_name": tenant_name}

	website_settings = frappe.get_single("Website Settings")
	website_settings.app_name = tenant_name
	website_settings.save(ignore_permissions=True)
	summary["branding"] = "applied"

	# Ensure the language-platform settings singleton exists with Ek-2
	# defaults, so retention jobs have something to read from day one.
	settings = frappe.get_single("LMS Language Settings")
	settings.save(ignore_permissions=True)
	summary["language_settings"] = "initialized"

	if admin_email:
		summary["admin"] = create_institution_admin(admin_email, admin_full_name)

	frappe.db.commit()
	return summary


def create_institution_admin(email: str, full_name: str | None = None) -> str:
	"""Create the tenant's first Moderator and send them an invite.

	No password is set here: the user activates through the invite/reset
	flow (§4.2.2), so a credential never travels through provisioning logs.
	"""
	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)
	else:
		first_name = (full_name or email.split("@")[0]).strip()
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name,
				"user_type": "System User",
				"send_welcome_email": 1,
			}
		)
		user.insert(ignore_permissions=True)

	user.add_roles("Moderator", "Course Creator")
	return user.name


# --- Roster import ------------------------------------------------------------


def _create_class(class_name: str) -> str:
	"""Create an LMS Batch for a class named in the roster.

	LMS Batch makes nine fields mandatory — including an instructor, an
	end date and a schedule — none of which a student roster carries.
	Rather than fail the import (which is what happened before this was
	exercised against a real site), fill defensible placeholders and let
	the admin correct them: an imported class with a provisional timetable
	is useful, an import that rejects every row because the CSV has no
	lesson times is not.

	The class is left **unpublished** so nothing reaches students until a
	human has reviewed those placeholders.
	"""
	today = frappe.utils.nowdate()
	placeholder = _("Imported from roster. Review schedule and instructor before publishing.")

	batch = frappe.get_doc(
		{
			"doctype": "LMS Batch",
			"title": class_name,
			"published": 0,
			"start_date": today,
			# A term-length default; the admin sets the real end date.
			"end_date": frappe.utils.add_days(today, 180),
			"start_time": "09:00:00",
			"end_time": "10:00:00",
			"timezone": frappe.db.get_single_value("System Settings", "time_zone") or "UTC",
			"description": placeholder,
			"batch_details": placeholder,
			# `instructors` is mandatory, and the roster does not name one.
			# Seeding the importing admin keeps the record valid and
			# visible to whoever created it, rather than inventing a
			# teacher who has not agreed to take the class.
			"instructors": [{"instructor": frappe.session.user}],
		}
	)
	batch.insert(ignore_permissions=True)
	return batch.name


def _import_row(row: dict, batch_cache: dict) -> str:
	"""Create (or reuse) one student and attach them to their class."""
	email = row["email"]

	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)
	else:
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": row.get("first_name"),
				"last_name": row.get("last_name") or None,
				"user_type": "Website User",
				"send_welcome_email": 1,
			}
		)
		user.insert(ignore_permissions=True)
	user.add_roles("LMS Student")

	class_name = row.get("class_name")
	if class_name:
		batch = batch_cache.get(class_name)
		if not batch:
			batch = frappe.db.get_value("LMS Batch", {"title": class_name}, "name")
			if not batch:
				batch = _create_class(class_name)
			batch_cache[class_name] = batch

		if not frappe.db.exists("LMS Batch Enrollment", {"batch": batch, "member": user.name}):
			frappe.get_doc(
				{
					"doctype": "LMS Batch Enrollment",
					"batch": batch,
					"member": user.name,
				}
			).insert(ignore_permissions=True)

	return user.name


def import_roster_rows(rows: list[dict]) -> dict:
	"""Import validated roster rows, reporting per-row outcomes.

	Rows are committed in batches and each row is isolated: one bad record
	(a rejected email, a link that does not resolve) fails only itself, so
	an admin importing 500 students never has to guess which of them
	landed.
	"""
	valid, errors = parse_roster(rows)

	imported = []
	batch_cache: dict[str, str] = {}

	for position, row in enumerate(valid, start=1):
		# Report the row's position in the *uploaded file*, not among the
		# survivors. Once parse_roster drops the rejected rows the two
		# diverge, and counting survivors would point an admin at the
		# wrong spreadsheet line — often one already listed in `errors`
		# for an unrelated reason.
		source_row = row.get(ROSTER_SOURCE_ROW, position)
		savepoint = f"roster_{position}"
		try:
			frappe.db.savepoint(savepoint)
			imported.append(_import_row(row, batch_cache))
		except Exception as e:
			frappe.db.rollback(save_point=savepoint)
			errors.append({"row": source_row, "email": row.get("email"), "errors": [str(e)]})

		if position % IMPORT_BATCH_SIZE == 0:
			frappe.db.commit()

	frappe.db.commit()

	return {
		"imported": len(imported),
		"failed": len(errors),
		"errors": errors[:100],  # cap the payload; the full picture is the counts
		"classes_touched": sorted(batch_cache.keys()),
	}


@frappe.whitelist()
@rate_limit(limit=20, seconds=60 * 60)
def import_roster_csv(csv_content: str) -> dict:
	"""Bulk-onboard students from CSV (§1.3, Ek-4.4 'toplu import').

	Expected columns: ``email``, ``first_name`` (required); ``last_name``,
	``class_name``, ``student_id``, ``level`` (optional). Column headers
	are case- and space-insensitive.
	"""
	if not has_moderator_role():
		frappe.throw(_("Only institution admins can import students."), frappe.PermissionError)

	if not (csv_content or "").strip():
		frappe.throw(_("The uploaded file is empty."))

	try:
		reader = csv.DictReader(io.StringIO(csv_content))
		rows = list(reader)
	except csv.Error as e:
		frappe.throw(_("Could not read the CSV file: {0}").format(e))

	if not rows:
		frappe.throw(_("The CSV file has no data rows."))
	if len(rows) > MAX_ROSTER_ROWS:
		frappe.throw(
			_("This import has {0} rows; the limit per upload is {1}. Split the file.").format(
				len(rows), MAX_ROSTER_ROWS
			)
		)

	result = import_roster_rows(rows)
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "User",
			"reference_name": frappe.session.user,
			"content": _("Roster import: {0} imported, {1} failed.").format(
				result["imported"], result["failed"]
			),
		}
	).insert(ignore_permissions=True)

	return result

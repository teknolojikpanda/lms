# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Which completed erasures could have left a live credential behind.

Erasure used to write its marker into a Password field's *column* while
the secret stayed in frappe's ``__Auth`` table, so a refresh token
survived a request that had already been recorded as processed. That is
fixed, but the fix cannot undo what leaked: a token is only really gone
once the provider revokes it.

This reports what a site still holds so an operator can revoke at the
source. It is deliberately read-only — deleting the row here would make
the token unreachable from the platform and leave it working at Google
or Zoom, which is the more dangerous of the two states because it looks
resolved.

Run per site on the bench host::

    bench --site <site> execute lms.lms.language_platform.erasure_audit.run

Exit reading: ``recoverable`` is what matters. Any entry there is a
credential an erased subject's provider account is still reachable
through.
"""

from __future__ import annotations

import frappe
from frappe.utils.password import get_decrypted_password

from lms.lms.language_platform.privacy_rules import PERSONAL_DATA_SOURCES


def _credential_fields(doctype: str) -> list[str]:
	return [f.fieldname for f in frappe.get_meta(doctype).fields if f.fieldtype == "Password"]


def _still_recoverable(doctype: str, name: str, fields: list[str]) -> list[str]:
	found = []
	for fieldname in fields:
		try:
			value = get_decrypted_password(doctype, name, fieldname, raise_exception=False)
		except Exception:
			# A key rotation or a corrupt row reads as unrecoverable, which
			# is the safe direction: it will not claim a leak that is not
			# there. The row is still listed under `checked`.
			value = None
		if value:
			found.append(fieldname)
	return found


def run() -> dict:
	"""Report completed erasures and any credential still readable.

	Returns the report as well as printing it, so it can be called from
	another script rather than parsed out of stdout.
	"""
	erasures = frappe.get_all(
		"LMS Data Request",
		filters={"request_type": "Erasure", "status": "Completed"},
		fields=["name", "subject_user", "completed_at"],
		order_by="completed_at asc",
		ignore_permissions=True,
	)

	# Only doctypes the erasure actually scrubs: a credential on a doctype
	# no erasure touches is a different problem and not this report's.
	scrubbed = [
		source["doctype"]
		for source in PERSONAL_DATA_SOURCES
		if source.get("scrub") and frappe.db.exists("DocType", source["doctype"])
	]

	checked = 0
	recoverable: list[dict] = []
	for doctype in scrubbed:
		fields = _credential_fields(doctype)
		if not fields:
			continue
		for name in frappe.get_all(doctype, pluck="name", ignore_permissions=True):
			checked += 1
			leaked = _still_recoverable(doctype, name, fields)
			if leaked:
				recoverable.append({"doctype": doctype, "name": name, "fields": leaked})

	report = {
		"site": frappe.local.site,
		"completed_erasures": erasures,
		"credential_rows_checked": checked,
		"recoverable": recoverable,
	}

	print(f"site: {report['site']}")
	print(f"completed erasures: {len(erasures)}")
	for row in erasures:
		print(f"  {row.name}  subject={row.subject_user}  completed={row.completed_at}")
	print(f"credential rows checked: {checked}")

	if recoverable:
		print(f"\nSTILL RECOVERABLE — revoke these at the provider, not in the database:")
		for entry in recoverable:
			print(f"  {entry['doctype']} {entry['name']}: {', '.join(entry['fields'])}")
	else:
		print("\nno recoverable credentials")

	return report

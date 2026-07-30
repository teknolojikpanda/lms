# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Privacy *policy*: what counts as personal data, how it is anonymised and
how long it is kept (§9.2, Ek-2).

Deliberately free of ``frappe`` imports so the rules that decide what gets
exported, scrubbed or deleted can be unit-tested without a site — the
mechanisms that act on them live in ``dsar.py`` and ``retention.py``.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

# --- Personal data registry -------------------------------------------------
# Single source of truth for BOTH export and erasure, so the two can never
# drift apart.
#   owner_field: column linking the row to the person
#   purge:       delete the row entirely on erasure (personal free text)
#   scrub:       field -> replacement value applied on erasure
PERSONAL_DATA_SOURCES = [
	# --- academic record: retained, pseudonymised by the User rename -----
	{"doctype": "LMS Enrollment", "owner_field": "member"},
	{"doctype": "LMS Course Progress", "owner_field": "member"},
	{"doctype": "LMS Quiz Submission", "owner_field": "member"},
	{"doctype": "LMS Assignment Submission", "owner_field": "member"},
	{"doctype": "LMS Certificate", "owner_field": "member"},
	{"doctype": "LMS Batch Enrollment", "owner_field": "member"},
	{"doctype": "LMS Video Watch Duration", "owner_field": "member"},
	{"doctype": "LMS Placement Attempt", "owner_field": "member"},
	{"doctype": "LMS Overlay Response", "owner_field": "member"},
	{"doctype": "LMS Programming Exercise Submission", "owner_field": "member"},
	{"doctype": "LMS Certificate Request", "owner_field": "member"},
	{"doctype": "LMS Badge Assignment", "owner_field": "member"},
	# Financial records carry their own statutory retention, which outlives
	# an erasure request. Registered so they are *exported*, and left in
	# place so the obligation is met.
	{"doctype": "LMS Payment", "owner_field": "member"},
	# The audit trail of the person's own requests. Deleting it would erase
	# the evidence that the erasure was carried out.
	{"doctype": "LMS Data Request", "owner_field": "subject_user"},
	# --- scrubbed: the row stays, the sensitive columns do not -----------
	{
		"doctype": "LMS Speaking Submission",
		"owner_field": "member",
		"scrub": {"transcript": None, "audio_file": None},
	},
	# A forensic trace exists to identify a viewer, so after erasure it
	# must no longer be able to. The mapping row is kept for the leak
	# record; the network identifiers that describe the person are not.
	{
		"doctype": "LMS Watermark Session",
		"owner_field": "member",
		"scrub": {"ip_address": None, "user_agent": None},
	},
	# --- purged: personal expression with no retention duty --------------
	{"doctype": "LMS Lesson Note", "owner_field": "member", "purge": True},
	{"doctype": "LMS Course Review", "owner_field": "owner", "purge": True},
	{"doctype": "LMS Batch Feedback", "owner_field": "member", "purge": True},
	{"doctype": "LMS Job Application", "owner_field": "user", "purge": True},
	# Disability-related settings: no academic value, and the most
	# sensitive inference in the set.
	{"doctype": "LMS Accessibility Preference", "owner_field": "member", "purge": True},
]

# User fields carrying identity, blanked on erasure.
USER_SCRUB_FIELDS = [
	"first_name",
	"last_name",
	"full_name",
	"username",
	"phone",
	"mobile_no",
	"bio",
	"headline",
	"user_image",
	"location",
	"interest",
	"birth_date",
]


def anonymized_handle(user: str) -> str:
	"""Stable pseudonym for a user id.

	Deterministic, so rows anonymised in separate batches still resolve to
	the same pseudonymous subject (cohort statistics stay coherent), but
	not reversible without the original address.
	"""
	digest = hashlib.sha256(user.encode("utf-8")).hexdigest()[:12]
	return f"anonymized-{digest}"


def anonymized_email(user: str) -> str:
	# .invalid is reserved by RFC 2606: guaranteed never deliverable.
	return f"{anonymized_handle(user)}@anonymized.invalid"


def user_scrub_values(user: str) -> dict:
	"""Replacement values for the User record on erasure."""
	handle = anonymized_handle(user)
	values = {field: None for field in USER_SCRUB_FIELDS}
	values.update(
		{
			"first_name": handle,
			"full_name": handle,
			"username": handle,
			"email": anonymized_email(user),
			"enabled": 0,
		}
	)
	return values


def build_export_payload(user_doc: dict, records: dict, generated_at: str) -> dict:
	"""Shape of the portable export document (§9.2 data portability)."""
	return {
		"schema": "lms-dsar-export/1",
		"generated_at": generated_at,
		"subject": user_doc,
		"records": records,
		"counts": {doctype: len(rows) for doctype, rows in records.items()},
	}


def retention_cutoff(retention_days: int, now: datetime | None = None) -> datetime | None:
	"""Oldest timestamp to keep, or ``None`` when retention is disabled.

	0 or negative means "keep indefinitely" — institutions with longer
	statutory obligations need that, and it must be distinguishable from
	"purge everything".
	"""
	if not retention_days or retention_days <= 0:
		return None
	return (now or datetime.now()) - timedelta(days=int(retention_days))

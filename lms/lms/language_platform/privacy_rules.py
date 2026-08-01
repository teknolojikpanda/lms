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
# Stands in for a mandatory field whose real value was personal. Emptying
# such a field would leave a row that fails validation the next time it is
# saved, so it carries a marker instead of nothing.
ERASED_MARKER = "erased"


class _UniqueErasedMarker:
	"""A marker resolved to a distinct value for each row it scrubs.

	A constant is wrong wherever the column carries a unique index: the
	second row erased collides with the first and the update fails partway
	through an anonymisation. Fields declared with this get a fresh value
	per row instead, and if the field also names the document, the
	document is renamed to match — otherwise the primary key keeps the
	value the scrub just removed from the column.
	"""

	def __repr__(self) -> str:  # pragma: no cover - debugging aid
		return "<unique erased marker>"


UNIQUE_ERASED_MARKER = _UniqueErasedMarker()

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
		# `audio_file` is mandatory, so it takes a marker rather than a
		# NULL: db.set_value bypasses validation during erasure, so the
		# NULL was written happily and only failed the next time anything
		# loaded and saved the row. The recording it pointed at is deleted
		# by the audio-retention sweep; this records that there was one and
		# that it is gone, which a NULL cannot say.
		"scrub": {"transcript": None, "audio_file": ERASED_MARKER},
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
	{"doctype": "LMS Certificate Evaluation", "owner_field": "member"},
	{"doctype": "LMS Live Class Participant", "owner_field": "member"},
	{"doctype": "LMS Program Member", "owner_field": "member"},
	{"doctype": "LMS Course Interest", "owner_field": "user", "purge": True},
	# Personal conferencing credentials. Scrubbed rather than purged
	# because LMS Batch and LMS Live Class hold Link fields to these rows:
	# deleting one raises a link-exists error and fails the whole erasure
	# request. Emptying the credentials removes the personal content and
	# leaves the batch's reference intact.
	#
	# `account_name` is mandatory *and* the autoname field, so it takes a
	# unique marker rather than a NULL — see the LMS Zoom Settings entry
	# below, which documents the same hazard: db.set_value bypasses
	# validation during erasure, so a NULL is written happily and then
	# fails the next time anything loads and saves the row.
	#
	# KNOWN GAP: `google_calendar` is likewise mandatory and is still
	# nulled here, so a scrubbed row remains unsavable. It is a Link, so a
	# marker would only be a dangling reference — the fix is either to
	# leave the link and bring Google Calendar into this list, or to
	# detach the referring Batch/Live Class fields and purge the row
	# outright. That is a retention decision rather than an engineering
	# one and is pending review; see the note in docs.
	{
		"doctype": "LMS Google Meet Settings",
		"owner_field": "member",
		"scrub": {
			"account_name": UNIQUE_ERASED_MARKER,
			"google_calendar": None,
			"enabled": 0,
		},
	},
	# account_id, client_id and client_secret are mandatory on this
	# doctype, so they are replaced with a marker rather than emptied:
	# db.set_value bypasses validation, but a NULL would leave a row that
	# fails the next time anything saves it.
	{
		"doctype": "LMS Zoom Settings",
		"owner_field": "member",
		"scrub": {
			# unique, and the autoname field: see _UniqueErasedMarker
			"account_name": UNIQUE_ERASED_MARKER,
			"account_id": ERASED_MARKER,
			"client_id": ERASED_MARKER,
			"client_secret": ERASED_MARKER,
			"enabled": 0,
		},
	},
]

# Doctypes that link to User but not to a *data subject*: they record who
# teaches, evaluates or mentors, which is a staffing assignment rather
# than personal data held about a learner. Listed explicitly so the
# coverage test can tell "considered and excluded" from "overlooked" —
# every LMS doctype with a User link must appear in one list or the other.
NON_SUBJECT_USER_LINKS = {
	"Course Evaluator": "evaluator — staff assignment",
	"Course Instructor": "instructor — staff assignment",
	"LMS Course Mentor Mapping": "mentor — staff assignment",
	"LMS Live Class": "host — staff assignment",
	"LMS Batch Timetable": "reference to a session's instructor",
	"LMS Enrollment": "handled via `member`; see the registry entry above",
}

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


def denormalized_scrub_values(user: str, fetch_map: dict) -> dict:
	"""Replacements for fields that *copy* User data onto another row.

	Frappe's ``fetch_from`` duplicates a value at write time — a
	submission stores ``member_name`` alongside its ``member`` link, and
	similarly the username and profile image. Renaming the User updates
	the link and leaves those copies untouched, so an erasure that only
	renamed would report a row pseudonymous while it still carried the
	subject's name and photograph.

	``fetch_map`` maps the local field to the User field it copies
	(``{"member_name": "full_name"}``), which the caller reads from the
	doctype metadata rather than a hand-written list — a new denormalised
	field is then covered the day it is added.
	"""
	scrubbed = user_scrub_values(user)
	return {
		local: scrubbed.get(source_field)
		for local, source_field in fetch_map.items()
		if source_field in scrubbed
	}


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

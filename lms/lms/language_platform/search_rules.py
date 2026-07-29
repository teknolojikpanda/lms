# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Search *policy* for privileged content (§8.2, §8.10).

The platform already searches its public catalogue through Frappe's
SQLite FTS (``lms/sqlite.py``: courses, batches, jobs). The agreement asks
for search over three further sources — question bank, transcripts and
audit — and all three are **privileged**, which changes the rules:

1. **Answer keys are never indexed.** An LMS Question carries
   ``is_correct_*`` and ``explanation_*``. A search index is copied,
   backed up and (with OpenSearch) shipped to a separate cluster; putting
   the answer key in it turns every one of those into an exam leak. Only
   the whitelisted fields below are indexed, so adding a field to the
   doctype cannot silently start exporting answers.

2. **One index per site.** Tenant = Frappe site (ADR-0001). A shared
   index with a ``tenant_id`` filter is one forgotten clause away from
   cross-tenant leakage; separate indices make the mistake
   unrepresentable.

3. **Transcripts follow their retention window.** They are personal data
   under Ek-2, so an index entry has to die with the row — see
   ``retention.py``.

No ``frappe`` imports, so the rules that decide what leaves the database
are unit-testable without a site.
"""

from __future__ import annotations

import re

# --- Sources -----------------------------------------------------------------
#
# indexed_fields  : copied into the search document, nothing else is
# text_fields     : free-text matched by a query
# never_index     : documented refusals — asserted by the tests
# roles           : any one of these may search the source at all
# owner_field     : non-privileged searchers see only their own rows
# privileged_roles: roles exempt from the owner filter

SEARCH_SOURCES = {
	"LMS Question": {
		"index": "questions",
		"indexed_fields": [
			"name",
			"question",
			"type",
			"language_level",
			"language_skill",
			"topic",
			"difficulty",
			"option_1",
			"option_2",
			"option_3",
			"option_4",
		],
		"text_fields": ["question", "option_1", "option_2", "option_3", "option_4"],
		# Options are indexed (staff need "do we already have this question?"),
		# correctness and explanations are not: those are the answer key.
		"never_index": [
			*(f"is_correct_{i}" for i in range(1, 11)),
			*(f"explanation_{i}" for i in range(1, 11)),
			*(f"possibility_{i}" for i in range(1, 11)),
		],
		"roles": ("System Manager", "Moderator", "Course Creator"),
		"owner_field": None,
		"privileged_roles": (),
	},
	"LMS Speaking Submission": {
		"index": "transcripts",
		"indexed_fields": [
			"name",
			"member",
			"prompt",
			"transcript",
			"status",
			"final_score",
			"creation",
		],
		"text_fields": ["transcript"],
		# Audio never leaves S3; only the derived text is searchable, and
		# only for as long as Ek-2 allows.
		"never_index": ["audio_file"],
		"roles": ("System Manager", "Moderator", "Course Creator", "Batch Evaluator", "LMS Student"),
		"owner_field": "member",
		"privileged_roles": ("System Manager", "Moderator", "Course Creator", "Batch Evaluator"),
	},
}

MAX_QUERY_LENGTH = 200
MIN_QUERY_LENGTH = 2
DEFAULT_LIMIT = 20
MAX_LIMIT = 100

_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class SearchError(ValueError):
	"""Raised for an unusable query or an unknown source."""


# --- Index naming -------------------------------------------------------------


def index_name(prefix: str, site: str, doctype: str) -> str:
	"""Build the per-site index name (see rule 2 in the module docstring).

	OpenSearch index names are lowercase and cannot contain most
	punctuation, so the site's dots become hyphens — ``kolej.example.com``
	→ ``lms-kolej-example-com-questions``.
	"""
	source = SEARCH_SOURCES.get(doctype)
	if not source:
		raise SearchError(f"'{doctype}' is not a searchable source.")

	site_slug = re.sub(r"[^a-z0-9]+", "-", (site or "").lower()).strip("-")
	if not site_slug:
		raise SearchError("A site name is required to build an index name.")

	prefix_slug = re.sub(r"[^a-z0-9]+", "-", (prefix or "lms").lower()).strip("-") or "lms"
	return f"{prefix_slug}-{site_slug}-{source['index']}"


# --- Documents -----------------------------------------------------------------


def strip_html(value: str) -> str:
	"""Question text is rich text; the index wants words, not markup."""
	if not value:
		return ""
	return _WHITESPACE.sub(" ", _HTML_TAG.sub(" ", str(value))).strip()


def build_document(doctype: str, row: dict) -> dict:
	"""Shape a row into a search document using only whitelisted fields.

	Anything not in ``indexed_fields`` is dropped, so a new doctype field
	never reaches the index by accident.
	"""
	source = SEARCH_SOURCES.get(doctype)
	if not source:
		raise SearchError(f"'{doctype}' is not a searchable source.")

	document = {"doctype": doctype}
	for field in source["indexed_fields"]:
		value = (row or {}).get(field)
		if value is None or value == "":
			continue
		document[field] = strip_html(value) if field in source["text_fields"] else value

	return document


# Stamped on every document as it is written, and the only way a full
# reindex can tell a row it just wrote from one left behind by a row that
# no longer exists.
INDEXED_AT_FIELD = "indexed_at"


def stale_document_query(indexed_before: str) -> dict:
	"""Documents a reindex did not write, and so no longer have a row.

	A full reindex upserts every row still in the database. Anything in
	the index it did *not* touch has no row behind it any more — a
	deleted submission whose incremental removal was lost, or one purged
	by the retention job while the index was unreachable. Left alone it
	stays searchable for ever, transcript included, which makes an
	erasure incomplete (§4.9.3).

	Matching on "older than this run started" rather than "not tagged
	with this run id" is deliberate: a document written incrementally
	*while* the reindex is in flight carries a newer timestamp, so it
	survives instead of being deleted as untouched.

	Documents written before this field existed carry no timestamp at
	all. They are stale by definition — the reindex would have restamped
	them if a row still existed — so the missing-field case is matched
	explicitly rather than left to a range comparison that would skip it.
	"""
	if not indexed_before:
		raise SearchError("A reindex cutoff is required to remove stale documents.")
	return {
		"query": {
			"bool": {
				"should": [
					{"bool": {"must_not": {"exists": {"field": INDEXED_AT_FIELD}}}},
					{"range": {INDEXED_AT_FIELD: {"lt": indexed_before}}},
				],
				"minimum_should_match": 1,
			}
		}
	}


def document_id(doctype: str, name: str) -> str:
	"""Stable id so re-indexing a row replaces it instead of duplicating."""
	return f"{doctype}::{name}"


# --- Queries --------------------------------------------------------------------


def sanitize_query(query: str) -> str:
	"""Normalise a user query, or raise when it cannot be searched.

	Control characters are stripped and length is bounded: an unbounded
	query string is both a wasted cluster round-trip and, with wildcards,
	a cheap way to make the search engine do expensive work.
	"""
	if not isinstance(query, str):
		raise SearchError("Search query must be text.")

	cleaned = _WHITESPACE.sub(" ", _CONTROL_CHARS.sub(" ", query)).strip()

	if len(cleaned) < MIN_QUERY_LENGTH:
		raise SearchError(f"Search query must be at least {MIN_QUERY_LENGTH} characters.")
	if len(cleaned) > MAX_QUERY_LENGTH:
		cleaned = cleaned[:MAX_QUERY_LENGTH]

	return cleaned


def escape_like(term: str) -> str:
	"""Escape LIKE wildcards so a query of ``100%`` is not a full scan."""
	return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def clamp_limit(limit) -> int:
	try:
		limit = int(limit)
	except (TypeError, ValueError):
		return DEFAULT_LIMIT
	return max(1, min(limit, MAX_LIMIT))


# --- Access -----------------------------------------------------------------------


def can_search(doctype: str, roles) -> bool:
	source = SEARCH_SOURCES.get(doctype)
	if not source:
		return False
	return bool(set(roles or ()) & set(source["roles"]))


def requires_owner_filter(doctype: str, roles) -> bool:
	"""True when the searcher may only see their own rows.

	A student searching transcripts gets their own; a grader gets all.
	Returning True by default for an unknown source would be safer still,
	but ``can_search`` has already refused those.
	"""
	source = SEARCH_SOURCES.get(doctype)
	if not source or not source.get("owner_field"):
		return False
	return not (set(roles or ()) & set(source["privileged_roles"]))


def owner_field(doctype: str) -> str | None:
	source = SEARCH_SOURCES.get(doctype)
	return source.get("owner_field") if source else None


def searchable_doctypes(roles) -> list[str]:
	"""Sources this caller may search at all — drives the UI's scope picker."""
	return [doctype for doctype in SEARCH_SOURCES if can_search(doctype, roles)]

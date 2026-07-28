# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Search over privileged content: question bank and transcripts (§8.10).

Access is decided **here**, before the provider is asked anything, and the
same decision applies whichever backend is configured. A search endpoint
is a classic way to read data you were never granted — so the rule is
that the caller's roles determine both *whether* a source can be searched
and *which rows* come back, and the provider only ever receives an
already-narrowed request.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from lms.lms.language_platform.search_providers import get_provider
from lms.lms.language_platform.search_rules import (
	SEARCH_SOURCES,
	SearchError,
	can_search,
	clamp_limit,
	requires_owner_filter,
	sanitize_query,
	searchable_doctypes,
)


@frappe.whitelist()
@rate_limit(limit=600, seconds=60 * 60)
def search(query: str, doctype: str, limit: int = 20) -> dict:
	"""Search one source, scoped to what the caller may actually see."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please login to search."), frappe.PermissionError)

	roles = frappe.get_roles()

	if not can_search(doctype, roles):
		# Same message whether the source is unknown or merely forbidden:
		# distinguishing them tells an attacker what exists.
		frappe.throw(_("You are not allowed to search this content."), frappe.PermissionError)

	try:
		query = sanitize_query(query)
	except SearchError as e:
		frappe.throw(str(e))

	owner = frappe.session.user if requires_owner_filter(doctype, roles) else None

	provider = get_provider()
	results = provider.search(doctype, query, clamp_limit(limit), owner=owner)

	return {
		"query": query,
		"doctype": doctype,
		"provider": provider.name,
		"scoped_to_self": bool(owner),
		"results": results,
	}


@frappe.whitelist()
def get_searchable_sources() -> list[str]:
	"""Sources this caller may search — drives the UI's scope picker."""
	if frappe.session.user == "Guest":
		return []
	return searchable_doctypes(frappe.get_roles())


# --- Index maintenance --------------------------------------------------------


def _index_enabled() -> bool:
	"""True only when a provider that actually keeps an index is configured."""
	try:
		settings = frappe.get_cached_doc("LMS Language Settings")
	except Exception:
		return False
	return (settings.search_provider or "Database") == "OpenSearch"


def on_doc_update(doc, method=None):
	"""Keep the index in step with a changed row (doc_events hook).

	Enqueued rather than inline: a slow or unreachable cluster must never
	be able to fail the user's save.
	"""
	if doc.doctype not in SEARCH_SOURCES or not _index_enabled():
		return

	frappe.enqueue(
		index_document,
		queue="short",
		job_id=f"search-index::{doc.doctype}::{doc.name}",
		deduplicate=True,
		doctype=doc.doctype,
		name=doc.name,
	)


def on_doc_delete(doc, method=None):
	if doc.doctype not in SEARCH_SOURCES or not _index_enabled():
		return

	frappe.enqueue(
		remove_document,
		queue="short",
		job_id=f"search-unindex::{doc.doctype}::{doc.name}",
		deduplicate=True,
		doctype=doc.doctype,
		name=doc.name,
	)


def index_document(doctype: str, name: str):
	try:
		get_provider().index_document(doctype, name)
	except Exception:
		# A search index falling behind is an availability problem, not a
		# correctness one; the nightly reindex repairs it.
		frappe.log_error(frappe.get_traceback(), f"Search index failed: {doctype} {name}")


def remove_document(doctype: str, name: str):
	"""Drop a row from the index.

	Called on delete **and** by the retention job: a transcript cleared
	from the database but left in the index would outlive its Ek-2
	retention window in the search cluster, which is exactly the kind of
	copy KVKK erasure is meant to reach.
	"""
	try:
		get_provider().remove_document(doctype, name)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Search unindex failed: {doctype} {name}")


@frappe.whitelist()
def rebuild_index(doctype: str | None = None) -> dict:
	"""Full reindex — run after enabling OpenSearch or changing the mapping."""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only platform owners can rebuild the search index."), frappe.PermissionError)

	targets = [doctype] if doctype else list(SEARCH_SOURCES)
	for target in targets:
		if target not in SEARCH_SOURCES:
			frappe.throw(_("'{0}' is not a searchable source.").format(target))

	frappe.enqueue(
		_rebuild_index_job, queue="long", job_id="search-rebuild", deduplicate=True, targets=targets
	)
	return {"queued": targets}


def _rebuild_index_job(targets: list[str]):
	provider = get_provider()
	for target in targets:
		try:
			result = provider.reindex(target)
			frappe.logger("lms.search").info(f"Reindexed {target}: {result}")
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Reindex failed: {target}")


def nightly_reindex():
	"""Scheduled repair for drift from failed incremental updates."""
	if not _index_enabled():
		return
	_rebuild_index_job(list(SEARCH_SOURCES))

# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Pluggable search backends for privileged content (§8.10).

The agreement's own staging applies: *"MVP'de Postgres full-text; olcek
artinca OpenSearch index"*. Two implementations, same interface, chosen
in **LMS Language Settings** — mirroring the speaking provider split so
there is one pattern to learn:

- ``DatabaseSearchProvider`` (default): queries the operational database
  directly. No cluster to run, nothing to keep in sync, and correct by
  construction because there is no second copy of the data. It degrades
  on large question banks, which is precisely when you move on.
- ``OpenSearchProvider``: a real index for scale. Buys relevance ranking
  and speed at the cost of an eventually-consistent copy of privileged
  data — which is why ``search_rules`` is strict about what may be
  copied at all.

Both return the same result shape, so callers never branch on provider.
"""

from __future__ import annotations

from datetime import datetime, timezone

import frappe
from frappe import _

from lms.lms.language_platform.search_rules import (
	INDEXED_AT_FIELD,
	SEARCH_SOURCES,
	build_document,
	clamp_limit,
	document_id,
	escape_like,
	index_name,
	owner_field,
	stale_document_query,
)


def _utc_now_iso() -> str:
	"""Index timestamps in UTC, independent of the site's timezone.

	The cutoff is compared against values written by other workers, which
	need not share a timezone with whoever runs the reindex.
	"""
	return datetime.now(timezone.utc).isoformat()


def get_provider():
	settings = frappe.get_cached_doc("LMS Language Settings")
	if (settings.search_provider or "Database") == "OpenSearch":
		return OpenSearchProvider(settings)
	return DatabaseSearchProvider(settings)


class DatabaseSearchProvider:
	"""Search the operational tables directly (MVP default).

	Indexing is a no-op: there is nothing to keep in sync, so a stale
	index — and the privileged data leaking into it — cannot happen.
	"""

	name = "Database"

	def __init__(self, settings=None):
		self.settings = settings

	def index_document(self, doctype: str, name: str):
		return

	def remove_document(self, doctype: str, name: str):
		return

	def reindex(self, doctype: str) -> dict:
		return {"indexed": 0, "provider": self.name, "note": "Database provider needs no index."}

	def search(self, doctype: str, query: str, limit: int, owner: str | None = None) -> list[dict]:
		source = SEARCH_SOURCES[doctype]
		limit = clamp_limit(limit)

		# One OR group across the source's text fields. Escaped so that a
		# query containing % or _ matches literally instead of scanning.
		pattern = f"%{escape_like(query)}%"
		or_filters = [[field, "like", pattern] for field in source["text_fields"]]

		filters = {}
		if owner:
			filters[owner_field(doctype)] = owner

		rows = frappe.get_all(
			doctype,
			filters=filters,
			or_filters=or_filters,
			fields=source["indexed_fields"],
			limit_page_length=limit,
			ignore_permissions=True,  # access already enforced by the caller
		)
		return [
			{"score": None, "source": build_document(doctype, row), "id": document_id(doctype, row["name"])}
			for row in rows
		]


class OpenSearchProvider:
	"""Amazon OpenSearch Service backend (§8.2 'Search (ops.)')."""

	name = "OpenSearch"

	def __init__(self, settings):
		self.settings = settings
		if not settings.opensearch_endpoint:
			frappe.throw(_("Set the OpenSearch endpoint in LMS Language Settings."))
		try:
			from opensearchpy import OpenSearch  # noqa: F401
		except ImportError:
			frappe.throw(_("opensearch-py is required for the OpenSearch provider."))

	def _client(self):
		from opensearchpy import OpenSearch, RequestsHttpConnection

		endpoint = self.settings.opensearch_endpoint.replace("https://", "").rstrip("/")
		kwargs = {
			"hosts": [{"host": endpoint, "port": 443}],
			"use_ssl": True,
			"verify_certs": True,
			"connection_class": RequestsHttpConnection,
			"timeout": 10,
		}

		# Prefer IAM (the ECS task role, §8.13 pattern) over static
		# credentials; fall back to basic auth for self-managed clusters.
		if self.settings.opensearch_use_iam:
			from opensearchpy import AWSV4SignerAuth
			import boto3

			region = self.settings.aws_region or "eu-central-1"
			credentials = boto3.Session().get_credentials()
			kwargs["http_auth"] = AWSV4SignerAuth(credentials, region, "es")
		elif self.settings.opensearch_username:
			password = self.settings.get_password("opensearch_password", raise_exception=False)
			kwargs["http_auth"] = (self.settings.opensearch_username, password)

		return OpenSearch(**kwargs)

	def _index(self, doctype: str) -> str:
		return index_name(
			self.settings.opensearch_index_prefix or "lms", frappe.local.site, doctype
		)

	def index_document(self, doctype: str, name: str):
		source = SEARCH_SOURCES[doctype]
		row = frappe.db.get_value(doctype, name, source["indexed_fields"], as_dict=True)
		if not row:
			return self.remove_document(doctype, name)

		self._client().index(
			index=self._index(doctype),
			id=document_id(doctype, name),
			# Stamped so a reindex running concurrently sees this as newer
			# than its own cutoff and leaves it alone.
			body={**build_document(doctype, row), INDEXED_AT_FIELD: _utc_now_iso()},
			refresh=False,
		)

	def remove_document(self, doctype: str, name: str):
		from opensearchpy.exceptions import NotFoundError

		try:
			self._client().delete(
				index=self._index(doctype), id=document_id(doctype, name), refresh=False
			)
		except NotFoundError:
			# Deleting something already absent is the desired end state.
			pass

	def reindex(self, doctype: str) -> dict:
		from opensearchpy.helpers import bulk

		source = SEARCH_SOURCES[doctype]
		index = self._index(doctype)
		client = self._client()

		if not client.indices.exists(index=index):
			client.indices.create(index=index)

		# Taken before the scan, not after: anything written while this
		# runs must count as newer than the cutoff and survive.
		started_at = _utc_now_iso()

		def actions():
			for row in frappe.get_all(
				doctype, fields=source["indexed_fields"], limit_page_length=0, ignore_permissions=True
			):
				yield {
					"_index": index,
					"_id": document_id(doctype, row["name"]),
					"_source": {**build_document(doctype, row), INDEXED_AT_FIELD: started_at},
				}

		indexed, _errors = bulk(client, actions(), stats_only=True)
		client.indices.refresh(index=index)

		# Upserting the surviving rows is only half a repair. Documents
		# this pass did not write have no row behind them any more — a
		# deletion whose incremental removal was lost, or a transcript the
		# retention job purged while the index was unreachable — and
		# without this they stay searchable for ever. That is what made
		# the advertised nightly repair unable to repair a deletion.
		removed = 0
		try:
			response = client.delete_by_query(
				index=index,
				body=stale_document_query(started_at),
				refresh=True,
				conflicts="proceed",
			)
			removed = response.get("deleted", 0)
		except Exception:
			# Never let the cleanup lose the reindex that just succeeded;
			# the next run retries it. Logged rather than raised because
			# this runs from the scheduler.
			frappe.log_error(
				title="OpenSearch stale-document cleanup failed",
				message=f"index={index}\n{frappe.get_traceback()}",
			)

		return {
			"indexed": indexed,
			"removed_stale": removed,
			"provider": self.name,
			"index": index,
		}

	def search(self, doctype: str, query: str, limit: int, owner: str | None = None) -> list[dict]:
		source = SEARCH_SOURCES[doctype]
		must = [{"multi_match": {"query": query, "fields": source["text_fields"]}}]
		body = {"query": {"bool": {"must": must}}, "size": clamp_limit(limit)}

		if owner:
			# The owner restriction is a filter clause, not a post-filter:
			# it must constrain the result set, never just re-rank it.
			body["query"]["bool"]["filter"] = [{"term": {f"{owner_field(doctype)}.keyword": owner}}]

		response = self._client().search(index=self._index(doctype), body=body)
		return [
			{"score": hit.get("_score"), "source": hit.get("_source", {}), "id": hit.get("_id")}
			for hit in response.get("hits", {}).get("hits", [])
		]

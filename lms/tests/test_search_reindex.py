"""The OpenSearch reindex must delete what it did not write.

opensearch-py is not installed — the platform ships with the database
provider and no AWS account — so the client and the helpers module are
stubbed. That still exercises the part this covers: the order of
operations, the timestamp stamped on each document, and the query used to
remove what the pass did not touch. It does not prove anything about
OpenSearch's own behaviour, which needs a real cluster.
"""

import sys
import types

import frappe
from frappe.tests import IntegrationTestCase

from lms.lms.language_platform.search_rules import INDEXED_AT_FIELD


class _FakeIndices:
	def __init__(self):
		self.created = []
		self.refreshed = []

	def exists(self, index):
		return True

	def create(self, index):
		self.created.append(index)

	def refresh(self, index):
		self.refreshed.append(index)


class _FakeClient:
	def __init__(self):
		self.indices = _FakeIndices()
		self.delete_by_query_calls = []

	def delete_by_query(self, index, body, refresh=False, conflicts=None):
		self.delete_by_query_calls.append({"index": index, "body": body, "conflicts": conflicts})
		return {"deleted": 3}


def _install_opensearch_stubs():
	"""Make `from opensearchpy.helpers import bulk` importable."""
	recorded = {"actions": []}

	def bulk(client, actions, stats_only=False):
		recorded["actions"] = list(actions)
		return len(recorded["actions"]), []

	root = types.ModuleType("opensearchpy")
	root.OpenSearch = object
	root.RequestsHttpConnection = object
	root.AWSV4SignerAuth = object

	helpers = types.ModuleType("opensearchpy.helpers")
	helpers.bulk = bulk

	exceptions = types.ModuleType("opensearchpy.exceptions")
	exceptions.NotFoundError = type("NotFoundError", (Exception,), {})

	sys.modules["opensearchpy"] = root
	sys.modules["opensearchpy.helpers"] = helpers
	sys.modules["opensearchpy.exceptions"] = exceptions
	return recorded


class TestReindexRemovesOrphans(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.recorded = _install_opensearch_stubs()
		self.addCleanup(
			lambda: [
				sys.modules.pop(m, None)
				for m in ("opensearchpy", "opensearchpy.helpers", "opensearchpy.exceptions")
			]
		)

		self.hash = frappe.generate_hash(length=6)
		self.question = frappe.get_doc(
			{
				"doctype": "LMS Question",
				"question": f"Reindex fixture {self.hash}?",
				"type": "Choices",
				"option_1": "a",
				"is_correct_1": 1,
				"option_2": "b",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

		from lms.lms.language_platform.search_providers import OpenSearchProvider

		self.client = _FakeClient()
		self.provider = object.__new__(OpenSearchProvider)
		self.provider.settings = frappe._dict({"opensearch_index_prefix": "test"})
		self.provider._client = lambda: self.client

	def tearDown(self):
		frappe.delete_doc(
			"LMS Question", self.question.name, force=True, ignore_permissions=True, ignore_missing=True
		)
		frappe.db.commit()
		super().tearDown()

	def test_every_document_is_stamped_with_the_run_time(self):
		self.provider.reindex("LMS Question")
		self.assertTrue(self.recorded["actions"], "nothing was indexed")
		for action in self.recorded["actions"]:
			self.assertIn(
				INDEXED_AT_FIELD, action["_source"], "a document went in without a timestamp"
			)

	def test_stale_documents_are_deleted_after_the_upsert(self):
		result = self.provider.reindex("LMS Question")

		self.assertEqual(len(self.client.delete_by_query_calls), 1, "no cleanup was issued")
		self.assertEqual(result["removed_stale"], 3)

		body = self.client.delete_by_query_calls[0]["body"]
		clauses = body["query"]["bool"]["should"]
		self.assertTrue(any("range" in c for c in clauses))
		self.assertTrue(any("must_not" in c.get("bool", {}) for c in clauses))

	def test_the_cutoff_is_not_later_than_the_documents_written(self):
		"""Taken before the scan, so a concurrent write survives.

		If the cutoff were taken afterwards, a document indexed while the
		reindex ran would be older than it and deleted as untouched.
		"""
		self.provider.reindex("LMS Question")

		cutoff = self.client.delete_by_query_calls[0]["body"]["query"]["bool"]["should"]
		cutoff = next(c for c in cutoff if "range" in c)["range"][INDEXED_AT_FIELD]["lt"]
		for action in self.recorded["actions"]:
			self.assertLessEqual(
				cutoff,
				action["_source"][INDEXED_AT_FIELD],
				"the cutoff is newer than a document this run wrote",
			)

	def test_a_failed_cleanup_does_not_lose_the_reindex(self):
		"""The upsert already succeeded; the next run retries the cleanup."""

		def exploding(**kwargs):
			raise RuntimeError("cluster unreachable")

		self.client.delete_by_query = exploding

		result = self.provider.reindex("LMS Question")
		self.assertGreater(result["indexed"], 0)
		self.assertEqual(result["removed_stale"], 0)

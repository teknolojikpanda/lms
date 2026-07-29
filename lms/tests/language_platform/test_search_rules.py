# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Unit tests for search policy (§8.10).

The answer-key exclusion and the per-site index naming are security
tests, not cosmetics: the first stops an exam leak, the second stops
cross-tenant leakage.

Pure tests — no Frappe site required:
	python -m unittest lms.tests.language_platform.test_search_rules
"""

import unittest

from lms.lms.language_platform.search_rules import (
	INDEXED_AT_FIELD,
	SEARCH_SOURCES,
	SearchError,
	build_document,
	can_search,
	clamp_limit,
	document_id,
	escape_like,
	index_name,
	owner_field,
	requires_owner_filter,
	sanitize_query,
	searchable_doctypes,
	stale_document_query,
	strip_html,
)


class TestStaleDocumentQuery(unittest.TestCase):
	"""A reindex has to remove what it did not write.

	Upserting the surviving rows leaves documents behind for rows that no
	longer exist — a deletion whose incremental removal was lost, or a
	transcript the retention job purged while the index was unreachable.
	They stay searchable for ever, which makes an erasure incomplete.
	"""

	CUTOFF = "2026-07-29T12:00:00+00:00"

	def _clauses(self):
		return stale_document_query(self.CUTOFF)["query"]["bool"]["should"]

	def test_matches_documents_older_than_the_run(self):
		ranges = [c for c in self._clauses() if "range" in c]
		self.assertEqual(len(ranges), 1)
		self.assertEqual(ranges[0]["range"][INDEXED_AT_FIELD], {"lt": self.CUTOFF})

	def test_matches_documents_with_no_timestamp_at_all(self):
		"""Written before the field existed; a range test would skip them."""
		missing = [c for c in self._clauses() if "must_not" in c.get("bool", {})]
		self.assertEqual(len(missing), 1)
		self.assertEqual(missing[0]["bool"]["must_not"], {"exists": {"field": INDEXED_AT_FIELD}})

	def test_either_condition_is_enough(self):
		self.assertEqual(stale_document_query(self.CUTOFF)["query"]["bool"]["minimum_should_match"], 1)

	def test_the_cutoff_is_never_open_ended(self):
		"""An empty cutoff would build a query matching the whole index."""
		for bad in ("", None):
			with self.assertRaises(SearchError, msg=f"accepted {bad!r}"):
				stale_document_query(bad)

	def test_a_document_written_during_the_run_is_not_matched(self):
		"""Concurrency: an incremental write mid-reindex must survive.

		Its timestamp is newer than the cutoff, so it fails the range
		clause and carries the field, so it fails the missing clause.
		"""
		later = "2026-07-29T12:00:01+00:00"
		clauses = self._clauses()
		range_clause = next(c for c in clauses if "range" in c)
		self.assertLess(
			range_clause["range"][INDEXED_AT_FIELD]["lt"],
			later,
			"a document written after the cutoff would be deleted as untouched",
		)


class TestAnswerKeyExclusion(unittest.TestCase):
	"""A leaked index must never hand out the answers."""

	def test_correctness_fields_are_never_indexed(self):
		question_row = {
			"name": "QTS-1",
			"question": "<p>Pick one</p>",
			"option_1": "right",
			"option_2": "wrong",
			"is_correct_1": 1,
			"is_correct_2": 0,
			"explanation_1": "because ...",
			"possibility_1": "answer text",
		}
		document = build_document("LMS Question", question_row)

		for field in list(document):
			self.assertFalse(field.startswith("is_correct"), f"{field} leaked into the index")
			self.assertFalse(field.startswith("explanation"), f"{field} leaked into the index")
			self.assertFalse(field.startswith("possibility"), f"{field} leaked into the index")

	def test_answer_values_absent_from_serialized_document(self):
		document = build_document(
			"LMS Question",
			{"name": "QTS-1", "question": "Q", "explanation_1": "SECRET-RATIONALE"},
		)
		self.assertNotIn("SECRET-RATIONALE", str(document))

	def test_registry_declares_its_refusals(self):
		never = SEARCH_SOURCES["LMS Question"]["never_index"]
		self.assertIn("is_correct_1", never)
		self.assertIn("explanation_1", never)

	def test_never_index_and_indexed_fields_never_overlap(self):
		# Guards against a future edit adding a field to both lists.
		for doctype, source in SEARCH_SOURCES.items():
			overlap = set(source["indexed_fields"]) & set(source["never_index"])
			self.assertEqual(overlap, set(), f"{doctype} indexes a field it declares forbidden")

	def test_audio_file_is_never_indexed(self):
		document = build_document(
			"LMS Speaking Submission",
			{"name": "S-1", "transcript": "hello", "audio_file": "/private/files/a.webm"},
		)
		self.assertNotIn("audio_file", document)


class TestDocumentBuilding(unittest.TestCase):
	def test_only_whitelisted_fields_survive(self):
		document = build_document(
			"LMS Question", {"name": "QTS-1", "question": "Q", "some_new_field": "leak"}
		)
		self.assertNotIn("some_new_field", document)

	def test_html_stripped_from_text_fields(self):
		document = build_document(
			"LMS Question", {"name": "QTS-1", "question": "<p>Hello <b>world</b></p>"}
		)
		self.assertEqual(document["question"], "Hello world")

	def test_empty_values_omitted(self):
		document = build_document("LMS Question", {"name": "QTS-1", "question": "Q", "topic": ""})
		self.assertNotIn("topic", document)

	def test_doctype_recorded(self):
		self.assertEqual(build_document("LMS Question", {"name": "x"})["doctype"], "LMS Question")

	def test_unknown_source_rejected(self):
		with self.assertRaises(SearchError):
			build_document("User", {"name": "a@b.com"})

	def test_document_id_is_stable_and_scoped(self):
		self.assertEqual(document_id("LMS Question", "QTS-1"), "LMS Question::QTS-1")
		self.assertNotEqual(
			document_id("LMS Question", "1"), document_id("LMS Speaking Submission", "1")
		)


class TestStripHtml(unittest.TestCase):
	def test_removes_tags_and_collapses_whitespace(self):
		self.assertEqual(strip_html("<div>a\n\n  b</div>"), "a b")

	def test_handles_empty(self):
		self.assertEqual(strip_html(""), "")
		self.assertEqual(strip_html(None), "")


class TestIndexNaming(unittest.TestCase):
	def test_index_is_scoped_to_the_site(self):
		# Tenant isolation: two sites must never share an index.
		a = index_name("lms", "kolej.example.com", "LMS Question")
		b = index_name("lms", "universite.example.com", "LMS Question")
		self.assertNotEqual(a, b)

	def test_dots_become_hyphens(self):
		self.assertEqual(
			index_name("lms", "kolej.example.com", "LMS Question"),
			"lms-kolej-example-com-questions",
		)

	def test_sources_get_distinct_indices(self):
		self.assertNotEqual(
			index_name("lms", "a.com", "LMS Question"),
			index_name("lms", "a.com", "LMS Speaking Submission"),
		)

	def test_rejects_missing_site(self):
		for site in ("", None, "..."):
			with self.assertRaises(SearchError):
				index_name("lms", site, "LMS Question")

	def test_rejects_unknown_source(self):
		with self.assertRaises(SearchError):
			index_name("lms", "a.com", "User")

	def test_prefix_defaults_when_blank(self):
		self.assertTrue(index_name("", "a.com", "LMS Question").startswith("lms-"))


class TestQuerySanitization(unittest.TestCase):
	def test_trims_and_collapses(self):
		self.assertEqual(sanitize_query("  hello   world  "), "hello world")

	def test_strips_control_characters(self):
		self.assertEqual(sanitize_query("hello\x00\x07world"), "hello world")

	def test_rejects_too_short(self):
		for query in ("", " ", "a"):
			with self.assertRaises(SearchError):
				sanitize_query(query)

	def test_truncates_overlong_queries(self):
		self.assertEqual(len(sanitize_query("x" * 5000)), 200)

	def test_rejects_non_string(self):
		for value in (None, 123, [], {}):
			with self.assertRaises(SearchError):
				sanitize_query(value)

	def test_escape_like_neutralizes_wildcards(self):
		# '100%' must not become a full-table scan.
		self.assertEqual(escape_like("100%"), "100\\%")
		self.assertEqual(escape_like("a_b"), "a\\_b")
		self.assertEqual(escape_like("back\\slash"), "back\\\\slash")

	def test_clamp_limit(self):
		self.assertEqual(clamp_limit(50), 50)
		self.assertEqual(clamp_limit(10_000), 100)
		self.assertEqual(clamp_limit(0), 1)
		self.assertEqual(clamp_limit("nonsense"), 20)
		self.assertEqual(clamp_limit(None), 20)


class TestAccessRules(unittest.TestCase):
	STAFF = ["Moderator"]
	TEACHER = ["Course Creator"]
	EVALUATOR = ["Batch Evaluator"]
	STUDENT = ["LMS Student"]

	def test_students_cannot_search_the_question_bank(self):
		self.assertFalse(can_search("LMS Question", self.STUDENT))

	def test_staff_can_search_the_question_bank(self):
		self.assertTrue(can_search("LMS Question", self.STAFF))
		self.assertTrue(can_search("LMS Question", self.TEACHER))

	def test_unknown_source_is_never_searchable(self):
		self.assertFalse(can_search("User", ["System Manager"]))

	def test_no_roles_can_search_nothing(self):
		self.assertFalse(can_search("LMS Question", []))
		self.assertEqual(searchable_doctypes([]), [])

	def test_students_are_restricted_to_their_own_transcripts(self):
		self.assertTrue(requires_owner_filter("LMS Speaking Submission", self.STUDENT))

	def test_graders_see_all_transcripts(self):
		for roles in (self.STAFF, self.TEACHER, self.EVALUATOR):
			self.assertFalse(requires_owner_filter("LMS Speaking Submission", roles))

	def test_question_bank_has_no_owner_filter(self):
		self.assertFalse(requires_owner_filter("LMS Question", self.STAFF))
		self.assertIsNone(owner_field("LMS Question"))

	def test_owner_field_for_transcripts(self):
		self.assertEqual(owner_field("LMS Speaking Submission"), "member")

	def test_searchable_doctypes_reflects_roles(self):
		self.assertEqual(searchable_doctypes(self.STUDENT), ["LMS Speaking Submission"])
		self.assertIn("LMS Question", searchable_doctypes(self.STAFF))


if __name__ == "__main__":
	unittest.main()

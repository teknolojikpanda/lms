# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Unit tests for the privacy policy rules (§9.2 DSAR, Ek-2 retention).

Pure tests — no Frappe site required:
	python -m unittest lms.tests.language_platform.test_privacy_rules
"""

import unittest
from datetime import datetime

from lms.lms.language_platform.privacy_rules import (
	PERSONAL_DATA_SOURCES,
	USER_SCRUB_FIELDS,
	anonymized_email,
	anonymized_handle,
	build_export_payload,
	retention_cutoff,
	user_scrub_values,
)


class TestAnonymization(unittest.TestCase):
	def test_handle_is_deterministic(self):
		self.assertEqual(anonymized_handle("a@b.com"), anonymized_handle("a@b.com"))

	def test_handle_differs_per_user(self):
		self.assertNotEqual(anonymized_handle("a@b.com"), anonymized_handle("c@d.com"))

	def test_handle_does_not_leak_the_original(self):
		handle = anonymized_handle("student@school.edu")
		self.assertNotIn("student", handle)
		self.assertNotIn("school", handle)

	def test_email_uses_reserved_invalid_tld(self):
		# RFC 2606 .invalid can never be delivered to — no accidental mail
		# to a former user after erasure.
		self.assertTrue(anonymized_email("a@b.com").endswith("@anonymized.invalid"))

	def test_scrub_values_blank_every_identity_field(self):
		values = user_scrub_values("student@school.edu")
		for field in USER_SCRUB_FIELDS:
			self.assertIn(field, values)
		# Fields not deliberately overwritten must be cleared, not left as-is.
		self.assertIsNone(values["last_name"])
		self.assertIsNone(values["bio"])
		self.assertIsNone(values["phone"])

	def test_scrub_values_disable_login(self):
		self.assertEqual(user_scrub_values("a@b.com")["enabled"], 0)

	def test_scrub_values_replace_identifiers_with_the_handle(self):
		values = user_scrub_values("a@b.com")
		handle = anonymized_handle("a@b.com")
		self.assertEqual(values["full_name"], handle)
		self.assertEqual(values["username"], handle)
		self.assertNotIn("a@b.com", values["email"])


class TestPersonalDataRegistry(unittest.TestCase):
	def test_every_source_declares_an_owner_field(self):
		for source in PERSONAL_DATA_SOURCES:
			self.assertIn("doctype", source)
			self.assertTrue(source.get("owner_field"), f"{source} missing owner_field")

	def test_no_duplicate_doctypes(self):
		doctypes = [source["doctype"] for source in PERSONAL_DATA_SOURCES]
		self.assertEqual(len(doctypes), len(set(doctypes)))

	def test_speaking_submissions_scrub_transcript_and_audio(self):
		source = next(
			s for s in PERSONAL_DATA_SOURCES if s["doctype"] == "LMS Speaking Submission"
		)
		self.assertIn("transcript", source["scrub"])
		self.assertIn("audio_file", source["scrub"])

	def test_free_text_sources_are_purged_not_retained(self):
		# Notes and reviews are the student's own words: erasure must remove
		# them outright rather than keep them pseudonymously.
		for doctype in ("LMS Lesson Note", "LMS Course Review"):
			source = next(s for s in PERSONAL_DATA_SOURCES if s["doctype"] == doctype)
			self.assertTrue(source.get("purge"), f"{doctype} should be purged")

	def test_academic_records_are_not_purged(self):
		# Ek-2 mandates retention windows for these; erasure keeps them
		# pseudonymously instead of deleting.
		for doctype in ("LMS Certificate", "LMS Quiz Submission", "LMS Placement Attempt"):
			source = next(s for s in PERSONAL_DATA_SOURCES if s["doctype"] == doctype)
			self.assertFalse(source.get("purge"), f"{doctype} must not be purged on erasure")


class TestExportPayload(unittest.TestCase):
	def test_counts_match_records(self):
		payload = build_export_payload(
			user_doc={"name": "a@b.com"},
			records={"LMS Enrollment": [{"x": 1}, {"x": 2}], "LMS Certificate": [{"y": 1}]},
			generated_at="2026-07-25 10:00:00",
		)
		self.assertEqual(payload["counts"]["LMS Enrollment"], 2)
		self.assertEqual(payload["counts"]["LMS Certificate"], 1)

	def test_payload_is_versioned(self):
		payload = build_export_payload({}, {}, "2026-07-25 10:00:00")
		self.assertEqual(payload["schema"], "lms-dsar-export/1")

	def test_empty_export_is_well_formed(self):
		payload = build_export_payload({}, {}, "2026-07-25 10:00:00")
		self.assertEqual(payload["counts"], {})
		self.assertEqual(payload["records"], {})


class TestRetentionCutoff(unittest.TestCase):
	NOW = datetime(2026, 7, 25, 12, 0, 0)

	def test_cutoff_subtracts_the_window(self):
		self.assertEqual(retention_cutoff(30, self.NOW), datetime(2026, 6, 25, 12, 0, 0))

	def test_zero_disables_purging(self):
		# Institutions with longer statutory obligations rely on this:
		# 0 must mean "keep forever", never "delete everything".
		self.assertIsNone(retention_cutoff(0, self.NOW))

	def test_none_disables_purging(self):
		self.assertIsNone(retention_cutoff(None, self.NOW))

	def test_negative_disables_purging(self):
		self.assertIsNone(retention_cutoff(-1, self.NOW))

	def test_ek2_defaults(self):
		self.assertEqual(retention_cutoff(365, self.NOW), datetime(2025, 7, 25, 12, 0, 0))
		self.assertEqual(retention_cutoff(730, self.NOW), datetime(2024, 7, 25, 12, 0, 0))


if __name__ == "__main__":
	unittest.main()

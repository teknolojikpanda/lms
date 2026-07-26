# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Unit tests for DRM playback policy (§4.4.3 Enterprise).

Pure tests — no Frappe site required:
	python -m unittest lms.tests.language_platform.test_drm_rules
"""

import unittest

from lms.lms.language_platform.drm_rules import (
	SYSTEM_FAIRPLAY,
	SYSTEM_PLAYREADY,
	SYSTEM_WIDEVINE,
	DRMError,
	build_playback_claims,
	content_id_for_lesson,
	is_token_expired,
	manifest_path,
	select_drm_system,
	streaming_format,
)

SAFARI_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
SAFARI_IOS = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
CHROME_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
CHROME_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
CHROME_ANDROID = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"
EDGE_CHROMIUM = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36 Edg/120.0"
EDGE_LEGACY = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/64 Safari/537.36 Edge/18.17763"
FIREFOX = "Mozilla/5.0 (Windows NT 10.0; rv:121.0) Gecko/20100101 Firefox/121.0"


class TestSystemSelection(unittest.TestCase):
	"""Picking wrong is indistinguishable from the video being broken."""

	def test_apple_platforms_get_fairplay(self):
		for agent in (SAFARI_MAC, SAFARI_IOS):
			self.assertEqual(select_drm_system(agent), SYSTEM_FAIRPLAY)

	def test_chrome_gets_widevine(self):
		for agent in (CHROME_WIN, CHROME_ANDROID):
			self.assertEqual(select_drm_system(agent), SYSTEM_WIDEVINE)

	def test_chrome_on_mac_gets_widevine_not_fairplay(self):
		# Chrome's UA contains both "Macintosh" and "Safari"; treating it as
		# an Apple platform would hand Widevine's engine a FairPlay licence.
		self.assertEqual(select_drm_system(CHROME_MAC), SYSTEM_WIDEVINE)

	def test_chromium_edge_gets_widevine(self):
		self.assertEqual(select_drm_system(EDGE_CHROMIUM), SYSTEM_WIDEVINE)

	def test_legacy_edge_gets_playready(self):
		self.assertEqual(select_drm_system(EDGE_LEGACY), SYSTEM_PLAYREADY)

	def test_firefox_gets_widevine(self):
		self.assertEqual(select_drm_system(FIREFOX), SYSTEM_WIDEVINE)

	def test_unknown_agent_falls_back_to_widevine(self):
		for agent in ("", None, "curl/8.0", "some-new-browser/1.0"):
			self.assertEqual(select_drm_system(agent), SYSTEM_WIDEVINE)


class TestStreamingFormat(unittest.TestCase):
	def test_fairplay_is_hls(self):
		self.assertEqual(streaming_format(SYSTEM_FAIRPLAY), "hls")

	def test_widevine_and_playready_are_dash(self):
		self.assertEqual(streaming_format(SYSTEM_WIDEVINE), "dash")
		self.assertEqual(streaming_format(SYSTEM_PLAYREADY), "dash")

	def test_unknown_system_rejected(self):
		with self.assertRaises(DRMError):
			streaming_format("not-a-system")


class TestContentId(unittest.TestCase):
	def test_stable_for_the_same_lesson(self):
		# Re-packaging must reuse the key, not orphan issued licences.
		self.assertEqual(content_id_for_lesson("LESSON-1"), content_id_for_lesson("LESSON-1"))

	def test_distinct_per_lesson(self):
		self.assertNotEqual(content_id_for_lesson("LESSON-1"), content_id_for_lesson("LESSON-2"))

	def test_does_not_leak_the_lesson_name(self):
		self.assertNotIn("LESSON", content_id_for_lesson("LESSON-1"))

	def test_requires_a_lesson(self):
		for value in ("", None):
			with self.assertRaises(DRMError):
				content_id_for_lesson(value)


class TestPlaybackClaims(unittest.TestCase):
	def test_claims_carry_subject_and_content(self):
		claims = build_playback_claims("a@b.com", "LESSON-1", SYSTEM_WIDEVINE, issued_at=1000)
		self.assertEqual(claims["sub"], "a@b.com")
		self.assertEqual(claims["lesson"], "LESSON-1")
		self.assertEqual(claims["drm_system"], SYSTEM_WIDEVINE)
		self.assertEqual(claims["format"], "dash")

	def test_default_ttl_is_short(self):
		claims = build_playback_claims("a@b.com", "L1", SYSTEM_WIDEVINE, issued_at=1000)
		self.assertEqual(claims["exp"] - claims["iat"], 300)

	def test_ttl_is_clamped(self):
		# A token is shareable, so an unbounded lifetime is a broadcast key.
		long_lived = build_playback_claims("a@b.com", "L1", SYSTEM_WIDEVINE, 1000, ttl_seconds=99999)
		self.assertEqual(long_lived["exp"] - long_lived["iat"], 3600)

		too_short = build_playback_claims("a@b.com", "L1", SYSTEM_WIDEVINE, 1000, ttl_seconds=1)
		self.assertEqual(too_short["exp"] - too_short["iat"], 30)

	def test_guests_cannot_be_issued_tokens(self):
		for user in ("Guest", "", None):
			with self.assertRaises(DRMError):
				build_playback_claims(user, "L1", SYSTEM_WIDEVINE, 1000)

	def test_unsupported_system_rejected(self):
		with self.assertRaises(DRMError):
			build_playback_claims("a@b.com", "L1", "made-up", 1000)

	def test_expiry_check(self):
		claims = build_playback_claims("a@b.com", "L1", SYSTEM_WIDEVINE, issued_at=1000)
		self.assertFalse(is_token_expired(claims, 1200))
		self.assertTrue(is_token_expired(claims, 1300))
		self.assertTrue(is_token_expired(claims, 1301))

	def test_missing_claims_treated_as_expired(self):
		# Fail closed: an unparseable token must not be treated as valid.
		self.assertTrue(is_token_expired({}, 0))
		self.assertTrue(is_token_expired(None, 0))


class TestManifestPath(unittest.TestCase):
	DOMAIN = "https://abc123.mediapackage.eu-central-1.amazonaws.com"

	def test_hls_manifest_for_fairplay(self):
		path = manifest_path(self.DOMAIN, "abc", SYSTEM_FAIRPLAY)
		self.assertTrue(path.endswith("index.m3u8"))

	def test_dash_manifest_for_widevine(self):
		path = manifest_path(self.DOMAIN, "abc", SYSTEM_WIDEVINE)
		self.assertTrue(path.endswith("index.mpd"))

	def test_trailing_slash_tolerated(self):
		self.assertEqual(
			manifest_path(self.DOMAIN + "/", "abc", SYSTEM_WIDEVINE),
			manifest_path(self.DOMAIN, "abc", SYSTEM_WIDEVINE),
		)

	def test_missing_domain_rejected(self):
		for domain in ("", None):
			with self.assertRaises(DRMError):
				manifest_path(domain, "abc", SYSTEM_WIDEVINE)


if __name__ == "__main__":
	unittest.main()

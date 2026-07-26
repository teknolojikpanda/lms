# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""DRM playback *policy* (§4.4.3 Enterprise, §8.12).

Encrypting the video is the easy half. The half that decides whether
content is actually protected is who gets handed a decryption licence —
so the platform proxies licence requests and checks entitlement before
forwarding them to the vendor. A licence endpoint reachable without that
check turns DRM into an expensive way to serve public video.

No ``frappe`` imports, so the system selection and claim shaping are
testable without a site.
"""

from __future__ import annotations

import hashlib
import re

# Published DRM system identifiers.
SYSTEM_WIDEVINE = "edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"
SYSTEM_PLAYREADY = "9a04f079-9840-4286-ab92-e65be0885f95"
SYSTEM_FAIRPLAY = "94ce86fb-07ff-4f43-adb8-93d2fa968ca2"

SUPPORTED_SYSTEMS = frozenset({SYSTEM_WIDEVINE, SYSTEM_PLAYREADY, SYSTEM_FAIRPLAY})

# Streaming format each system is delivered with.
SYSTEM_FORMATS = {
	SYSTEM_FAIRPLAY: "hls",
	SYSTEM_WIDEVINE: "dash",
	SYSTEM_PLAYREADY: "dash",
}

DEFAULT_TOKEN_TTL_SECONDS = 300

_SAFARI = re.compile(r"safari", re.I)
_CHROMIUM = re.compile(r"chrome|chromium|crios|edg/", re.I)
# iOS/iPadOS/tvOS only. Deliberately excludes "macintosh": Chrome and Edge
# on macOS carry both "Macintosh" and "Safari" in their user agent, and
# matching on the device alone hands them a FairPlay licence their
# Widevine engine cannot use.
_APPLE_WEBKIT_DEVICE = re.compile(r"iphone|ipad|ipod|apple tv", re.I)
_EDGE_LEGACY = re.compile(r"edge/", re.I)


class DRMError(ValueError):
	"""Raised for an unusable DRM request."""


def select_drm_system(user_agent: str | None) -> str:
	"""Pick the DRM system a browser can actually play.

	The three systems are not interchangeable — a Widevine licence is
	useless to Safari — and a student brings whatever device they own, so
	guessing wrong is indistinguishable from the video being broken.

	Two separate rules, because "Apple device" and "Safari" are not the
	same question:

	- On iOS/iPadOS/tvOS every browser is WebKit underneath, so FairPlay
	  regardless of which browser the student believes they are using.
	- On macOS only real Safari takes FairPlay; Chrome and Edge there use
	  Widevine despite advertising "Macintosh" and "Safari".

	Legacy Edge gets PlayReady. Everything else, including unrecognised
	agents, gets Widevine — the safest fallback, since Chromium
	derivatives dominate the long tail.
	"""
	agent = user_agent or ""

	if _APPLE_WEBKIT_DEVICE.search(agent):
		return SYSTEM_FAIRPLAY
	if _SAFARI.search(agent) and not _CHROMIUM.search(agent):
		return SYSTEM_FAIRPLAY
	if _EDGE_LEGACY.search(agent):
		return SYSTEM_PLAYREADY
	return SYSTEM_WIDEVINE


def streaming_format(system_id: str) -> str:
	"""Manifest format matching a DRM system."""
	if system_id not in SYSTEM_FORMATS:
		raise DRMError(f"Unsupported DRM system: {system_id}")
	return SYSTEM_FORMATS[system_id]


def content_id_for_lesson(lesson: str) -> str:
	"""Stable content id used as the SPEKE key identifier.

	Derived rather than random so re-packaging the same lesson reuses its
	key instead of orphaning every licence already issued for it.
	"""
	if not lesson:
		raise DRMError("A lesson is required to derive a content id.")
	return hashlib.sha256(f"lesson:{lesson}".encode("utf-8")).hexdigest()[:32]


def build_playback_claims(
	user: str,
	lesson: str,
	system_id: str,
	issued_at: int,
	ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
) -> dict:
	"""Claims for the short-lived playback token handed to the player.

	Deliberately short-lived: the token is the thing a student could pass
	to a friend, and a five-minute window bounds that to roughly nothing
	while still covering a slow player start.
	"""
	if not user or user == "Guest":
		raise DRMError("Anonymous users cannot be issued playback tokens.")
	if system_id not in SUPPORTED_SYSTEMS:
		raise DRMError(f"Unsupported DRM system: {system_id}")

	ttl = max(30, min(int(ttl_seconds), 3600))
	return {
		"sub": user,
		"lesson": lesson,
		"content_id": content_id_for_lesson(lesson),
		"drm_system": system_id,
		"format": streaming_format(system_id),
		"iat": int(issued_at),
		"exp": int(issued_at) + ttl,
	}


def is_token_expired(claims: dict, now: int) -> bool:
	return int(now) >= int((claims or {}).get("exp", 0))


def manifest_path(packaging_group_domain: str, content_id: str, system_id: str) -> str:
	"""URL of the packaged manifest for this content and system."""
	if not packaging_group_domain:
		raise DRMError("No MediaPackage packaging group configured.")

	fmt = streaming_format(system_id)
	extension = "m3u8" if fmt == "hls" else "mpd"
	domain = packaging_group_domain.rstrip("/")
	return f"{domain}/out/v1/{content_id}/index.{extension}"

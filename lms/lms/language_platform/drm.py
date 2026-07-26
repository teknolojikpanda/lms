# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Enterprise DRM playback (§4.4.3, §8.12).

Encryption is only half of content protection. The half that decides
whether the content is actually protected is **who gets handed a
decryption licence** — so every licence request is proxied through here
and checked against the same entitlement rule that governs the lesson
itself. A licence endpoint the vendor exposes directly to browsers,
without this check, turns an expensive DRM subscription into a
complicated way of serving public video.

The DRM licence itself is out of scope per §1.2 (procurement); this
module is the integration point where a purchased vendor plugs in. When
DRM is disabled — the default — playback falls back to the MVP tier:
CloudFront signed cookies over plain HLS (§4.4.3).
"""

from __future__ import annotations

import json
import time

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from lms.lms.language_platform.drm_rules import (
	DRMError,
	build_playback_claims,
	content_id_for_lesson,
	is_token_expired,
	manifest_path,
	select_drm_system,
)
from lms.lms.permissions import can_access_lesson


def _settings():
	return frappe.get_cached_doc("LMS Language Settings")


def drm_enabled() -> bool:
	try:
		return bool(_settings().drm_enabled)
	except Exception:
		return False


def _require_entitlement(lesson: str):
	"""The single gate. Reuses the lesson access rule rather than
	inventing a second one — two answers to "may this student watch
	this?" is how they drift apart."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please login to play this lesson."), frappe.PermissionError)

	if not can_access_lesson(lesson):
		frappe.logger("lms.security").warning(
			"DRM licence denied: user=%s lesson=%s", frappe.session.user, lesson
		)
		frappe.throw(_("You do not have access to this lesson."), frappe.PermissionError)


@frappe.whitelist()
@rate_limit(limit=600, seconds=60 * 60)
def get_playback_config(lesson: str) -> dict:
	"""What the player needs to start: manifest, DRM system, licence URL.

	Returns the MVP shape when DRM is off, so the player has one code
	path and the tier is a configuration decision rather than a fork in
	the frontend.
	"""
	_require_entitlement(lesson)

	if not drm_enabled():
		return {"drm": False, "lesson": lesson}

	settings = _settings()
	user_agent = frappe.get_request_header("User-Agent") or ""
	system_id = select_drm_system(user_agent)

	try:
		claims = build_playback_claims(
			user=frappe.session.user,
			lesson=lesson,
			system_id=system_id,
			issued_at=int(time.time()),
			ttl_seconds=settings.drm_token_ttl_seconds or 300,
		)
		manifest = manifest_path(settings.mediapackage_domain, claims["content_id"], system_id)
	except DRMError as e:
		frappe.throw(str(e))

	return {
		"drm": True,
		"lesson": lesson,
		"drm_system": system_id,
		"format": claims["format"],
		"manifest_url": manifest,
		# The player posts to *us*, never to the vendor directly: that
		# indirection is what makes the entitlement check unavoidable.
		"license_url": "/api/method/lms.lms.language_platform.api.drm_license",
		"playback_token": _issue_token(claims),
		"expires_in": claims["exp"] - claims["iat"],
	}


def _issue_token(claims: dict) -> str:
	"""Sign the claims so they cannot be edited in the browser.

	Frappe's site secret already backs session signing, so it is the
	right key here too — a separate secret would be one more thing to
	rotate and forget.
	"""
	from frappe.utils.password import get_encryption_key
	import hashlib
	import hmac
	import base64

	payload = base64.urlsafe_b64encode(json.dumps(claims, sort_keys=True).encode()).decode()
	signature = hmac.new(
		get_encryption_key().encode() if isinstance(get_encryption_key(), str) else get_encryption_key(),
		payload.encode(),
		hashlib.sha256,
	).hexdigest()
	return f"{payload}.{signature}"


def _verify_token(token: str) -> dict:
	"""Reject anything that was not issued by us and is still fresh."""
	from frappe.utils.password import get_encryption_key
	import hashlib
	import hmac
	import base64

	try:
		payload, signature = (token or "").rsplit(".", 1)
	except ValueError:
		frappe.throw(_("Malformed playback token."), frappe.PermissionError)

	key = get_encryption_key()
	expected = hmac.new(
		key.encode() if isinstance(key, str) else key, payload.encode(), hashlib.sha256
	).hexdigest()

	# Constant-time compare: a timing oracle here would let an attacker
	# forge a token byte by byte.
	if not hmac.compare_digest(expected, signature):
		frappe.throw(_("Invalid playback token."), frappe.PermissionError)

	claims = json.loads(base64.urlsafe_b64decode(payload.encode()))

	if is_token_expired(claims, int(time.time())):
		frappe.throw(_("Playback token expired. Reload the lesson."), frappe.PermissionError)

	if claims.get("sub") != frappe.session.user:
		# A valid token belonging to somebody else — the exact sharing
		# case DRM is bought to prevent.
		frappe.logger("lms.security").warning(
			"Playback token replay: token_sub=%s session=%s",
			claims.get("sub"),
			frappe.session.user,
		)
		frappe.throw(_("This playback token is not yours."), frappe.PermissionError)

	return claims


@frappe.whitelist()
@rate_limit(limit=1200, seconds=60 * 60)
def drm_license(playback_token: str, license_request: str) -> dict:
	"""Proxy a licence request to the vendor after checking entitlement.

	Three checks before anything reaches the key server: the token is
	ours and unexpired, it belongs to the caller, and the caller still
	has access to the lesson. The last matters because entitlement can be
	revoked between issuing a token and using it — an unenrolled student
	should stop being able to renew a licence immediately, not in five
	minutes.
	"""
	if not drm_enabled():
		frappe.throw(_("DRM playback is not enabled."))

	claims = _verify_token(playback_token)
	_require_entitlement(claims["lesson"])

	settings = _settings()
	if not settings.drm_license_url:
		frappe.throw(_("No DRM licence server configured. This requires a vendor contract (§1.2)."))

	return _forward_license_request(settings, claims, license_request)


def _forward_license_request(settings, claims: dict, license_request: str) -> dict:
	"""Hand the (opaque) challenge to the vendor and return their response.

	The body is a binary challenge from the browser's CDM; it is passed
	through untouched. Nothing here interprets it — that is the vendor's
	job, and parsing it would only create a way to get it wrong.
	"""
	import base64

	import requests

	headers = {"Content-Type": "application/octet-stream"}
	if settings.drm_license_token:
		headers["X-DRM-Token"] = settings.get_password(
			"drm_license_token", raise_exception=False
		)

	# Vendors key licences by content id; ours is derived from the lesson.
	headers["X-Content-Id"] = claims["content_id"]
	headers["X-DRM-System"] = claims["drm_system"]

	try:
		response = requests.post(
			settings.drm_license_url,
			data=base64.b64decode(license_request),
			headers=headers,
			timeout=10,
		)
		response.raise_for_status()
	except Exception as e:
		frappe.log_error(f"DRM licence request failed: {e}", "DRM Licence Error")
		frappe.throw(_("Could not obtain a playback licence. Please try again."))

	return {"license": base64.b64encode(response.content).decode()}


@frappe.whitelist()
def get_drm_status() -> dict:
	"""Configuration status for the owner portal's video settings screen."""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Not permitted."), frappe.PermissionError)

	settings = _settings()
	return {
		"enabled": bool(settings.drm_enabled),
		"mediapackage_configured": bool(settings.mediapackage_domain),
		"license_server_configured": bool(settings.drm_license_url),
		"tier": "Enterprise (DRM)" if settings.drm_enabled else "MVP (signed cookies)",
		"note": _(
			"DRM requires a third-party licence agreement; §1.2 places that procurement "
			"outside the delivery scope."
		),
	}

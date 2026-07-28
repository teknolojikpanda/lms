# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Session watermarking policy (§1.1 "kopyalama caydiricilik", §4.4.3).

Two different things wear the name "watermark", and conflating them
oversells what this does:

**Session watermark (here).** A per-viewer code drawn over the player.
It deters the common leak — someone screen-recording a lesson and
passing it around — because the recording carries a mark that resolves
to whoever made it. It does not survive re-encoding by a determined
adversary, and it is not meant to.

**Forensic watermark (Premium, §4.4.3).** An imperceptible signal
embedded in the video essence itself, surviving re-encode, crop and
camcording. That needs a specialist vendor (Nagra NexGuard, Verimatrix,
Irdeto) for both the embedder and the detector, so it sits behind the
same §1.2 procurement boundary as DRM. See `docs/watermarking.md`.

Design decision worth stating: the overlay shows an **opaque code, not
the student's email**. Burning identity onto the screen would put PII in
front of every classmate during a shared screen or a projected lesson,
which is precisely the kind of casual exposure KVKK data minimisation
(§6.4) exists to prevent. A code is equally traceable — the registry
resolves it — and harder to convincingly forge or blur than a
recognisable address.

No ``frappe`` imports, so the traceability rules are testable without a
site.
"""

from __future__ import annotations

import hashlib
import re

# Unambiguous alphabet: no O/0, I/1/L, or U/V confusion. A code read off
# a blurry screen recording is useless if its characters are guessable.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTWXYZ23456789"
CODE_LENGTH = 8
CODE_GROUP = 4  # displayed as XXXX-XXXX

CODE_PATTERN = re.compile(rf"^[{CODE_ALPHABET}]{{{CODE_LENGTH}}}$")

# How often the overlay jumps to a new position. A static corner mark is
# removed by cropping; moving it means a crop that hides it also crops
# the content.
DEFAULT_MOVE_INTERVAL_SECONDS = 20

# Kept inside the middle band so the mark cannot be cropped off without
# losing picture, but away from the dead centre where it would ruin
# viewing.
POSITION_MIN_PERCENT = 12
POSITION_MAX_PERCENT = 78


class WatermarkError(ValueError):
	"""Raised for an unusable watermark request or malformed code."""


def generate_session_code(seed: str) -> str:
	"""Derive a short, display-safe code from a unique session seed.

	Deterministic in the seed so the same session always renders the same
	code (a viewer who reloads keeps one identity), while carrying no
	recoverable information about the user — resolution goes through the
	registry, not through arithmetic.
	"""
	if not seed:
		raise WatermarkError("A session seed is required.")

	digest = hashlib.sha256(str(seed).encode("utf-8")).digest()
	base = len(CODE_ALPHABET)
	return "".join(CODE_ALPHABET[byte % base] for byte in digest[:CODE_LENGTH])


def format_code(code: str) -> str:
	"""Group the code for reading off a screen: ABCD-EFGH."""
	code = (code or "").strip().upper()
	if not CODE_PATTERN.match(code):
		raise WatermarkError(f"Not a valid watermark code: {code!r}")
	return f"{code[:CODE_GROUP]}-{code[CODE_GROUP:]}"


def normalize_code(value: str) -> str:
	"""Accept a code as transcribed by a human and return the raw form.

	Someone reporting a leak types what they see, so hyphens, spaces and
	lowercase all have to work. Anything still invalid after that is
	rejected rather than guessed at.
	"""
	if not isinstance(value, str):
		raise WatermarkError("Watermark code must be text.")

	cleaned = re.sub(r"[\s\-]", "", value).upper()
	if not CODE_PATTERN.match(cleaned):
		raise WatermarkError(f"Not a valid watermark code: {value!r}")
	return cleaned


def position_schedule(code: str, steps: int = 12) -> list[dict]:
	"""Deterministic sequence of overlay positions, as percentages.

	Derived from the code so the client needs no server round-trip to
	move the mark, and so the same session reproduces the same path —
	which matters when correlating a leaked recording against what the
	player would have drawn.
	"""
	if steps <= 0:
		raise WatermarkError("steps must be positive.")

	code = normalize_code(code)
	digest = hashlib.sha256(f"pos:{code}".encode("utf-8")).digest()
	span = POSITION_MAX_PERCENT - POSITION_MIN_PERCENT

	positions = []
	for step in range(steps):
		# Two bytes per step; extend the digest if more steps are asked for.
		offset = (step * 2) % len(digest)
		x = POSITION_MIN_PERCENT + (digest[offset] % span)
		y = POSITION_MIN_PERCENT + (digest[(offset + 1) % len(digest)] % span)
		positions.append({"x": x, "y": y})

	return positions


def build_overlay_config(
	code: str,
	label_prefix: str = "",
	move_interval_seconds: int = DEFAULT_MOVE_INTERVAL_SECONDS,
	opacity: float = 0.35,
) -> dict:
	"""Everything the player needs to draw the mark.

	Opacity is bounded: too faint and it is croppable-by-contrast or
	simply unreadable in a recording, too strong and students reasonably
	complain the lesson is hard to watch.
	"""
	code = normalize_code(code)
	return {
		"code": code,
		"display": f"{label_prefix} {format_code(code)}".strip(),
		"positions": position_schedule(code),
		"move_interval_seconds": max(5, min(int(move_interval_seconds), 300)),
		"opacity": max(0.15, min(float(opacity), 0.8)),
	}

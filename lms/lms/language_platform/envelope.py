# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""API response envelope per Technical Agreement v1.3 §7.3.

Custom language-platform endpoints wrap their payloads in the agreed
success/error envelope carrying a correlation id, while normal Frappe
endpoints keep the framework's default shape (see ADR-0001).
"""

import functools
import uuid

import frappe


def get_correlation_id() -> str:
	"""Reuse the inbound X-Correlation-Id header when present, else generate one."""
	try:
		header = frappe.get_request_header("X-Correlation-Id")
	except Exception:
		header = None
	# Never trust arbitrary header lengths/content into logs verbatim.
	if header and len(header) <= 64:
		return header
	return uuid.uuid4().hex


# Frappe raises these to steer the request, not to report a failure: a
# Redirect performs a redirect, and the auth and session errors carry
# their own status codes and challenge headers. They subclass Exception
# directly, so the catch-all below would otherwise turn a redirect into a
# 500 and an expired session into one too. Let them through untouched.
PASS_THROUGH_EXCEPTIONS = (
	frappe.exceptions.Redirect,
	frappe.exceptions.AuthenticationError,
	frappe.exceptions.SessionStopped,
	frappe.exceptions.CSRFTokenError,
)

GENERIC_ERROR_MESSAGE = (
	"Something went wrong on our side. Quote the correlation id if you report this."
)


def _discard_partial_work():
	"""Roll back whatever the failed call had already written.

	Swallowing an exception and returning a value makes the request look
	successful, and `frappe.app.sync_database` commits on that path for
	any state-changing method. Without this, a call that failed halfway
	would have its first half committed — the opposite of what the
	framework does when the exception is allowed to propagate.

	Used **only** for unforeseen exceptions. See the handled branches for
	why they must not roll back.
	"""
	try:
		frappe.db.rollback()
	except Exception:
		pass  # nothing usable to roll back; never mask the original error


def envelope(fn):
	"""Wrap a whitelisted method's return value in the §7.3 envelope."""

	@functools.wraps(fn)
	def wrapper(*args, **kwargs):
		correlation_id = get_correlation_id()
		try:
			data = fn(*args, **kwargs)
			return {"ok": True, "data": data, "meta": {"correlationId": correlation_id}}
		# Rollback is the default on every failure path, including handled
		# ones. Returning a value puts the request on frappe's success
		# path, which commits — so without this a call that wrote and then
		# threw would keep the write. `record_restore_test` is the shape
		# that matters: it saves the DR clock and then adds the audit
		# comment, and committing the clock without its audit record is
		# worse than failing outright.
		#
		# A caller that genuinely means to persist before throwing commits
		# first and says so — see the timeout finalisation in
		# lms_placement_attempt. Committed work is not ours to undo, which
		# is what makes that an opt-in rather than an exception here.
		# Before ValidationError, which this subclasses. Left to that branch a
		# throttled caller got a 417 VALIDATION_ERROR carrying "Too Many
		# Requests" as though the payload were at fault — indistinguishable
		# from a real validation failure, and stripped of the 429 that tells
		# a client to back off and retry rather than correct and resubmit.
		# `rate_limit` raises before the wrapped body runs, so nothing has
		# been written; the rollback is for consistency, not repair.
		except frappe.RateLimitExceededError as e:
			_discard_partial_work()
			frappe.clear_messages()
			frappe.local.response["http_status_code"] = 429
			return {
				"ok": False,
				"error": {
					"code": "RATE_LIMITED",
					"message": str(e) or "Too many requests. Please retry later.",
					"details": None,
				},
				"meta": {"correlationId": correlation_id},
			}
		except frappe.exceptions.ValidationError as e:
			_discard_partial_work()
			frappe.clear_messages()
			frappe.local.response["http_status_code"] = 417
			return {
				"ok": False,
				"error": {"code": "VALIDATION_ERROR", "message": str(e), "details": None},
				"meta": {"correlationId": correlation_id},
			}
		except frappe.PermissionError as e:
			_discard_partial_work()
			frappe.clear_messages()
			frappe.local.response["http_status_code"] = 403
			return {
				"ok": False,
				"error": {"code": "AUTH_FORBIDDEN", "message": str(e) or "Not permitted.", "details": None},
				"meta": {"correlationId": correlation_id},
			}
		except PASS_THROUGH_EXCEPTIONS:
			raise
		except Exception:
			# Everything unforeseen. Without this the §7.3 contract broke
			# exactly when a client most needs a structured error: a
			# KeyError or a database failure escaped as a bare Frappe
			# error, so a caller parsing the envelope got a shape it had
			# never been told about.
			_discard_partial_work()

			# The correlation id is the whole point of logging here. The
			# client is told nothing about the cause deliberately, so the
			# id is the only thread tying their report to this traceback.
			# Best-effort. log_error writes an Error Log *through the database*,
			# so the outage most likely to land here is exactly the one that
			# made this call fail — and an exception raised while reporting an
			# exception escapes the wrapper, masks the original cause, and
			# returns the bare Frappe error this branch exists to prevent.
			# Losing the log is bad; losing the envelope with it is worse.
			try:
				frappe.log_error(
					title=f"Language platform API error [{correlation_id}]",
					message=f"correlationId: {correlation_id}\nendpoint: {fn.__module__}.{fn.__name__}\n\n"
					+ frappe.get_traceback(),
				)
			except Exception:
				pass
			# log_error does not commit, and a read-only request would be
			# rolled back on the way out — taking the log with it. Commit
			# now, which is safe because the rollback above left nothing
			# else pending.
			try:
				frappe.db.commit()
			except Exception:
				pass

			frappe.clear_messages()
			frappe.local.response["http_status_code"] = 500
			return {
				"ok": False,
				"error": {
					"code": "INTERNAL_ERROR",
					"message": GENERIC_ERROR_MESSAGE,
					"details": None,
				},
				"meta": {"correlationId": correlation_id},
			}

	return wrapper

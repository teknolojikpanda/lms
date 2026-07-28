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


def envelope(fn):
	"""Wrap a whitelisted method's return value in the §7.3 envelope."""

	@functools.wraps(fn)
	def wrapper(*args, **kwargs):
		correlation_id = get_correlation_id()
		try:
			data = fn(*args, **kwargs)
			return {"ok": True, "data": data, "meta": {"correlationId": correlation_id}}
		except frappe.exceptions.ValidationError as e:
			frappe.clear_messages()
			frappe.local.response["http_status_code"] = 417
			return {
				"ok": False,
				"error": {"code": "VALIDATION_ERROR", "message": str(e), "details": None},
				"meta": {"correlationId": correlation_id},
			}
		except frappe.PermissionError as e:
			frappe.clear_messages()
			frappe.local.response["http_status_code"] = 403
			return {
				"ok": False,
				"error": {"code": "AUTH_FORBIDDEN", "message": str(e) or "Not permitted.", "details": None},
				"meta": {"correlationId": correlation_id},
			}

	return wrapper

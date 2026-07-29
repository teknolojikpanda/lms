import frappe
from frappe.tests import IntegrationTestCase

from lms.lms.language_platform.envelope import GENERIC_ERROR_MESSAGE, envelope

SECRET = "connection to db-prod-7.internal:3306 refused for user 'lms_app'"


class TestEnvelopeErrors(IntegrationTestCase):
	"""§7.3: every response carries the envelope, including the failures.

	Only ValidationError and PermissionError were wrapped, so anything
	unforeseen escaped as a bare Frappe error — the contract broke exactly
	where a client most needs a structured error to parse.
	"""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		frappe.local.response = frappe._dict()

	def _status(self):
		return frappe.local.response.get("http_status_code")

	def test_success_is_wrapped(self):
		@envelope
		def ok():
			return {"value": 1}

		result = ok()
		self.assertTrue(result["ok"])
		self.assertEqual(result["data"], {"value": 1})
		self.assertTrue(result["meta"]["correlationId"])

	def test_validation_error_is_wrapped(self):
		@envelope
		def boom():
			frappe.throw("bad input")

		result = boom()
		self.assertFalse(result["ok"])
		self.assertEqual(result["error"]["code"], "VALIDATION_ERROR")
		self.assertEqual(self._status(), 417)

	def test_permission_error_is_wrapped(self):
		@envelope
		def boom():
			frappe.throw("nope", frappe.PermissionError)

		result = boom()
		self.assertEqual(result["error"]["code"], "AUTH_FORBIDDEN")
		self.assertEqual(self._status(), 403)

	# --- the gap this closes ---------------------------------------------

	def test_unexpected_error_is_wrapped_as_500(self):
		@envelope
		def boom():
			raise KeyError("member")

		result = boom()
		self.assertFalse(result["ok"])
		self.assertEqual(result["error"]["code"], "INTERNAL_ERROR")
		self.assertEqual(self._status(), 500)
		self.assertTrue(result["meta"]["correlationId"])

	def test_the_envelope_shape_is_identical_for_every_outcome(self):
		"""A client parses one shape, whatever went wrong."""

		@envelope
		def ok():
			return 1

		@envelope
		def validation():
			frappe.throw("bad")

		@envelope
		def unexpected():
			raise TypeError("boom")

		for result in (ok(), validation(), unexpected()):
			self.assertIn("ok", result)
			self.assertIn("meta", result)
			self.assertIn("correlationId", result["meta"])
			if not result["ok"]:
				self.assertEqual(set(result["error"]), {"code", "message", "details"})

	def test_the_message_does_not_leak_internals(self):
		"""The client is told nothing about the cause, deliberately."""

		@envelope
		def boom():
			raise RuntimeError(SECRET)

		result = boom()
		body = frappe.as_json(result)
		self.assertEqual(result["error"]["message"], GENERIC_ERROR_MESSAGE)
		for leak in ("db-prod-7.internal", "lms_app", "3306", "RuntimeError", "Traceback"):
			self.assertNotIn(leak, body, f"{leak!r} leaked to the client")

	def test_the_cause_is_logged_against_the_correlation_id(self):
		"""Generic to the client, findable for an operator."""

		@envelope
		def boom():
			raise RuntimeError(SECRET)

		result = boom()
		correlation_id = result["meta"]["correlationId"]

		logs = frappe.get_all(
			"Error Log",
			filters={"error": ["like", f"%{correlation_id}%"]},
			fields=["name", "error"],
			limit=1,
		)
		self.assertEqual(len(logs), 1, "the failure was not logged against its correlation id")
		self.assertIn(SECRET, logs[0].error, "the real cause was not recorded server-side")
		frappe.delete_doc("Error Log", logs[0].name, force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_control_flow_exceptions_are_not_swallowed(self):
		"""A redirect must redirect, not become a 500."""

		@envelope
		def redirecting():
			raise frappe.exceptions.Redirect

		with self.assertRaises(frappe.exceptions.Redirect):
			redirecting()

		@envelope
		def unauthenticated():
			raise frappe.exceptions.AuthenticationError("session expired")

		with self.assertRaises(frappe.exceptions.AuthenticationError):
			unauthenticated()

	def test_partial_work_is_rolled_back(self):
		"""Swallowing the error must not let half a write commit.

		Returning normally puts the request on frappe's success path, and
		sync_database commits there for state-changing methods.
		"""
		title = f"envelope-rollback-{frappe.generate_hash(length=6)}"

		@envelope
		def half_writes():
			frappe.get_doc(
				{
					"doctype": "LMS Speaking Prompt",
					"title": title,
					"scenario": "written before the failure",
					"enabled": 1,
				}
			).insert(ignore_permissions=True)
			raise RuntimeError("failed after writing")

		result = half_writes()
		self.assertEqual(result["error"]["code"], "INTERNAL_ERROR")
		self.assertFalse(
			frappe.db.exists("LMS Speaking Prompt", {"title": title}),
			"a write made before the failure survived",
		)

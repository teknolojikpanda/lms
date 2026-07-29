"""How the OpenSearch provider authenticates, and how it fails.

Neither opensearch-py nor boto3 is installed — the platform ships with
the database provider and no AWS account — so both are stubbed. That
covers the branch taken and the message an operator is given; it proves
nothing about SigV4 signing itself, which needs a real cluster.
"""

import sys
import types

import frappe
from frappe.tests import IntegrationTestCase


class _SignerAuth:
	def __init__(self, credentials, region, service):
		self.credentials = credentials
		self.region = region
		self.service = service


def _stub_opensearch():
	root = types.ModuleType("opensearchpy")
	root.OpenSearch = lambda **kwargs: types.SimpleNamespace(**kwargs)
	root.RequestsHttpConnection = object
	root.AWSV4SignerAuth = _SignerAuth
	sys.modules["opensearchpy"] = root


def _stub_boto3(credentials):
	module = types.ModuleType("boto3")
	module.Session = lambda: types.SimpleNamespace(get_credentials=lambda: credentials)
	sys.modules["boto3"] = module


class TestOpenSearchAuth(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self._saved = {m: sys.modules.get(m) for m in ("opensearchpy", "boto3")}
		self.addCleanup(self._restore_modules)
		_stub_opensearch()

		from lms.lms.language_platform.search_providers import OpenSearchProvider

		self.provider = object.__new__(OpenSearchProvider)
		self.provider.settings = frappe._dict(
			{
				"opensearch_endpoint": "https://search.example.internal",
				"opensearch_use_iam": 1,
				"aws_region": "eu-west-1",
				"opensearch_username": "fallback-user",
			}
		)

	def _restore_modules(self):
		for name, saved in self._saved.items():
			if saved is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = saved

	def test_signs_with_the_available_credentials(self):
		_stub_boto3(credentials=object())
		auth = self.provider._iam_auth()
		self.assertEqual(auth.region, "eu-west-1")
		self.assertEqual(auth.service, "es")
		self.assertIsNotNone(auth.credentials)

	def test_missing_credentials_fail_with_an_actionable_message(self):
		"""The gap: None was passed straight to the signer.

		The failure then surfaced from inside the signer, naming neither
		IAM nor the setting that turned it on, and reads like the cluster
		being unreachable.
		"""
		_stub_boto3(credentials=None)

		with self.assertRaises(frappe.ValidationError) as caught:
			self.provider._iam_auth()

		message = str(caught.exception)
		self.assertIn("credentials", message.lower())
		self.assertIn("Authenticate with IAM", message)

	def test_missing_boto3_fails_with_an_actionable_message(self):
		sys.modules["boto3"] = None  # import raises ImportError

		with self.assertRaises(frappe.ValidationError) as caught:
			self.provider._iam_auth()
		self.assertIn("boto3", str(caught.exception))

	def test_iam_does_not_silently_fall_back_to_basic_auth(self):
		"""IAM was asked for; quietly using a password hides a broken role."""
		_stub_boto3(credentials=None)

		with self.assertRaises(frappe.ValidationError):
			self.provider._iam_auth()

	def test_basic_auth_is_used_when_iam_is_off(self):
		self.provider.settings.opensearch_use_iam = 0
		self.provider.settings.get_password = lambda *a, **k: "secret"

		client = self.provider._client()
		self.assertEqual(client.http_auth, ("fallback-user", "secret"))

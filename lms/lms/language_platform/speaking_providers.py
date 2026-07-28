# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Pluggable transcription/scoring providers for speaking assessment (§4.9, §8.13).

Two implementations ship with the app:

- ``MockProvider`` (default): fully offline and deterministic. Lets every
  environment (dev, CI, demo) run the complete pipeline without AWS
  credentials or per-minute cost.
- ``AWSProvider``: Amazon Transcribe (batch) + Amazon Bedrock rubric scoring,
  matching the agreement's reference pipeline. Requires ``boto3`` and
  configuration in **LMS Language Settings**.

The provider only produces raw material (transcript, rubric proposal).
Persistence, state transitions and retries live in ``speaking_pipeline``.
"""

from __future__ import annotations

import hashlib
import json

import frappe
from frappe import _

RUBRIC_DIMENSIONS = ["Fluency", "Grammar", "Vocabulary", "Coherence", "Task Completion"]

SCORING_PROMPT = """You are an examiner grading a language learner's spoken response.
Scenario: {scenario}
Target level: {level}
Transcript: {transcript}
Objective metrics: {metrics}

Score each dimension 0-100 and give one short feedback line and one concrete,
repeatable improvement tip per dimension. Respond with JSON only:
{{"scores": [{{"dimension": "...", "score": 0, "feedback": "...", "improvement_tip": "..."}}]}}
Dimensions: Fluency, Grammar, Vocabulary, Coherence, Task Completion.
"""


def get_provider():
	settings = frappe.get_cached_doc("LMS Language Settings")
	if (settings.speaking_provider or "Mock") == "AWS":
		return AWSProvider(settings)
	return MockProvider(settings)


class MockProvider:
	"""Deterministic offline provider — same audio file, same result."""

	def __init__(self, settings):
		self.settings = settings

	def transcribe(self, submission) -> str:
		# Without a real STT engine there is nothing to transcribe; return a
		# deterministic placeholder so downstream metrics/scoring are stable.
		digest = hashlib.sha256((submission.audio_file or submission.name).encode()).hexdigest()
		sample_sentences = [
			"I would like to talk about my daily routine and my plans for the weekend.",
			"In my opinion the best way to learn a language is to practice speaking every day.",
			"Last summer I visited my grandparents and we spent time in the garden together.",
			"I usually take the bus to school because it is cheaper than driving a car.",
		]
		picked = sample_sentences[int(digest[:8], 16) % len(sample_sentences)]
		return f"{picked} This is a mock transcript generated for offline evaluation."

	def score(self, submission, transcript: str, metrics: dict) -> list[dict]:
		digest = hashlib.sha256(f"{submission.name}|{transcript}".encode()).hexdigest()
		scores = []
		for i, dimension in enumerate(RUBRIC_DIMENSIONS):
			base = 55 + int(digest[i * 2 : i * 2 + 2], 16) % 36  # 55-90, deterministic
			scores.append(
				{
					"dimension": dimension,
					"score": float(base),
					"feedback": f"{dimension}: solid attempt; assessed by the offline mock scorer.",
					"improvement_tip": f"Practice one {dimension.lower()} exercise daily and re-record this scenario.",
				}
			)
		return scores


class AWSProvider:
	"""Amazon Transcribe + Bedrock adapter (agreement reference pipeline)."""

	def __init__(self, settings):
		self.settings = settings
		try:
			import boto3  # noqa: F401
		except ImportError:
			frappe.throw(_("boto3 is required for the AWS speaking provider. pip install boto3"))

	def _client(self, service):
		import boto3

		return boto3.client(service, region_name=self.settings.aws_region or "eu-central-1")

	def transcribe(self, submission) -> str:
		"""Run a batch Transcribe job on the submission's audio file.

		The audio must live on S3 (production setup per §8.13 — presigned
		upload to the audio bucket). ``audio_file`` should therefore hold an
		``s3://`` URI in AWS mode.
		"""
		import time
		import urllib.request

		if not (submission.audio_file or "").startswith("s3://"):
			frappe.throw(_("AWS provider expects the audio file to be an s3:// URI."))

		client = self._client("transcribe")
		job_name = f"lms-speaking-{submission.name}".replace(" ", "-")[:200]
		client.start_transcription_job(
			TranscriptionJobName=job_name,
			Media={"MediaFileUri": submission.audio_file},
			LanguageCode=self.settings.transcribe_language_code or "en-US",
		)
		# Batch polling with backoff; overall pipeline timeout guards runaway jobs.
		for delay in (5, 10, 15, 30, 30, 60, 60, 120):
			time.sleep(delay)
			job = client.get_transcription_job(TranscriptionJobName=job_name)["TranscriptionJob"]
			status = job["TranscriptionJobStatus"]
			if status == "COMPLETED":
				uri = job["Transcript"]["TranscriptFileUri"]
				with urllib.request.urlopen(uri) as response:
					payload = json.loads(response.read().decode("utf-8"))
				return payload["results"]["transcripts"][0]["transcript"]
			if status == "FAILED":
				raise RuntimeError(job.get("FailureReason") or "Transcribe job failed")
		raise TimeoutError("Transcribe job did not complete in time")

	def score(self, submission, transcript: str, metrics: dict) -> list[dict]:
		prompt_doc = frappe.db.get_value(
			"LMS Speaking Prompt", submission.prompt, ["scenario", "language_level"], as_dict=True
		)
		body = {
			"messages": [
				{
					"role": "user",
					"content": SCORING_PROMPT.format(
						scenario=frappe.utils.strip_html_tags(prompt_doc.scenario or ""),
						level=prompt_doc.language_level or "unknown",
						transcript=transcript,
						metrics=json.dumps(metrics),
					),
				}
			],
			"max_tokens": 800,
		}
		client = self._client("bedrock-runtime")
		response = client.invoke_model(
			modelId=self.settings.bedrock_model_id,
			body=json.dumps(body),
		)
		payload = json.loads(response["body"].read())
		text = payload.get("content", [{}])[0].get("text") or payload.get("outputs", [{}])[0].get("text", "")
		return parse_rubric_response(text)


def parse_rubric_response(text: str) -> list[dict]:
	"""Extract and validate the rubric JSON from an LLM response."""
	start, end = text.find("{"), text.rfind("}")
	if start == -1 or end == -1:
		raise ValueError("Scoring model returned no JSON object")
	data = json.loads(text[start : end + 1])
	scores = data.get("scores")
	if not isinstance(scores, list) or not scores:
		raise ValueError("Scoring model returned no scores")

	cleaned = []
	for row in scores:
		dimension = row.get("dimension")
		if dimension not in RUBRIC_DIMENSIONS:
			continue
		score = max(0.0, min(100.0, float(row.get("score", 0))))
		cleaned.append(
			{
				"dimension": dimension,
				"score": score,
				"feedback": (row.get("feedback") or "")[:500],
				"improvement_tip": (row.get("improvement_tip") or "")[:500],
			}
		)
	if not cleaned:
		raise ValueError("Scoring model returned no valid dimensions")
	return cleaned

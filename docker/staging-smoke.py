# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""End-to-end exercise of the language-platform modules against a real site.

Run inside the staging bench:

    bench --site staging.localhost execute \\
        lms.lms.language_platform.staging_smoke.run

or, from the repo copy mounted into the container:

    bench --site staging.localhost console < docker/staging-smoke.py

Every check reports pass/fail independently and the script continues after
a failure, because the point is to find *all* the defects in one run
rather than the first one.
"""

import json
import traceback

import frappe

RESULTS = []

# Plain module dict rather than frappe.local: frappe.local raises
# AttributeError for anything unset, so one failed check cascaded into
# every later one and buried the real defect count.
STATE = {}


def dumps(value):
	"""JSON for assertions only.

	`default=str` because the APIs return datetimes, which Frappe's own
	response encoder handles but the stdlib's does not — the difference
	is a property of this test, not of the code under test.
	"""
	return json.dumps(value, default=str)


def check(name):
	"""Decorator: run a check, record the outcome, never stop the run."""

	def wrapper(fn):
		try:
			detail = fn()
			RESULTS.append({"check": name, "status": "PASS", "detail": detail or ""})
			print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))
		except Exception as e:
			RESULTS.append(
				{"check": name, "status": "FAIL", "detail": f"{type(e).__name__}: {e}"}
			)
			print(f"  FAIL  {name}")
			print("        " + traceback.format_exc().replace("\n", "\n        ")[:1500])
		return fn

	return wrapper


def run():
	frappe.set_user("Administrator")
	print("\n=== Language platform staging smoke ===\n")

	RESULTS.clear()
	STATE.clear()
	_reset_state()

	_offline()
	_doctypes()
	_settings()
	_placement()
	_overlays()
	_speaking()
	_accessibility()
	_search()
	_watermark()
	_dashboards()
	_tenants()
	_dsar()
	_dr()

	passed = sum(1 for r in RESULTS if r["status"] == "PASS")
	failed = sum(1 for r in RESULTS if r["status"] == "FAIL")
	print(f"\n=== {passed} passed, {failed} failed ===\n")
	if failed:
		print("Failures:")
		for r in RESULTS:
			if r["status"] == "FAIL":
				print(f"  - {r['check']}: {r['detail']}")
	return {"passed": passed, "failed": failed, "results": RESULTS}


# --- offline ---------------------------------------------------------------------


def _offline():
	"""The platform must run with no AWS account and no outbound network.

	Every AWS-backed feature is opt-in, and these checks exist so a future
	change cannot quietly flip a default and make a working offline
	install start reaching for credentials it does not have.
	"""
	print("[offline]")

	@check("no AWS provider is selected by default")
	def _():
		settings = frappe.get_single("LMS Language Settings")
		active = {
			"speaking_provider": settings.speaking_provider or "Mock",
			"search_provider": settings.search_provider or "Database",
			"drm_enabled": bool(settings.drm_enabled),
			"watermark_enabled": bool(settings.watermark_enabled),
		}
		if active["speaking_provider"] != "Mock":
			raise AssertionError(f"speaking provider is {active['speaking_provider']}, not Mock")
		if active["search_provider"] != "Database":
			raise AssertionError(f"search provider is {active['search_provider']}, not Database")
		if active["drm_enabled"]:
			raise AssertionError("DRM is enabled; it requires a vendor licence server")
		return "speaking=Mock, search=Database, drm=off"

	@check("no AWS SDK is imported at module scope")
	def _():
		# boto3 is an optional dependency. If any module imported it at
		# import time, the app would fail to load without it installed.
		import sys

		for module in ("boto3", "botocore", "opensearchpy"):
			if module in sys.modules:
				raise AssertionError(f"{module} was imported during app load")
		return "boto3/opensearch-py not loaded"

	@check("providers resolve to the offline implementations")
	def _():
		from lms.lms.language_platform.search_providers import get_provider as search_provider
		from lms.lms.language_platform.speaking_providers import get_provider as speaking_provider

		speaking = speaking_provider()
		search = search_provider()
		if type(speaking).__name__ != "MockProvider":
			raise AssertionError(f"speaking resolved to {type(speaking).__name__}")
		if type(search).__name__ != "DatabaseSearchProvider":
			raise AssertionError(f"search resolved to {type(search).__name__}")
		return f"{type(speaking).__name__} + {type(search).__name__}"


# --- schema -------------------------------------------------------------------

NEW_DOCTYPES = [
	"LMS Placement Blueprint",
	"LMS Placement Blueprint Segment",
	"LMS Placement Level Mapping",
	"LMS Placement Attempt",
	"LMS Placement Attempt Question",
	"LMS Video Overlay",
	"LMS Overlay Response",
	"LMS Speaking Prompt",
	"LMS Speaking Submission",
	"LMS Speaking Rubric Score",
	"LMS Language Settings",
	"LMS Data Request",
	"LMS Tenant",
	"LMS Watermark Session",
	"LMS Accessibility Preference",
]


def _doctypes():
	print("[schema]")

	@check("all new doctypes exist")
	def _():
		missing = [dt for dt in NEW_DOCTYPES if not frappe.db.exists("DocType", dt)]
		if missing:
			raise AssertionError(f"missing: {missing}")
		return f"{len(NEW_DOCTYPES)} doctypes"

	@check("doctype tables are queryable")
	def _():
		for dt in NEW_DOCTYPES:
			if frappe.db.get_value("DocType", dt, "issingle"):
				continue
			frappe.get_all(dt, limit=1)
		return "all tables respond"

	@check("LMS Question has CEFR metadata columns")
	def _():
		meta = frappe.get_meta("LMS Question")
		for field in ("language_level", "language_skill", "topic", "difficulty"):
			if not meta.get_field(field):
				raise AssertionError(f"LMS Question missing {field}")
		return "level/skill/topic/difficulty present"


def _settings():
	print("[settings]")

	@check("LMS Language Settings singleton loads with defaults")
	def _():
		settings = frappe.get_single("LMS Language Settings")
		settings.save(ignore_permissions=True)
		frappe.db.commit()
		return (
			f"provider={settings.speaking_provider}, "
			f"audio_retention={settings.audio_retention_days}d, "
			f"search={settings.search_provider}"
		)


# --- placement -----------------------------------------------------------------

BLUEPRINT_TITLE = "[smoke] Placement"


def _placement():
	print("[placement]")

	@check("create question pool with CEFR metadata")
	def _():
		created = 0
		for skill in ("Grammar", "Vocabulary"):
			for i in range(6):
				name = f"smoke-{skill}-{i}"
				if frappe.db.exists("LMS Question", {"question": name}):
					continue
				frappe.get_doc(
					{
						"doctype": "LMS Question",
						"question": name,
						"type": "Choices",
						"language_skill": skill,
						"language_level": "A1",
						"topic": "smoke",
						"difficulty": 3,
						"option_1": "right",
						"is_correct_1": 1,
						"option_2": "wrong",
					}
				).insert(ignore_permissions=True)
				created += 1
		frappe.db.commit()
		return f"{created} questions created"

	@check("create placement blueprint")
	def _():
		existing = frappe.db.get_value("LMS Placement Blueprint", {"title": BLUEPRINT_TITLE})
		if existing:
			return f"reused {existing}"
		doc = frappe.get_doc(
			{
				"doctype": "LMS Placement Blueprint",
				"title": BLUEPRINT_TITLE,
				"enabled": 1,
				"max_attempts": 50,
				"duration": 30,
				"default_level": "A1",
				"segments": [
					{"skill": "Grammar", "level": "A1", "question_count": 2},
					{"skill": "Vocabulary", "level": "A1", "question_count": 2},
				],
				"level_mappings": [
					{"min_score": 0, "level": "A1"},
					{"min_score": 50, "level": "A2"},
					{"min_score": 80, "level": "B1"},
				],
			}
		)
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		return doc.name

	@check("blueprint rejects an empty segment list")
	def _():
		try:
			frappe.get_doc(
				{
					"doctype": "LMS Placement Blueprint",
					"title": "[smoke] invalid",
					"segments": [],
					"level_mappings": [{"min_score": 0, "level": "A1"}],
				}
			).insert(ignore_permissions=True)
		except frappe.ValidationError:
			return "validation fired as designed"
		raise AssertionError("empty blueprint was accepted")

	@check("start placement attempt (deterministic selection)")
	def _():
		from lms.lms.doctype.lms_placement_attempt.lms_placement_attempt import start_placement

		blueprint = frappe.db.get_value("LMS Placement Blueprint", {"title": BLUEPRINT_TITLE})
		attempt = start_placement(blueprint)
		if len(attempt["questions"]) != 4:
			raise AssertionError(f"expected 4 questions, got {len(attempt['questions'])}")
		# The student payload must never carry the answer key.
		blob = dumps(attempt)
		if "is_correct" in blob:
			raise AssertionError("answer key leaked into the attempt payload")
		STATE["attempt"] = attempt["name"]
		return f"{attempt['name']} with {len(attempt['questions'])} questions, no answer key"

	@check("seed is persisted for audit")
	def _():
		seed = frappe.db.get_value("LMS Placement Attempt", STATE["attempt"], "seed")
		if not seed:
			raise AssertionError("no seed recorded")
		return f"seed {seed[:16]}…"

	@check("autosave an answer")
	def _():
		from lms.lms.doctype.lms_placement_attempt.lms_placement_attempt import (
			save_placement_answer,
		)

		attempt = frappe.get_doc("LMS Placement Attempt", STATE["attempt"])
		question = attempt.questions[0].question
		save_placement_answer(attempt.name, question, json.dumps(["right"]))
		frappe.db.commit()
		saved = frappe.get_doc("LMS Placement Attempt", attempt.name).questions[0].answer
		if not saved:
			raise AssertionError("answer not persisted")
		return "answer stored"

	@check("submit and grade server-side")
	def _():
		from lms.lms.doctype.lms_placement_attempt.lms_placement_attempt import submit_placement

		attempt = frappe.get_doc("LMS Placement Attempt", STATE["attempt"])
		answers = {row.question: ["right"] for row in attempt.questions}
		result = submit_placement(attempt.name, json.dumps(answers))
		if result["percentage"] != 100:
			raise AssertionError(f"expected 100%, got {result['percentage']}")
		if not result["result_level"]:
			raise AssertionError("no level assigned")
		return f"{result['percentage']}% → {result['result_level']}"

	@check("resubmitting a completed attempt is refused")
	def _():
		from lms.lms.doctype.lms_placement_attempt.lms_placement_attempt import submit_placement

		try:
			submit_placement(STATE["attempt"])
		except frappe.ValidationError:
			return "second submit blocked"
		raise AssertionError("completed attempt accepted a second submit")

	@check("level override requires a reason and is audited")
	def _():
		from lms.lms.doctype.lms_placement_attempt.lms_placement_attempt import (
			override_placement_level,
		)

		try:
			override_placement_level(STATE["attempt"], "B1", "   ")
			raise AssertionError("blank reason accepted")
		except frappe.ValidationError:
			pass

		override_placement_level(STATE["attempt"], "B1", "Smoke test override")
		frappe.db.commit()
		doc = frappe.get_doc("LMS Placement Attempt", STATE["attempt"])
		if doc.override_level != "B1" or not doc.override_by:
			raise AssertionError("override not recorded")
		return f"override → {doc.override_level} by {doc.override_by}"


# --- overlays --------------------------------------------------------------------


def _make_lesson() -> str:
	"""Minimal course → chapter → lesson so overlays have something to attach to."""
	course = frappe.db.get_value("LMS Course", {"title": "[smoke] Course"}, "name")
	if not course:
		course = (
			frappe.get_doc(
				{
					"doctype": "LMS Course",
					"title": "[smoke] Course",
					"short_introduction": "smoke fixture",
					"description": "smoke fixture",
					"published": 1,
					# LMS Course requires an instructor, same as LMS Batch.
					"instructors": [{"instructor": "Administrator"}],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	chapter = frappe.get_doc(
		{"doctype": "Course Chapter", "title": "[smoke] Chapter", "course": course}
	).insert(ignore_permissions=True)

	lesson = frappe.get_doc(
		{
			"doctype": "Course Lesson",
			"title": "[smoke] Lesson",
			"chapter": chapter.name,
			"course": course,
			"content": frappe.as_json({"blocks": []}),
		}
	).insert(ignore_permissions=True)

	frappe.db.commit()
	return lesson.name


def _reset_state():
	"""Clear the settings this run mutates.

	Without it a second run reads state left by the first and reports
	failures that are artefacts of ordering rather than defects — which is
	exactly what happened on the first pass.
	"""
	settings = frappe.get_single("LMS Language Settings")
	settings.watermark_enabled = 0
	settings.last_restore_test_at = None
	settings.last_dr_drill_at = None
	settings.save(ignore_permissions=True)

	for name in frappe.get_all(
		"LMS Accessibility Preference", filters={"member": "Administrator"}, pluck="name"
	):
		frappe.delete_doc("LMS Accessibility Preference", name, ignore_permissions=True, force=True)

	frappe.db.commit()
	frappe.clear_cache()


def _overlays():
	print("[overlays]")

	@check("create a note overlay")
	def _():
		# A bare site has no lessons, so make one rather than skipping the
		# whole overlay section on an empty install.
		lesson = frappe.db.get_value("Course Lesson", {}, "name") or _make_lesson()
		STATE["lesson"] = lesson

		existing = frappe.db.get_value("LMS Video Overlay", {"lesson": lesson, "type": "Note"})
		if existing:
			STATE["overlay"] = existing
			return f"reused {existing}"

		doc = frappe.get_doc(
			{
				"doctype": "LMS Video Overlay",
				"lesson": lesson,
				"timestamp_ms": 5000,
				"type": "Note",
				"scope": "Course",
				"note_text": "smoke note",
				"published": 1,
			}
		)
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		STATE["overlay"] = doc.name
		return doc.name

	@check("version increments on edit (optimistic locking)")
	def _():
		doc = frappe.get_doc("LMS Video Overlay", STATE["overlay"])
		before = doc.version or 1
		doc.note_text = "smoke note edited"
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		after = frappe.db.get_value("LMS Video Overlay", doc.name, "version")
		if after <= before:
			raise AssertionError(f"version did not advance: {before} → {after}")
		return f"version {before} → {after}"

	@check("stale write is rejected")
	def _():
		from lms.lms.doctype.lms_video_overlay.lms_video_overlay import save_overlay

		current = frappe.db.get_value("LMS Video Overlay", STATE["overlay"], "version")
		try:
			save_overlay(
				json.dumps({"name": STATE["overlay"], "note_text": "stale"}),
				expected_version=current - 1,
			)
		except frappe.ValidationError:
			return "stale version refused"
		raise AssertionError("stale write was accepted")

	@check("question overlay must link an auto-gradable question")
	def _():
		try:
			frappe.get_doc(
				{
					"doctype": "LMS Video Overlay",
					"lesson": STATE["lesson"],
					"timestamp_ms": 9000,
					"type": "Question",
					"scope": "Course",
				}
			).insert(ignore_permissions=True)
		except frappe.ValidationError:
			return "unlinked question overlay refused"
		raise AssertionError("question overlay without a question was accepted")

	@check("fetch overlays for a lesson")
	def _():
		from lms.lms.doctype.lms_video_overlay.lms_video_overlay import get_lesson_overlays

		overlays = get_lesson_overlays(STATE["lesson"])
		return f"{len(overlays)} overlay(s) returned"


# --- speaking ---------------------------------------------------------------------


def _speaking():
	print("[speaking]")

	@check("create speaking prompt")
	def _():
		existing = frappe.db.get_value("LMS Speaking Prompt", {"title": "[smoke] Prompt"})
		if existing:
			STATE["prompt"] = existing
			return f"reused {existing}"
		doc = frappe.get_doc(
			{
				"doctype": "LMS Speaking Prompt",
				"title": "[smoke] Prompt",
				"enabled": 1,
				"language_level": "A2",
				"max_duration_seconds": 120,
				"scenario": "Describe your day.",
			}
		)
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		STATE["prompt"] = doc.name
		return doc.name

	@check("recording over the duration limit is refused")
	def _():
		try:
			frappe.get_doc(
				{
					"doctype": "LMS Speaking Submission",
					"member": "Administrator",
					"prompt": STATE["prompt"],
					"audio_file": "/private/files/smoke.webm",
					"duration_seconds": 9999,
				}
			).insert(ignore_permissions=True)
		except frappe.ValidationError:
			return "duration limit enforced"
		raise AssertionError("over-length recording accepted")

	@check("submission runs through the mock pipeline to Ready")
	def _():
		from lms.lms.language_platform.speaking_pipeline import process_submission

		doc = frappe.get_doc(
			{
				"doctype": "LMS Speaking Submission",
				"member": "Administrator",
				"prompt": STATE["prompt"],
				"audio_file": "/private/files/smoke.webm",
				"duration_seconds": 60,
			}
		)
		# flags.in_test would skip enqueue; call the pipeline directly so the
		# state machine is exercised synchronously.
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		STATE["submission"] = doc.name

		process_submission(doc.name)
		doc.reload()
		if doc.status != "Ready":
			raise AssertionError(f"status {doc.status}, error: {doc.error_message}")
		if not doc.rubric_scores:
			raise AssertionError("no rubric scores produced")
		return (
			f"{doc.status}, {len(doc.rubric_scores)} dimensions, "
			f"ai={doc.ai_total_score}, wpm={doc.wpm}"
		)

	@check("teacher override preserves the AI score")
	def _():
		from lms.lms.doctype.lms_speaking_submission.lms_speaking_submission import (
			override_speaking_score,
		)

		doc = frappe.get_doc("LMS Speaking Submission", STATE["submission"])
		ai_before = doc.ai_total_score
		override_speaking_score(doc.name, 88, "Smoke override")
		frappe.db.commit()
		doc.reload()
		if doc.final_score != 88 or doc.ai_total_score != ai_before:
			raise AssertionError(
				f"final={doc.final_score} ai={doc.ai_total_score} (was {ai_before})"
			)
		return f"final={doc.final_score}, ai preserved at {doc.ai_total_score}"

	@check("pipeline is idempotent on a Ready submission")
	def _():
		from lms.lms.language_platform.speaking_pipeline import process_submission

		doc = frappe.get_doc("LMS Speaking Submission", STATE["submission"])
		before = doc.final_score
		process_submission(doc.name)
		doc.reload()
		if doc.final_score != before:
			raise AssertionError("re-processing changed a completed submission")
		return "no-op as designed"


# --- accessibility ------------------------------------------------------------------


def _accessibility():
	print("[accessibility]")

	@check("defaults returned for a user with no preferences")
	def _():
		from lms.lms.language_platform.accessibility import get_accessibility_preferences

		prefs = get_accessibility_preferences()
		if prefs["font_step"] != 3 or len(prefs["font_steps"]) != 6:
			raise AssertionError(f"unexpected defaults: {prefs}")
		return f"step={prefs['font_step']}, {len(prefs['font_steps'])} steps offered"

	@check("save and read back preferences")
	def _():
		from lms.lms.language_platform.accessibility import (
			get_accessibility_preferences,
			save_accessibility_preferences,
		)

		save_accessibility_preferences(font_step=5, contrast_mode="high", whiteboard_mode=1)
		frappe.db.commit()
		prefs = get_accessibility_preferences()
		if prefs["font_step"] != 5 or prefs["contrast_mode"] != "high":
			raise AssertionError(f"round-trip failed: {prefs}")
		return f"step={prefs['font_step']}, contrast={prefs['contrast_mode']}"

	@check("invalid font step refused")
	def _():
		from lms.lms.language_platform.accessibility import save_accessibility_preferences

		try:
			save_accessibility_preferences(font_step=99)
		except frappe.ValidationError:
			return "out-of-range step refused"
		raise AssertionError("font step 99 accepted")

	@check("contrast audit computes AAA ratios")
	def _():
		from lms.lms.language_platform.accessibility import get_contrast_audit

		audit = get_contrast_audit()
		if not audit["all_pass"]:
			failing = [r["name"] for r in audit["results"] if not r["passes"]]
			raise AssertionError(f"palette below AAA: {failing}")
		lowest = min(r["ratio"] for r in audit["results"])
		return f"all pass, lowest {lowest}:1"


# --- search -------------------------------------------------------------------------


def _search():
	print("[search]")

	@check("database provider returns question matches")
	def _():
		from lms.lms.language_platform.search import search as platform_search

		result = platform_search("smoke", "LMS Question", limit=5)
		if not result["results"]:
			raise AssertionError("no results for a term known to exist")
		blob = dumps(result)
		if "is_correct" in blob:
			raise AssertionError("answer key leaked through search results")
		return f"{len(result['results'])} hit(s) via {result['provider']}, no answer key"

	@check("searching an unknown source is refused")
	def _():
		from lms.lms.language_platform.search import search as platform_search

		try:
			platform_search("smoke", "User", limit=5)
		except frappe.PermissionError:
			return "unknown source refused"
		raise AssertionError("arbitrary doctype was searchable")

	@check("too-short query refused")
	def _():
		from lms.lms.language_platform.search import search as platform_search

		try:
			platform_search("a", "LMS Question")
		except frappe.ValidationError:
			return "short query refused"
		raise AssertionError("one-character query accepted")


# --- watermark -----------------------------------------------------------------------


def _watermark():
	print("[watermark]")

	@check("watermark disabled by default")
	def _():
		from lms.lms.language_platform.watermark import get_watermark

		config = get_watermark(STATE["lesson"])
		if config.get("enabled"):
			raise AssertionError("watermark on without being configured")
		return "off as shipped"

	@check("issues a traceable session when enabled")
	def _():
		from lms.lms.language_platform.watermark import get_watermark, trace_watermark

		settings = frappe.get_single("LMS Language Settings")
		settings.watermark_enabled = 1
		settings.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.clear_cache()

		config = get_watermark(STATE["lesson"])
		if not config.get("enabled"):
			raise AssertionError("watermark still disabled after enabling")
		frappe.db.commit()

		traced = trace_watermark(config["code"])
		if not traced["found"] or traced["member"] != "Administrator":
			raise AssertionError(f"trace failed: {traced}")
		return f"code {config['display']} traced to {traced['member']}"

	@check("malformed code rejected rather than guessed")
	def _():
		from lms.lms.language_platform.watermark import trace_watermark

		try:
			trace_watermark("NOTACODE")
		except frappe.ValidationError:
			return "invalid code refused"
		raise AssertionError("invalid code accepted")


# --- dashboards -------------------------------------------------------------------------
#
# These run raw SQL, which unit tests cannot exercise: the first time the
# institution dashboard met MariaDB it failed with error 1247 (a HAVING
# clause referencing an aggregate alias). Anything issuing hand-written
# SQL needs a check that actually executes it.


def _dashboards():
	print("[dashboards]")

	@check("institution dashboard query runs against MariaDB")
	def _():
		from lms.lms.language_platform.admin_api import get_admin_dashboard

		data = get_admin_dashboard()
		for key in ("kpis", "placement_distribution", "activity", "risky_students", "pending_grading"):
			if key not in data:
				raise AssertionError(f"missing section: {key}")
		return (
			f"{data['kpis']['active_students']} students, "
			f"{len(data['risky_students'])} at risk, "
			f"{len(data['activity']['lesson_progress'])}-day activity series"
		)

	@check("owner dashboard query runs against MariaDB")
	def _():
		from lms.lms.language_platform.admin_api import get_owner_dashboard

		data = get_owner_dashboard()
		for key in ("kpis", "trends", "ops", "cost_estimate"):
			if key not in data:
				raise AssertionError(f"missing section: {key}")
		return (
			f"{data['kpis']['total_users']} users, "
			f"cost estimate ${data['cost_estimate']['total_usd']}"
		)

	@check("DR readiness query runs")
	def _():
		from lms.lms.language_platform.dr import get_dr_readiness

		data = get_dr_readiness()
		return f"status={data['status']}, {len(data['rto_plan'])} RTO phases"


# --- tenants --------------------------------------------------------------------------


def _tenants():
	print("[tenants]")

	@check("register a tenant")
	def _():
		from lms.lms.doctype.lms_tenant.lms_tenant import register_tenant

		if frappe.db.exists("LMS Tenant", "smoke-kolej"):
			return "reused smoke-kolej"
		result = register_tenant(
			tenant_name="Smoke Koleji", subdomain="smoke-kolej", seat_limit=10
		)
		frappe.db.commit()
		return f"{result['name']} status={result['status']}"

	@check("reserved subdomain refused")
	def _():
		from lms.lms.doctype.lms_tenant.lms_tenant import register_tenant

		try:
			register_tenant(tenant_name="Bad", subdomain="www")
		except frappe.ValidationError:
			return "reserved name refused"
		raise AssertionError("reserved subdomain accepted")

	@check("invalid lifecycle transition refused")
	def _():
		doc = frappe.get_doc("LMS Tenant", "smoke-kolej")
		try:
			doc.transition_to("Purged")
		except frappe.ValidationError:
			return "Requested → Purged blocked"
		raise AssertionError("illegal transition accepted")

	@check("purge refused while guards are unmet")
	def _():
		from lms.lms.doctype.lms_tenant.lms_tenant import mark_purged

		try:
			mark_purged("smoke-kolej")
		except frappe.ValidationError:
			return "purge guards held"
		raise AssertionError("purge succeeded without guards")

	@check("roster CSV import creates users and classes")
	def _():
		from lms.lms.language_platform.tenant_setup import import_roster_csv

		csv = (
			"email,first_name,last_name,class_name\n"
			"smoke.a@loadtest.invalid,Ada,One,Smoke Class\n"
			"smoke.b@loadtest.invalid,Bob,Two,Smoke Class\n"
			"bad-email,Broken,Row,Smoke Class\n"
			"smoke.a@loadtest.invalid,Dup,Row,Smoke Class\n"
		)
		result = import_roster_csv(csv)
		if result["imported"] < 1:
			raise AssertionError(f"nothing imported: {result}")
		if result["failed"] != 2:
			raise AssertionError(f"expected 2 rejects (bad email + duplicate), got {result}")
		return f"{result['imported']} imported, {result['failed']} rejected as designed"


# --- DSAR -----------------------------------------------------------------------------


def _dsar():
	print("[dsar]")

	@check("self-service export produces a file")
	def _():
		from lms.lms.doctype.lms_data_request.lms_data_request import export_my_data

		result = export_my_data()
		frappe.db.commit()
		if not result.get("file_url"):
			raise AssertionError(f"no export file: {result}")
		return f"{result['name']}, {len(result['counts'])} doctype(s) exported"

	@check("erasure request requires approval by someone else")
	def _():
		from lms.lms.doctype.lms_data_request.lms_data_request import (
			approve_data_request,
			create_data_request,
		)

		request = create_data_request(
			subject_user="smoke.b@loadtest.invalid",
			request_type="Erasure",
			reason="Smoke test",
		)
		frappe.db.commit()
		try:
			approve_data_request(request["name"])
		except frappe.PermissionError:
			return "self-approval of erasure blocked (four-eyes)"
		raise AssertionError("requester approved their own erasure")

	@check("system accounts cannot be erasure subjects")
	def _():
		from lms.lms.doctype.lms_data_request.lms_data_request import create_data_request

		try:
			create_data_request(
				subject_user="Administrator", request_type="Erasure", reason="Smoke"
			)
		except frappe.ValidationError:
			return "Administrator refused as subject"
		raise AssertionError("Administrator accepted as erasure subject")


# --- DR --------------------------------------------------------------------------------


def _dr():
	print("[dr]")

	@check("readiness reports unknown/breach before any test is recorded")
	def _():
		from lms.lms.language_platform.dr import get_dr_readiness

		readiness = get_dr_readiness()
		if readiness["status"] == "ok":
			raise AssertionError("reported healthy with no restore test ever recorded")
		return f"status={readiness['status']}, {len(readiness['checks'])} checks"

	@check("failed restore test does not reset the clock")
	def _():
		from lms.lms.language_platform.dr import record_restore_test

		result = record_restore_test("fail", "Smoke: deliberate failure")
		frappe.db.commit()
		if result["clock_reset"]:
			raise AssertionError("a failed restore test reset the clock")
		settings = frappe.get_single("LMS Language Settings")
		if settings.last_restore_test_at:
			raise AssertionError("failed test recorded as a success")
		return "failure recorded, clock untouched"

	@check("passing restore test resets the clock")
	def _():
		from lms.lms.language_platform.dr import record_restore_test

		result = record_restore_test("pass", "Smoke: verified restore")
		frappe.db.commit()
		if not result["clock_reset"]:
			raise AssertionError("passing test did not reset the clock")
		return "clock reset on pass"

	@check("drill requires notes")
	def _():
		from lms.lms.language_platform.dr import record_dr_drill

		try:
			record_dr_drill("")
		except frappe.ValidationError:
			return "notes enforced"
		raise AssertionError("drill recorded without notes")

# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

"""Seed fixtures for the k6 load suite (§6.6).

Run against a **non-production** site::

	bench --site <site> execute lms.load_test_seed.seed --kwargs "{'users': 1000}"

or, since this file lives outside the app package, via bench console::

	bench --site <site> console
	>>> exec(open('load-tests/seed/seed_load_test_data.py').read())
	>>> seed(users=1000)

Creates:
  * N enabled LMS Student users sharing one password (one per k6 VU — the
    load scripts deliberately use a distinct account per VU so per-user
    rate limits do not distort the results)
  * a question pool with CEFR metadata large enough to satisfy the blueprint
  * a placement blueprint with generous max_attempts so a VU can loop
  * a speaking prompt and a dummy audio File for the pipeline scenario

Everything it creates is tagged with the ``loadtest`` prefix so ``cleanup()``
can remove it again.
"""

import frappe
from frappe.utils import cint

USER_PREFIX = "loadtest"
USER_DOMAIN = "loadtest.invalid"
USER_PASSWORD = "loadtest-password"
FIXTURE_TAG = "loadtest"

SKILLS = ["Grammar", "Vocabulary", "Reading", "Listening"]
LEVELS = ["A1", "A2", "B1"]
QUESTIONS_PER_SEGMENT = 12  # comfortably above the blueprint's per-segment need


def _guard_production():
	"""Refuse to run anywhere that looks like production.

	The seed creates a thousand logins with a shared, published password —
	catastrophic on a real tenant. Better to fail loudly than to trust the
	operator picked the right --site.
	"""
	site = frappe.local.site or ""
	if frappe.conf.get("developer_mode"):
		return
	if any(marker in site for marker in ("prod", "live")):
		frappe.throw(f"Refusing to seed load-test data on what looks like production: {site}")


def seed(users: int = 100) -> dict:
	"""Create all fixtures; returns the ids the k6 scripts need as env vars."""
	_guard_production()
	users = cint(users) or 100

	created_users = _seed_users(users)
	_seed_questions()
	blueprint = _seed_blueprint()
	prompt = _seed_speaking_prompt()
	audio_file = _seed_dummy_audio()

	frappe.db.commit()

	result = {
		"users": created_users,
		"BLUEPRINT": blueprint,
		"SPEAKING_PROMPT": prompt,
		"AUDIO_FILE": audio_file,
		"USER_PASSWORD": USER_PASSWORD,
	}
	print("\nLoad-test fixtures ready. Export these for k6:\n")
	for key in ("BLUEPRINT", "SPEAKING_PROMPT", "AUDIO_FILE"):
		print(f"  export {key}='{result[key]}'")
	print(f"  export USER_COUNT={created_users}\n")
	return result


def _seed_users(count: int) -> int:
	created = 0
	for index in range(1, count + 1):
		email = f"{USER_PREFIX}{index}@{USER_DOMAIN}"
		if frappe.db.exists("User", email):
			continue
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": f"Load Test {index}",
				"send_welcome_email": 0,
				"user_type": "Website User",
				"enabled": 1,
				"new_password": USER_PASSWORD,
			}
		)
		user.flags.ignore_permissions = True
		user.insert(ignore_permissions=True)
		user.add_roles("LMS Student")
		created += 1

		# Commit in batches: a thousand user inserts in one transaction is a
		# long lock and a painful rollback if anything trips.
		if created % 100 == 0:
			frappe.db.commit()
			print(f"  ... {created} users created")

	frappe.db.commit()
	print(f"Users: {created} created, {count - created} already existed")
	return count


def _seed_questions():
	existing = frappe.db.count("LMS Question", {"topic": FIXTURE_TAG})
	needed = len(SKILLS) * len(LEVELS) * QUESTIONS_PER_SEGMENT
	if existing >= needed:
		print(f"Questions: {existing} already present, skipping")
		return

	created = 0
	for skill in SKILLS:
		for level in LEVELS:
			for i in range(QUESTIONS_PER_SEGMENT):
				question = frappe.get_doc(
					{
						"doctype": "LMS Question",
						"question": f"[{FIXTURE_TAG}] {skill} {level} question {i + 1}?",
						"type": "Choices",
						"language_skill": skill,
						"language_level": level,
						"topic": FIXTURE_TAG,
						"difficulty": (i % 5) + 1,
						"option_1": "Correct answer",
						"is_correct_1": 1,
						"option_2": "Wrong answer A",
						"option_3": "Wrong answer B",
						"option_4": "Wrong answer C",
					}
				)
				question.insert(ignore_permissions=True)
				created += 1
		frappe.db.commit()
	print(f"Questions: {created} created")


def _seed_blueprint() -> str:
	title = f"[{FIXTURE_TAG}] Placement Blueprint"
	existing = frappe.db.get_value("LMS Placement Blueprint", {"title": title}, "name")
	if existing:
		print(f"Blueprint: reusing {existing}")
		return existing

	blueprint = frappe.get_doc(
		{
			"doctype": "LMS Placement Blueprint",
			"title": title,
			"enabled": 1,
			# High cap so a VU can start many attempts across a long run
			# without exhausting its account mid-test.
			"max_attempts": 1000,
			"duration": 60,
			"default_level": "A1",
			"segments": [
				{"skill": skill, "level": "A1", "question_count": 2} for skill in SKILLS
			]
			+ [{"skill": skill, "level": "A2", "question_count": 2} for skill in SKILLS],
			"level_mappings": [
				{"min_score": 0, "level": "A1"},
				{"min_score": 40, "level": "A2"},
				{"min_score": 60, "level": "B1"},
				{"min_score": 80, "level": "B2"},
			],
		}
	)
	blueprint.insert(ignore_permissions=True)
	frappe.db.commit()
	print(f"Blueprint: created {blueprint.name} ({blueprint.total_question_count()} questions)")
	return blueprint.name


def _seed_speaking_prompt() -> str:
	title = f"[{FIXTURE_TAG}] Speaking Prompt"
	existing = frappe.db.get_value("LMS Speaking Prompt", {"title": title}, "name")
	if existing:
		print(f"Speaking prompt: reusing {existing}")
		return existing

	prompt = frappe.get_doc(
		{
			"doctype": "LMS Speaking Prompt",
			"title": title,
			"enabled": 1,
			"language_level": "A2",
			"topic": FIXTURE_TAG,
			"max_duration_seconds": 120,
			"scenario": "Describe your daily routine for one minute.",
		}
	)
	prompt.insert(ignore_permissions=True)
	frappe.db.commit()
	print(f"Speaking prompt: created {prompt.name}")
	return prompt.name


def _seed_dummy_audio() -> str:
	"""A placeholder audio File so the pipeline scenario has something to point at.

	The Mock provider never reads the bytes, so an empty file is enough to
	measure queue and worker throughput. Against the AWS provider this is
	NOT sufficient — Transcribe needs real audio in S3.
	"""
	file_name = f"{FIXTURE_TAG}-audio.webm"
	existing = frappe.db.get_value("File", {"file_name": file_name}, "file_url")
	if existing:
		print(f"Audio fixture: reusing {existing}")
		return existing

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"is_private": 1,
			"content": "load-test placeholder audio",
		}
	)
	file_doc.insert(ignore_permissions=True)
	frappe.db.commit()
	print(f"Audio fixture: created {file_doc.file_url}")
	return file_doc.file_url


def cleanup():
	"""Remove everything seed() created."""
	_guard_production()

	attempts = frappe.get_all(
		"LMS Placement Attempt",
		filters={"member": ["like", f"{USER_PREFIX}%@{USER_DOMAIN}"]},
		pluck="name",
	)
	for name in attempts:
		frappe.delete_doc("LMS Placement Attempt", name, ignore_permissions=True, force=True)

	submissions = frappe.get_all(
		"LMS Speaking Submission",
		filters={"member": ["like", f"{USER_PREFIX}%@{USER_DOMAIN}"]},
		pluck="name",
	)
	for name in submissions:
		frappe.delete_doc("LMS Speaking Submission", name, ignore_permissions=True, force=True)

	users = frappe.get_all(
		"User", filters={"email": ["like", f"{USER_PREFIX}%@{USER_DOMAIN}"]}, pluck="name"
	)
	for name in users:
		frappe.delete_doc("User", name, ignore_permissions=True, force=True)

	questions = frappe.get_all("LMS Question", filters={"topic": FIXTURE_TAG}, pluck="name")
	for name in questions:
		frappe.delete_doc("LMS Question", name, ignore_permissions=True, force=True)

	for doctype, field, value in (
		("LMS Placement Blueprint", "title", f"[{FIXTURE_TAG}] Placement Blueprint"),
		("LMS Speaking Prompt", "title", f"[{FIXTURE_TAG}] Speaking Prompt"),
	):
		for name in frappe.get_all(doctype, filters={field: value}, pluck="name"):
			frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)

	frappe.db.commit()
	print(
		f"Cleaned up: {len(users)} users, {len(questions)} questions, "
		f"{len(attempts)} attempts, {len(submissions)} submissions"
	)

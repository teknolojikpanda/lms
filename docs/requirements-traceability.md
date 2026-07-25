# Requirements Traceability — Technical Agreement v1.3 → frappe/lms

Status legend: ✅ exists in frappe/lms · 🔨 implemented in this repo (this increment) ·
🏗️ infrastructure scope (Terraform/AWS, separate repo) · ⏭️ next increment

| § | Requirement | Status | Where |
|---|---|---|---|
| 1.1 | B2B multi-tenant, tenant isolation | 🔨 | Tenant = Frappe site (ADR-0001). `LMS Tenant` registry + lifecycle APIs (owner portal) and `provisioning/provision_tenant.py` CLI (site create/suspend/resume). Privileged step deliberately kept off the request path |
| 1.3 | Onboarding: 1 institution, 10 classes, 500 students in a day | 🔨 | CSV roster import (`tenant_setup.import_roster_csv`) creating students + classes with per-row error reporting and seat-limit enforcement; runbook in `docs/tenant-onboarding-runbook.md` |
| 3.1 | 4-role hierarchy | ✅ | Role mapping in ADR-0001 (Moderator / Course Creator / Batch Evaluator / LMS Student) |
| 3.2 | Separate portal URLs | ✅/⏭️ | SPA route groups per role; subdomain per tenant 🏗️ |
| 4.1 | Landing page + lead form | ⏭️ | Frappe Web Pages / static site; references list doctype ⏭️ |
| 4.2 | Auth, user lifecycle (invite, CSV import, deactivate) | ✅ | Frappe users + LMS onboarding; CSV import via Data Import |
| 4.3 | Institution → branch → class → student | ✅/⏭️ | LMS Batch (+ enrollment); branch layer ⏭️ |
| 4.4.1 | Course → unit → lesson hierarchy, CEFR + skill tags | ✅/🔨 | LMS Course/Chapter/Lesson ✅; CEFR metadata on questions 🔨 (`lms_question`) |
| 4.4.2 | HLS player, subtitles, speed, shortcuts | ✅/🏗️ | Plyr-based video blocks ✅; HLS/ABR + signed cookies 🏗️ |
| 4.4.3 | Video protection (signed cookies, DRM opt.) | 🏗️ | CloudFront + OAC (infra repo) |
| 4.5 | **Video overlay (timestamp question/note)** | 🔨 | `LMS Video Overlay`, `LMS Overlay Response` + APIs; rules: timestamp validation, optimistic `version` lock, scope visibility (Global/Course/Batch), question payload via LMS Question. UI: player integration in `VideoBlock.vue` (markers, pause+popup, answers) + teacher editor `VideoOverlayEditor.vue` |
| 4.6 | **Placement test** (blueprint, score→level mapping, admin override + audit) | 🔨 | `LMS Placement Blueprint` (+segments, +level mapping), `LMS Placement Attempt`, APIs in the doctype controllers. UI: `PlacementTests.vue` + `PlacementAttempt.vue` (timer, autosave, per-skill result) |
| 4.7.1 | Question types incl. difficulty/duration metadata | ✅/🔨 | LMS Question ✅ + difficulty/level/skill/topic fields 🔨 |
| 4.7.2 | **Deterministic blueprint randomization (MUST)** | 🔨 | `lms/lms/language_platform/exam_engine.py` — segment fill → remainder, no in-attempt duplicates, retake exposure control, seed persisted on attempt |
| 4.7.3 | Exam security (server-authoritative timer, resume) | ✅/⏭️ | Quiz timer ✅ (server check ⏭️); attempt resume on placement 🔨 |
| 4.8 | Progress events + risk heuristics | ✅/🔨 | Course progress, watch duration ✅; risky-student heuristic (inactivity + low scores) in `admin_api.py` surfaced on the institution dashboard 🔨 |
| 4.9 | **AI speaking pipeline** | 🔨 | `LMS Speaking Prompt`, `LMS Speaking Submission` (+ rubric child); state machine Queued→Transcribing→Scoring→Ready/Failed; metrics (wpm, TTR, filler ratio); provider abstraction (mock default, AWS Transcribe/Bedrock adapters); teacher override preserves `ai_total_score`. UI: `SpeakingPractice.vue` (MediaRecorder, upload, polling, rubric feedback) + `SpeakingGrading.vue` (queue + audited override) |
| 4.9.3 | Speaking data privacy (retention, disclosure) | 🔨/🏗️ | Retention days + daily quota in module settings 🔨; S3 lifecycle 🏗️ |
| 4.10 | FinOps / CUR dashboards | 🔨/🏗️ | Owner dashboard with application-metered cost estimate (Ek-6.3 unit prices, clearly labelled) 🔨; authoritative CUR+Athena feed 🏗️ |
| 5.1 | Stack (changed by ADR) | 🔨 | `docs/adr/ADR-0001-stack-change-frappe-lms.md` |
| 6.3 | Accessibility: 6 font steps, whiteboard mode, WCAG AA | ⏭️ | frontend increment (CSS vars exist in frappe-ui theme) |
| 6.4 | KVKK: retention, audit log, encryption | 🔨/🏗️ | `track_changes` on all new doctypes + override audit comments; Ek-2 retention purge jobs (audio 30d, transcripts 1y, exam results 2y — all configurable, 0 = keep forever) in `retention.py` 🔨; S3/KMS 🏗️ |
| 7.3 | API error envelope + correlationId | 🔨 | `lms/lms/language_platform/envelope.py` decorator used by module APIs |
| 8.x | AWS infra (VPC, ECS, CloudFront, S3, …) | 🔨 | **`dil-platformu-infra`** repo: 9 Terraform modules (network, data, compute, edge, media, ai, security, observability, finops) + bootstrap + dev/prod roots; `terraform validate` green. Remaining: AWS Organizations/SCP (§8.6), MediaPackage DRM, DR replication |
| 9.1 | Input validation, RBAC, rate limits | ✅/🔨 | Frappe schema validation + role perms; per-endpoint checks in new APIs; `rate_limit` on placement start/submit/autosave, speaking upload, overlay writes and all score overrides 🔨 |
| 9.2 | KVKK technical rights (DSAR) | 🔨 | `LMS Data Request` (Export/Erasure, Pending→Approved→Completed, four-eyes gate: erasure cannot be self-approved) + `dsar.py` / `privacy_rules.py`. Export = full JSON of everything stored about the person; erasure = anonymise (academic records kept pseudonymously per "zorunlu saklama", personal free text deleted, speaking audio destroyed immediately). Self-service export for students. **Residual flagged for legal review:** the login identifier persists in `owner`/`modified_by` columns — documented in `dsar._scrub_user_record` |
| 6.6 | Load and performance tests | 🔨 | `load-tests/` k6 suite: exam start (1000 VU stampede, p95<800ms), exam taking (autosave p95<300ms), API CRUD, VOD cache hit ratio >85%, speaking time-to-score <5min — all as enforced thresholds. Seed script + README. Player soak test still manual |
| 10.1 | Mandatory tests (blueprint respected, no dupes, deterministic seed, isolation) | 🔨 | `lms/tests/language_platform/` (pure unit) + doctype test stubs (bench CI) |
| Ek-1 | RBAC matrix | 🔨 | Doctype permission tables on new doctypes mirror Ek-1 |
| Ek-2 | Retention matrix | 🔨/🏗️ | Audio 30d, transcripts 1y, exam results 2y enforced by `retention.py` daily jobs (all configurable in LMS Language Settings) 🔨; S3 lifecycle policies in the infra repo 🏗️ |
| Ek-4 | Screen specs (4 portals) | 🔨/⏭️ | Existing LMS UI covers student/teacher course flows; new module screens delivered: placement taking, overlay editor + in-player overlays, speaking practice, grading center, institution dashboard (Ek-4.4: KPIs, placement distribution, activity, risky students, pending grading, quick actions) and owner dashboard (Ek-4.3: usage KPIs, trends, ops signals, cost estimate). Remaining Ek-4 detail screens (tenant list/billing, integrations, compliance) track the multi-site rollout |

## Increment plan

1. **This increment (backend MVP):** ADR, CEFR metadata, exam engine, placement, video
   overlays, speaking assessment, unit tests.
2. **Frontend increment:** placement-taking UI, overlay editor + player integration, speaking
   recorder + grading center (Vue pages under `frontend/src/pages`).
3. **Infra increment (separate repo):** Terraform modules per Ek-5.1, site-per-tenant
   provisioning, VOD pipeline, CUR/FinOps.
4. **Hardening (done):** endpoint rate limits, server-authoritative exam timer, DSAR
   export/erasure with approval workflow, Ek-2 retention purge jobs.
5. **Load tests (done):** k6 suite in `load-tests/` with §6.2 thresholds as pass/fail gates.
6. **Provisioning (done):** tenant registry, provisioning CLI, roster import, runbook.
7. **Remaining:** run the load suite against staging to record baseline numbers,
   browser-based player soak test (§6.6), OpenSearch for question/transcript search at
   scale (§8.10), AWS Organizations/SCP (§8.6), tenant archival/offboarding automation.

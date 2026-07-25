# ADR-0001: Adopt Frappe/LMS as the Platform Stack

- **Status:** Accepted
- **Date:** 2026-07-25
- **Relates to:** Technical Agreement "Web Tabanlı Yabancı Dil Eğitim Platformu (B2B SaaS)" v1.3
  (§5.1 *Varsayılan Stack*, §12 *Değişiklik Yönetimi*)

## Context

The technical agreement defines a **default (suggested)** stack in §5.1: Next.js + NestJS +
Aurora PostgreSQL (RLS) + Prisma + Redis/SQS + Terraform. §5.1 explicitly states:
*"Stack değişirse, ADR ile resmileştirilir ve bu doküman güncellenir."*

The project sponsor has directed the build onto **[frappe/lms](https://github.com/frappe/lms)**
(Frappe Framework + MariaDB + Vue 3 + frappe-ui). This ADR formalises that change as required
by §12 (Change Control).

## Decision

Build the platform as a fork/extension of **frappe/lms** on the **Frappe Framework**:

| Concern (agreement) | Default stack | Adopted (this ADR) |
|---|---|---|
| Frontend portals | Next.js + TS | Vue 3 + frappe-ui SPA (`frontend/`) |
| Backend API | NestJS REST + OpenAPI | Frappe REST + whitelisted RPC methods |
| DB | Aurora PostgreSQL + RLS | MariaDB (managed, Multi-AZ in prod) |
| ORM / migrations | Prisma + SQL | Frappe DocType schema sync + patches |
| AuthN | Amazon Cognito (OIDC) | Frappe auth; optional OIDC/SAML via `frappe/oauth` & Social Login Keys (Cognito can still front as IdP) |
| Cache / queue | Redis + SQS | Redis (Frappe cache + RQ background job queues) |
| Workflow orchestration | Step Functions | Frappe background jobs (RQ) with retry/backoff + status state machine on the document (see ADR notes below); Step Functions remains an option for the AWS-native speaking pipeline |
| Async eventing | EventBridge | Frappe doc_events + hooks; EventBridge retained for S3-driven media events |
| Video VOD | S3 + MediaConvert + CloudFront signed cookies | unchanged (infrastructure-level, stack-agnostic) |
| AI speaking | Transcribe + Bedrock | unchanged, invoked from Frappe workers through a provider abstraction (`lms/lms/speaking/providers.py`) with an offline mock default |
| IaC / CI | Terraform + GitHub Actions | unchanged |

## Multi-tenancy model

The agreement (§3, §7.1) requires hard tenant isolation (app filter + DB RLS + object prefixes).
Frappe provides a **stronger** isolation primitive natively: **one site per tenant**
(bench multi-tenancy). Decision:

- **Tenant = Frappe site** (`<tenant>.<domain>` — matches the agreement's preferred subdomain
  routing option in §3.2/§8.7). Each tenant gets its own database, its own file namespace and
  its own user table. Cross-tenant reads are impossible by construction — this satisfies and
  exceeds the RLS requirement (defense-in-depth is the DB-per-tenant boundary itself).
- **Platform Owner portal** = a dedicated owner site (or the bench host site) aggregating
  operational/billing data. Tenant provisioning = scripted `bench new-site` + app install
  (automation lives in infra repo).
- Role mapping (§3.1, Ek-1):
  - Platform Sahibi → System Manager / LMS Moderator on the owner site
  - Kurum Yöneticisi → **Moderator** on the tenant site
  - Öğretmen → **Course Creator + Batch Evaluator** on the tenant site
  - Öğrenci → **LMS Student**
- Classes (Sınıf) → **LMS Batch**; branches/campuses → batch grouping via LMS Category or a
  dedicated doctype in a later increment.

## Consequences

**Positive**
- ~70% of the functional scope (courses, lessons, video lessons, quizzes, question bank,
  batches, enrollment, progress, certificates, assignments, notifications, payments) exists
  and is battle-tested in frappe/lms — the delivery risk drops substantially.
- Tenant isolation by site removes an entire class of cross-tenant bugs the agreement's
  mandatory negative tests target (§10.1).
- One codebase (Python + Vue) instead of two TypeScript services.

**Negative / accepted trade-offs**
- MariaDB instead of Aurora PostgreSQL: no RLS, mitigated by site-per-tenant. Managed MariaDB
  (RDS Multi-AZ) keeps RPO/RTO targets (§6.1).
- API error envelope (§7.3) differs from Frappe's default response shape; custom endpoints in
  this repo return the agreed envelope via a shared decorator (see
  `lms/lms/language_platform/envelope.py`).
- Cognito is no longer the system of record for identity; if a customer mandates it, Cognito
  is wired as an external OIDC IdP.

**New MUSTs carried over unchanged** (tracked in `docs/requirements-traceability.md`):
deterministic exam randomization (§4.7.2), overlay optimistic locking (§4.5), speaking
pipeline limits/quotas (§8.13), audit logging (§6.4), retention jobs (Ek-2).

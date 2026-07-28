# Running the platform locally (no AWS)

The whole platform runs on one machine with **no AWS account, no
credentials and no cloud services**. Everything that would use AWS is
opt-in and ships switched off.

## Requirements

- Docker Desktop (running)
- ~8 GB free disk, ~4 GB RAM for the containers

Nothing else. No AWS CLI, no credentials, no Terraform.

## Start it

```bash
cd docker
docker compose -f docker-compose.staging.yml up -d
```

First run takes **10–20 minutes** — it downloads Frappe, installs node
modules, creates the site and builds the frontend. Watch it with:

```bash
docker compose -f docker-compose.staging.yml logs -f frappe
```

It is ready when the log prints `==> STAGING BENCH READY`.

| | |
|---|---|
| URL | <http://localhost:8000> |
| Site | `staging.localhost` |
| Login | `Administrator` / `admin` |

Stop with `docker compose -f docker-compose.staging.yml stop`, and start
again with `start` — that keeps the bench and the data. `down` removes
the containers and re-runs the 20-minute init next time; `down -v` also
deletes the database.

## Verify it

```bash
docker exec lms-staging-frappe-1 bash -lc "
  cd /home/frappe/frappe-bench
  cp /workspace/lms-src/docker/staging-smoke.py \
     apps/lms/lms/lms/language_platform/staging_smoke.py
  bench --site staging.localhost execute lms.lms.language_platform.staging_smoke.run"
```

Expect **48 passed, 0 failed** — placement, overlays, speaking,
accessibility, search, watermarking, tenants, DSAR, DR, plus three
checks that assert no AWS provider is active.

## What works offline, and what it uses instead

| Feature | Offline behaviour |
|---|---|
| Placement tests | Fully working — deterministic selection, grading, override |
| Video overlays | Fully working — timeline notes/questions, optimistic locking |
| **AI speaking** | **Mock provider**: deterministic transcript and rubric scores. The full pipeline, state machine, metrics, teacher override and retention all run; only the words are synthetic |
| **Search** | **Database provider**: queries the tables directly. No cluster, and correct by construction since there is no second copy |
| Session watermark | Fully working — codes, registry, tracing |
| Accessibility | Fully working — font scale, contrast, whiteboard mode |
| KVKK / DSAR | Fully working — export, erasure, retention jobs |
| Tenant registry | Fully working. Actually *provisioning a site* needs the CLI on a bench host, which this is |
| Dashboards | Working. The owner cost panel shows an application-metered estimate, not real billing |
| DR readiness | Working — reports RPO from local backups, tracks restore tests |

Switched off, and requiring AWS or a vendor if you ever want them:

| Feature | Needs |
|---|---|
| Real speech-to-text and LLM scoring | AWS Transcribe + Bedrock |
| OpenSearch | An OpenSearch domain (§8.10) |
| DRM playback | A DRM vendor licence (§1.2 procurement) |
| Forensic watermarking | A NexGuard licence |
| CDN, signed cookies, transcoding | The AWS infrastructure in `dil-platformu-infra` |

None of these are needed to develop, demo or evaluate the platform.

## Tests you can run without the bench

```bash
# 226 unit tests: exam engine, privacy, tenants, search, DRM, DR, a11y
python -m unittest discover -s lms/tests/language_platform -t .

# 426 frontend tests
cd frontend && corepack yarn vitest run
```

## The infrastructure repo

`dil-platformu-infra` describes AWS and is **not needed to run the
platform**. Without credentials you can still check it:

```bash
docker run --rm -v "<path>/dil-platformu-infra:/work" -w /work \
  hashicorp/terraform:1.9 fmt -check -recursive

docker run --rm -v "<path>/dil-platformu-infra:/src" \
  aquasec/tfsec /src
```

`terraform plan` and `apply` need real credentials and create billable
resources; neither has been run.

## Troubleshooting

**Port 8000 in use** — stop the other service, or change the port mapping
in `docker-compose.staging.yml`.

**`bench start` but the site 404s** — Frappe resolves the site from the
Host header. Use `localhost:8000`, not `127.0.0.1:8000`.

**Changed code not taking effect** — the app is installed by cloning the
repo at `LMS_BRANCH`, so commit first, then copy the file in:

```bash
docker exec lms-staging-frappe-1 bash -lc \
  "cp /workspace/lms-src/lms/lms/language_platform/foo.py \
      /home/frappe/frappe-bench/apps/lms/lms/lms/language_platform/foo.py"
```

Doctype JSON changes additionally need
`bench --site staging.localhost migrate`.

See `docs/staging-bench.md` for the setup traps behind these choices.

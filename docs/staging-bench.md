# Staging bench

A local Frappe bench for verifying **this working tree** — the regular
`docker/docker-compose.yml` runs `bench get-app lms`, which fetches from
GitHub and therefore tells you nothing about local changes.

```bash
cd docker
docker compose -f docker-compose.staging.yml up -d
docker compose -f docker-compose.staging.yml logs -f frappe
```

First run takes 10–20 minutes (bench init, node modules, site creation).
The site is `staging.localhost` on <http://localhost:8000>,
Administrator / `admin`.

The app is installed by cloning the mounted repo at the branch in
`LMS_BRANCH`, so **only committed work is tested**. After committing a
change, re-copy it into the running container rather than rebuilding:

```bash
docker exec lms-staging-frappe-1 bash -lc \
  "cp /workspace/lms-src/lms/lms/language_platform/foo.py \
      /home/frappe/frappe-bench/apps/lms/lms/lms/language_platform/foo.py"
```

## Smoke test

`docker/staging-smoke.py` exercises every language-platform module
against the live site — placement, overlays, speaking, accessibility,
search, watermarking, tenants, DSAR and DR:

```bash
docker exec lms-staging-frappe-1 bash -lc "
  cd /home/frappe/frappe-bench
  cp /workspace/lms-src/docker/staging-smoke.py \
     apps/lms/lms/lms/language_platform/staging_smoke.py
  bench --site staging.localhost execute lms.lms.language_platform.staging_smoke.run"
```

Each check runs independently and the script continues after a failure,
so one run reports every defect rather than the first. It resets the
state it mutates on entry, because otherwise a second run reports
ordering artefacts as failures — which is exactly what happened the first
time.

### Removing the fixtures afterwards

The run seeds a course tree, a question bank, placement and speaking
records, a tenant, a roster and DSAR requests, and leaves them on the
site. `cleanup` removes all of it and puts the settings the run mutates
back to their shipped defaults:

```bash
docker exec -w /home/frappe/frappe-bench lms-staging-frappe-1 \
  bench --site staging.localhost execute \
  lms.lms.language_platform.staging_smoke.cleanup
```

It re-counts every marker afterwards, so anything that refuses deletion
is reported rather than assumed gone. Two things survive by default and
are reported as still present:

- **Processed `LMS Data Request` records.** Their `on_trash` guard
  protects them as the KVKV audit trail.
- **`Error Log` entries.** The run generates them (attach failures and
  password notifications — expected with no SMTP and no real audio
  offline), but the log is also where real defects surface: a
  speaking-pipeline failure handler that saves a stale document was
  found only by reading it.

Read those before overriding, then, on a disposable bench only:

```bash
docker exec -w /home/frappe/frappe-bench lms-staging-frappe-1 \
  bench --site staging.localhost execute \
  lms.lms.language_platform.staging_smoke.cleanup \
  --kwargs "{'include_audit_records': True, 'clear_error_logs': True}"
```

## Notes that cost time to discover

- **MariaDB healthcheck** must pass credentials. `healthcheck.sh
  --connect` authenticates as root with no password, and `--su-mysql` as
  a unix user with no socket grant; both fail when
  `MYSQL_ROOT_PASSWORD` is set. Hence the explicit `mysqladmin ping`.
- **No volume over `frappe-bench`.** `bench init` refuses a directory
  that already exists, which a mounted volume always does, and
  initialising elsewhere then moving it breaks the virtualenv (absolute
  paths in shebangs). The bench lives in the container layer: `down`
  loses it and re-runs init; site data survives in the mariadb volume.
- **`git config --global --add safe.directory`** is needed before
  cloning the mount — the repo is owned by the host user, not `frappe`,
  so git refuses it as "dubious ownership".
- **`block_endpoints` must be set** or `lms.tests.security.test_auth`
  fails. `lms.auth.authenticate` returns immediately without it, so the
  allowlist tests never exercise anything. This looks like a regression
  and is not one.
- **Frappe's API encoder handles datetimes; `json.dumps` does not.**
  Assertions in test code need `default=str`, or they fail on responses
  that are perfectly valid over HTTP.

## What this does not cover

A bench validates the application against real MariaDB and Redis. It says
nothing about the AWS infrastructure (`dil-platformu-infra`), which needs
a real account and a `terraform apply`, nor about the load, soak or
browser suites, which need a deployed environment.

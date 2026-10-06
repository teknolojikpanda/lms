#!/bin/bash
# Staging bench bootstrap — installs the LMS app from the mounted repo
# rather than from GitHub, so it validates local work.
#
# Idempotent: re-running against an existing bench skips straight to
# starting it.
set -e

BENCH_DIR="/home/frappe/frappe-bench"
SITE="staging.localhost"
SRC="/workspace/lms-src"

# The bind-mounted repo is owned by the host user, not `frappe`, so git
# refuses to read it ("dubious ownership") until the path is trusted.
# Safe here: a throwaway container reading a read-only mount. Trusted
# this early because resolving the branch below has to read the repo.
git config --global --add safe.directory "${SRC}"
git config --global --add safe.directory "${SRC}/.git"

# Follow whatever branch the mounted repo is on, unless told otherwise.
# A hardcoded name is how this broke once: the branch was deleted after
# merging and every fresh bench then failed at `bench get-app`. A
# detached checkout has no branch name to follow, so fall back.
BRANCH="${LMS_BRANCH:-}"
if [ -z "${BRANCH}" ]; then
    BRANCH="$(git -C "${SRC}" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
fi
if [ -z "${BRANCH}" ] || [ "${BRANCH}" = "HEAD" ]; then
    BRANCH="develop"
fi

export PATH="${NVM_DIR}/versions/node/v${NODE_VERSION_DEVELOP}/bin/:${PATH}"

# The image ships a MariaDB 11.x client, which requires TLS by default;
# the mariadb:10.8 service offers none, so `bench new-site` dies restoring
# the framework schema with "ERROR 2026: SSL is required, but the server
# does not support it". Traffic never leaves the compose network.
printf '[client]\nskip-ssl\n' > "${HOME}/.my.cnf"

# Written only after the site is fully set up. Testing for apps/frappe
# instead would treat a bench whose site creation failed as finished, and
# a restart would then serve a bench with no site.
READY_MARKER="${BENCH_DIR}/.staging-ready"

if [ -f "${READY_MARKER}" ]; then
    echo "==> Bench already initialised; starting."
    cd "${BENCH_DIR}"
    exec bench start
fi

# A half-built bench (a failed earlier run) makes `bench init` refuse,
# with a message that reads as if a working bench were already present.
# Clear it rather than leaving the operator to guess.
if [ -d "${BENCH_DIR}" ]; then
    echo "==> Removing incomplete bench at ${BENCH_DIR}"
    rm -rf "${BENCH_DIR}"
fi

echo "==> Initialising bench (this takes a while on first run)"
bench init --skip-redis-config-generation frappe-bench
cd "${BENCH_DIR}"

echo "==> Pointing bench at the compose services"
bench set-mariadb-host mariadb
bench set-redis-cache-host redis://redis:6379
bench set-redis-queue-host redis://redis:6379
bench set-redis-socketio-host redis://redis:6379

# Redis and the asset watcher run as their own containers / are not needed.
sed -i '/redis/d' ./Procfile
sed -i '/watch/d' ./Procfile
# `bench serve` binds 127.0.0.1 by default, which Docker's port mapping
# cannot reach: the site answers inside the container and the host gets
# an empty reply on localhost:8000.
sed -i 's/bench serve /bench serve --host 0.0.0.0 /' ./Procfile

echo "==> Fetching payments (required by the lms app)"
bench get-app payments

echo "==> Installing lms from the mounted repo, branch ${BRANCH}"
# `bench get-app` against a local path is what makes this install the
# mounted repo rather than GitHub. Let it own the clone, the editable
# install, the asset build and sites/apps.txt.
#
# Do NOT hand-roll this with `git clone` + `echo >> apps.txt`: bench
# writes app names without a trailing newline, so appending produces
# "paymentslms" on one line and the site then fails to create with
# ModuleNotFoundError.
bench get-app --branch "${BRANCH}" "${SRC}"

echo "==> Creating site ${SITE}"
bench new-site "${SITE}" \
    --force \
    --mariadb-root-password 123 \
    --admin-password admin \
    --no-mariadb-socket

echo "==> Installing apps onto the site"
bench --site "${SITE}" install-app payments
bench --site "${SITE}" install-app lms
bench --site "${SITE}" set-config developer_mode 1
bench --site "${SITE}" set-config server_script_enabled 1
# Required by lms.tests.security.test_auth: lms.auth.authenticate is a
# no-op unless this is set, so without it the endpoint-allowlist tests
# pass vacuously and look like a regression when they fail.
bench --site "${SITE}" set-config block_endpoints 1
bench --site "${SITE}" set-config allow_tests true
bench --site "${SITE}" clear-cache
bench use "${SITE}"
touch "${READY_MARKER}"

echo "==> STAGING BENCH READY: ${SITE}"
bench start

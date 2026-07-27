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
BRANCH="${LMS_BRANCH:-feature/language-platform-mvp}"

export PATH="${NVM_DIR}/versions/node/v${NODE_VERSION_DEVELOP}/bin/:${PATH}"

if [ -d "${BENCH_DIR}/apps/frappe" ]; then
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

echo "==> Fetching payments (required by the lms app)"
bench get-app payments

echo "==> Installing lms from the mounted repo, branch ${BRANCH}"
# The bind-mounted repo is owned by the host user, not `frappe`, so git
# refuses to read it ("dubious ownership") until the path is trusted.
# Safe here: a throwaway container reading a read-only mount.
git config --global --add safe.directory "${SRC}"
git config --global --add safe.directory "${SRC}/.git"

# Cloning from the mount rather than GitHub is the whole point of this
# compose file. The source is read-only, so this copies rather than links.
git clone --branch "${BRANCH}" "${SRC}" "${BENCH_DIR}/apps/lms"
bench pip install -e "${BENCH_DIR}/apps/lms"
echo "lms" >> "${BENCH_DIR}/sites/apps.txt"

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

echo "==> STAGING BENCH READY: ${SITE}"
bench start

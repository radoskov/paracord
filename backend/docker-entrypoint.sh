#!/usr/bin/env bash
# Entrypoint for the api image. Runs DB migrations only when starting the server
# (so `docker compose run --rm api pytest` skips them — the test suite uses SQLite).
# Both uvicorn (dev) and gunicorn/sh (prod) trigger the migration step.
set -e

APP_USER=appuser

# D4: the managed-library volume is created root-owned (named Docker volumes inherit root on every
# host, whatever the host UID), so the non-root app user can't write to it until we fix ownership.
# Do it here — while still root — before dropping privileges. Ensure the dir exists, then chown only
# the entries NOT already owned by the app user: idempotent (a no-op once settled), portable across
# machines, and — unlike a top-level-only owner check — it also repairs a partial state where a
# subdirectory was left root-owned (which would otherwise fail an upload's mkdir deep in the tree).
if [ -d /app/storage ]; then
    find /app/storage \! -user "$APP_USER" -exec chown "$APP_USER:$APP_USER" {} + 2>/dev/null || true
fi

case "$1" in
    uvicorn|gunicorn|sh)
        echo "Applying database migrations..."
        alembic -c backend/alembic.ini upgrade head
        ;;
esac

# Drop root: run the actual server/worker process as the non-root app user (D4).
exec gosu "$APP_USER" "$@"

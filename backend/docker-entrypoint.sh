#!/usr/bin/env bash
# Entrypoint for the api image. Runs DB migrations only when starting the server
# (so `docker compose run --rm api pytest` skips them — the test suite uses SQLite).
# Both uvicorn (dev) and gunicorn/sh (prod) trigger the migration step.
set -e

APP_USER=appuser
APP_UID="$(id -u "$APP_USER")"

# D4: the managed-library volume is created root-owned (named Docker volumes inherit root), so the
# non-root app user can't write to it until we fix ownership. Do it here — while we are still root —
# then drop privileges. Guarded so the recursive chown effectively runs only once (small tree).
if [ -d /app/storage ] && [ "$(stat -c %u /app/storage)" != "$APP_UID" ]; then
    chown -R "$APP_USER:$APP_USER" /app/storage
fi

case "$1" in
    uvicorn|gunicorn|sh)
        echo "Applying database migrations..."
        alembic -c backend/alembic.ini upgrade head
        ;;
esac

# Drop root: run the actual server/worker process as the non-root app user (D4).
exec gosu "$APP_USER" "$@"

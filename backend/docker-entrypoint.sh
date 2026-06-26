#!/usr/bin/env bash
# Entrypoint for the api image. Runs DB migrations only when starting the server
# (so `docker compose run --rm api pytest` skips them — the test suite uses SQLite).
# Both uvicorn (dev) and gunicorn/sh (prod) trigger the migration step.
set -e

case "$1" in
    uvicorn|gunicorn|sh)
        echo "Applying database migrations..."
        alembic -c backend/alembic.ini upgrade head
        ;;
esac

exec "$@"

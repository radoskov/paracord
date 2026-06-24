#!/usr/bin/env bash
# Entrypoint for the api image. Runs DB migrations only when starting the server
# (so `docker compose run --rm api pytest` skips them — the test suite uses SQLite).
set -e

if [ "$1" = "uvicorn" ]; then
    echo "Applying database migrations..."
    alembic -c backend/alembic.ini upgrade head
fi

exec "$@"

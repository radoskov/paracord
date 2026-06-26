# PaperRacks server (FastAPI) image. Targets Python 3.12.
#
# Build targets:
#   development  (default) — dev deps, uvicorn --reload, suitable for bind-mount overlay
#   production              — runtime deps only, gunicorn workers, no --reload
#
# docker compose  uses `development` by default via docker-compose.yml.
# docker compose -f docker-compose.yml -f docker-compose.prod.yml  uses `production`.

# ── base: shared OS packages and PYTHONPATH ────────────────────────────────────
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/backend

WORKDIR /app

# curl is used by the container healthcheck. psycopg[binary]/lxml/bcrypt ship wheels,
# so no compiler toolchain is required.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 8000
ENTRYPOINT ["/app/backend/docker-entrypoint.sh"]

# ── development: dev deps installed, live-reload via bind-mount ────────────────
FROM base AS development

COPY backend/requirements.txt backend/requirements-dev.txt /app/backend/
RUN pip install -r /app/backend/requirements-dev.txt

# Application code (overlaid by a bind mount in docker-compose for live reload).
COPY backend /app/backend
COPY scripts /app/scripts
COPY config /app/config
COPY pyproject.toml /app/pyproject.toml

CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ── production: runtime deps only, gunicorn + uvicorn workers ─────────────────
FROM base AS production

COPY backend/requirements.txt /app/backend/
RUN pip install -r /app/backend/requirements.txt

COPY backend /app/backend
COPY scripts /app/scripts
COPY config /app/config
COPY pyproject.toml /app/pyproject.toml

# GUNICORN_WORKERS defaults to 2; tune via the environment for your hardware.
ENV GUNICORN_WORKERS=2

CMD ["sh", "-c", \
     "gunicorn app.main:app \
       --chdir backend \
       --worker-class uvicorn.workers.UvicornWorker \
       --workers ${GUNICORN_WORKERS} \
       --bind 0.0.0.0:8000 \
       --access-logfile - \
       --error-logfile -"]

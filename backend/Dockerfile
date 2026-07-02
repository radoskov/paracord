# PaperRacks server (FastAPI) image. Targets Python 3.12.
#
# Build targets:
#   development  (default) — dev deps, uvicorn --reload, suitable for bind-mount overlay
#   production              — runtime deps only, gunicorn workers, no --reload
#
# The following is not true:
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
#
# OCR (Phase B5): tesseract-ocr + the English data + ghostscript + the ocrmypdf CLI let the
# worker add a searchable text layer to scanned/poor-text PDFs before GROBID. This is a bounded
# local subprocess with no network egress. These packages add roughly 300-500 MB to the base
# image; add extra `tesseract-ocr-<lang>` packages here for other OCR languages.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        tesseract-ocr \
        tesseract-ocr-eng \
        ghostscript \
        ocrmypdf \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 8000
ENTRYPOINT ["/app/backend/docker-entrypoint.sh"]

# ── development: dev deps installed, live-reload via bind-mount ────────────────
FROM base AS development

COPY backend/requirements.txt backend/requirements-dev.txt backend/requirements.lock /app/backend/
# Install the hash-pinned runtime lock (D24), then layer on the dev/test tools. The lock is the
# reproducible source of runtime versions; requirements.txt is the human-edited intent it is
# compiled from (regenerate with `pip-compile --generate-hashes`). The second install keeps the
# already-satisfied locked runtime deps and only adds pytest/ruff.
RUN pip install --require-hashes -r /app/backend/requirements.lock \
    && pip install -r /app/backend/requirements-dev.txt

# Application code (overlaid by a bind mount in docker-compose for live reload).
COPY backend /app/backend
COPY scripts /app/scripts
COPY config /app/config
COPY pyproject.toml /app/pyproject.toml

CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ── production: runtime deps only, gunicorn + uvicorn workers ─────────────────
FROM base AS production

COPY backend/requirements.txt backend/requirements.lock /app/backend/
# Install from the hash-pinned lock (D24) so a production rebuild can't silently pull a new major.
RUN pip install --require-hashes -r /app/backend/requirements.lock

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

# ── ml-extraction (opt-in): heavy ML PDF extractors (Nougat/Marker) ───────────
# Built ONLY on request via `make build-ml-extraction` — these pull torch + model weights (multi-GB)
# and are never installed at runtime. Selecting ocr_backend=full_ml activates them when present and
# otherwise degrades to GROBID. Layers on top of `development` so the app + OCR deps are already in.
FROM development AS ml-extraction

RUN pip install \
        "nougat-ocr>=0.1.17" \
        "marker-pdf>=0.2"

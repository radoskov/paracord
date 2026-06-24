# PaperRacks server (FastAPI) image. Targets Python 3.12.
FROM python:3.12-slim

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

# Install dependencies first for better layer caching.
COPY backend/requirements.txt backend/requirements-dev.txt /app/backend/
RUN pip install -r /app/backend/requirements-dev.txt

# Application code (overlaid by a bind mount in docker-compose for live reload).
COPY backend /app/backend
COPY scripts /app/scripts
COPY config /app/config
COPY pyproject.toml /app/pyproject.toml

EXPOSE 8000
ENTRYPOINT ["/app/backend/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000", "--reload"]

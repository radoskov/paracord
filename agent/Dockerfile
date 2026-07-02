# PaperRacks local workstation agent (client) image. Targets Python 3.12.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/agent

WORKDIR /app

COPY agent/requirements.txt /app/agent/requirements.txt
RUN pip install -r /app/agent/requirements.txt && pip install "pytest>=8.2"

COPY agent /app/agent

# D4: run as a non-root user. UID 1000 matches the host developer so the bind-mounted ./agent tree
# stays readable/writable. The agent mounts no root-owned named volume, so a plain USER is enough
# (no entrypoint chown needed, unlike the api/worker managed-library volume).
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid 1000 --create-home appuser \
    && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "paperracks_agent.cli", "--help"]

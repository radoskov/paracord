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

CMD ["python", "-m", "paperracks_agent.cli", "--help"]

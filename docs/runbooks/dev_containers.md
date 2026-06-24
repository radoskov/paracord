# Development & evaluation containers

PaperRacks is built and validated in containers so the implementation can be run and
tested reproducibly, independent of the host's Python version. The stack targets
**Python 3.12**.

## Services (docker-compose.yml)

| Service | Role | Image / build | Profile |
|---|---|---|---|
| `postgres` | Database (pgvector) | `pgvector/pgvector:pg17` | default |
| `redis` | Job queue / cache | `redis:7-alpine` | default |
| `api` | **Server** — FastAPI backend | built from `backend/Dockerfile` | default |
| `agent` | **Client** — local workstation agent | built from `agent/Dockerfile` | default |
| `grobid` | PDF extraction service | `grobid/grobid` | `extraction` |
| `ollama` | Local LLM (summaries/embeddings) | `ollama/ollama` | `ai` |

Credentials come from a local `.env` file (gitignored). Create it first:

```bash
cp .env.example .env
```

The `postgres` service guards its required variables with `${VAR:?…}`, so a missing
`.env` fails fast with a clear message instead of starting a broken database.

## Common tasks

```bash
docker compose up -d --build            # start postgres, redis, api, agent
docker compose run --rm api pytest      # run the test suite (Python 3.12, SQLite — no DB needed)
docker compose run --rm api ruff check backend agent scripts
docker compose logs -f api              # follow server logs
docker compose exec agent python -m paperracks_agent.cli --help
docker compose --profile extraction up -d grobid   # start GROBID when needed
docker compose --profile ai up -d ollama           # start Ollama when needed
docker compose down -v                  # stop and drop volumes
```

Make equivalents: `make up`, `make test`, `make lint`, `make down`.

## How it runs

- The `api` image installs `backend/requirements-dev.txt` (runtime deps + `pytest`/`ruff`).
- `backend/docker-entrypoint.sh` applies Alembic migrations **only** when starting the
  server (`uvicorn`); `pytest`/`ruff` skip migrations, and the suite uses SQLite so it
  needs no database service.
- Source is bind-mounted into the containers, so code edits are picked up without a
  rebuild (the server runs `uvicorn --reload`). Rebuild only when dependencies change:
  `docker compose build`.
- The server reads `DATABASE_URL`/`REDIS_URL`/`GROBID_URL`/`OLLAMA_URL` from the compose
  environment (pointing at service names), overriding the host-oriented values in `.env`.

## Bootstrap an owner and smoke-test the API

```bash
docker compose exec api python -c \
  "from scripts.bootstrap_admin import create_first_owner; create_first_owner('owner','change-me-please')"
curl -fsS -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' -d '{"username":"owner","password":"change-me-please"}'
```

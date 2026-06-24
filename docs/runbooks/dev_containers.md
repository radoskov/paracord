# Development & Evaluation Containers

PaRacORD uses Docker Compose as the source of truth for runtime, testing, migrations, and deployment-like validation.

Use Make targets for normal development. Raw `docker compose` commands are shown here when useful for debugging or understanding what the Make targets do.

## Quick container workflow

```bash
make init                 # create .env from .env.example if missing
make up                   # build and start postgres, redis, api, worker, agent, frontend
make migrate              # apply database migrations inside the api container
make bootstrap-admin      # create the first owner account
make health               # check API health

make logs-api             # follow server logs
make logs-agent           # follow agent logs
make ps                   # show service status

make check                # Docker lint + Docker tests
make down                 # stop containers, keep volumes
make clean                # stop containers, delete volumes; destructive
```

## Common tasks

```bash
make up                   # start the full Docker development stack
make up-api               # start postgres, redis, and api only
make up-infra             # start postgres and redis only
make frontend-dev         # start the Svelte/frontend dev server
make frontend-build       # build the frontend in Docker

make test                 # run backend/agent tests in Docker
make test-api             # run backend tests in Docker
make test-agent           # run agent tests in Docker
make lint                 # run Ruff check + Ruff format check in Docker
make fix                  # host-local Ruff autofix and formatting
make fix-docker           # Docker-based Ruff autofix and formatting
make check                # Docker lint + Docker tests
make ready                # local autofix + pre-commit + Docker checks

make migrate              # apply migrations in Docker
make migration MSG="..."  # generate a new Alembic migration in Docker
make db-shell             # open psql in the postgres container

make logs                 # follow all service logs
make logs-api             # follow API logs
make logs-worker          # follow worker logs
make logs-agent           # follow agent logs
make shell-api            # open a shell in the API container
make shell-agent          # open a shell in the agent container

make agent-help           # run agent CLI help in Docker
make bootstrap-admin      # create first owner account
make reset-admin-password # reset owner/admin password from server console

make down                 # stop containers but keep volumes/data
make clean                # stop containers and delete volumes/data
```

## Raw Docker Compose equivalents

Prefer Make targets during normal development. Use these raw commands when debugging Compose behavior directly.

```bash
docker compose up -d --build
# start the main stack

docker compose up frontend
# start the frontend service interactively

docker compose run --rm --no-deps frontend npm run build
# build the frontend

docker compose run --rm --no-deps api ruff check backend agent scripts
docker compose run --rm --no-deps api ruff format --check backend agent scripts
# run lint checks inside the API container

docker compose run --rm api pytest backend/tests agent/tests
# run the Python test suite inside Docker

docker compose run --rm api alembic -c backend/alembic.ini upgrade head
# apply database migrations inside Docker

docker compose logs -f api
# follow API logs

docker compose logs -f agent
# follow agent logs

docker compose exec agent python -m paperracks_agent.cli --help
# show agent CLI help in the running agent container

docker compose --profile extraction up -d grobid
# start GROBID when extraction work is needed

docker compose --profile ai up -d ollama
# start Ollama when local AI work is needed

docker compose down
# stop containers but keep volumes

docker compose down -v
# stop containers and delete volumes; destructive
```

## Service map

| Service | Role | Started by |
|---|---|---|
| `postgres` | PostgreSQL database, pgvector support | `make up`, `make up-api`, `make up-infra` |
| `redis` | queue/cache service | `make up`, `make up-api`, `make up-infra` |
| `api` | FastAPI backend | `make up`, `make up-api` |
| `worker` | background job worker | `make up` |
| `agent` | local workstation agent scaffold | `make up` |
| `frontend` | web UI development server | `make up`, `make frontend-dev` |
| `grobid` | optional PDF extraction service | `docker compose --profile extraction up -d grobid` |
| `ollama` | optional local AI service | `docker compose --profile ai up -d ollama` |

## What should run in Docker?

Run these in Docker:

```bash
make up                   # application runtime
make migrate              # database migrations
make test                 # tests
make check                # lint + tests
make frontend-build       # frontend build
make bootstrap-admin      # server-local admin bootstrap
make reset-admin-password # server-local credential recovery
```

These may run on the host for speed:

```bash
make fix                  # Ruff autofix
make lint-local           # Ruff check
make precommit            # pre-commit hooks
make docs                 # documentation compilation
make source-archive       # create source archive
```

Rule of thumb:

- if it depends on the app environment, database, services, or deployment behavior, use Docker;
- if it only edits/checks source text, host-local is acceptable.

## First container startup

```bash
make init                 # create .env
make build                # build images
make up                   # start services
make migrate              # migrate database
make bootstrap-admin      # create first owner
make health               # verify API
```

Inspect logs if anything fails:

```bash
make logs-api
make logs-worker
make logs-agent
```

## Backend container workflow

```bash
make up-api               # postgres + redis + api
make migrate              # apply migrations
make logs-api             # follow API logs
make shell-api            # shell into API container
make test-api             # backend tests
```

## Agent container workflow

```bash
make up                   # start stack including agent
make agent-help           # show CLI help
make logs-agent           # inspect agent logs
make shell-agent          # shell into agent container
make test-agent           # agent tests
```

The agent must access local files only through configured roots and known file IDs. It must not expose a generic path-browsing API.

## Frontend container workflow

```bash
make frontend-dev         # start dev server
make frontend-build       # production build
```

## Migration workflow

```bash
# edit SQLAlchemy models

make migration MSG="describe schema change"
make migrate
make test-api
make check
```

Useful DB commands:

```bash
make db-current           # current revision
make db-history           # migration history
make db-shell             # psql shell
```

## Optional GROBID service

Start GROBID only when working on extraction:

```bash
docker compose --profile extraction up -d grobid
```

GROBID should be internal to the Docker network or backend. Do not expose it directly to the LAN.

## Optional Ollama service

Start Ollama only when working on local AI:

```bash
docker compose --profile ai up -d ollama
```

Local AI features should be optional and should not block core library functionality.

## Resetting state

Soft stop:

```bash
make down
```

Hard reset:

```bash
make clean
```

`make clean` removes volumes/data. Use it only when you intentionally want to reset the development database.

## Before pushing

```bash
make ready
git status
git add .
git commit -m "..."
git push
```

If `make ready` changes files, inspect and stage those changes before committing.

## Bootstrap an owner and smoke-test the API

```bash
docker compose exec api python -c \
  "from scripts.bootstrap_admin import create_first_owner; create_first_owner('owner','change-me-please')"
curl -fsS -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' -d '{"username":"owner","password":"change-me-please"}'
```

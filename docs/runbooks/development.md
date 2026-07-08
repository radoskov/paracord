# Development & Containers

Consolidated runbook (2026-07-08). Combines the former `development_setup.md` (the "what do I do next" narrative) and `dev_containers.md` (the container / Docker Compose reference). Both are preserved in full; the overlap between them is intentional and left intact — treat `development_setup.md` as the workflow guide and `dev_containers.md` as the container reference.

## Contents
- Development Setup Runbook — first setup, dev loop, per-area workflows, before-push
- Development & Evaluation Containers — Compose reference, service map, container workflows, overload knobs


---

<!-- consolidated from docs/runbooks/development_setup.md -->

## Development Setup Runbook

This is the main “what do I do next?” runbook for PaRacORD development.

The project is Docker-first:

- runtime services run in Docker Compose;
- database migrations run in Docker Compose;
- tests run in Docker Compose;
- host-local tools are used only for fast source cleanup, such as Ruff and pre-commit.

## 30-second first setup

```bash
make init                 # create .env from .env.example if missing
pip install pre-commit    # install pre-commit on the host, once
pre-commit install        # enable automatic checks before git commit

make up                   # build and start postgres, redis, api, worker, agent, frontend
make migrate              # apply database migrations inside the api container
make bootstrap-admin      # create the first owner account
make health               # check that the API responds
```

After this, open the frontend and API in your browser according to the ports configured in `docker-compose.yml` / `.env`.

## Normal development loop

```bash
make up                   # start the stack if it is not already running

# edit code

make fix                  # fast host-local Ruff autofix and formatting
pre-commit run --all-files # run all pre-commit hooks manually
make check                # host-local lint + Docker tests

git status
git add .
git commit -m "Describe the change"
git push
```

If `pre-commit` says a hook failed because files were modified, that is usually normal. Review the changes, stage them, and run it again:

```bash
git diff
git add .
pre-commit run --all-files
```

## Common tasks

```bash
make help                 # show all Make targets with short descriptions

make init                 # create .env from .env.example if missing
make build                # build Docker Compose images
make up                   # start the full development stack
make up-api               # start postgres, redis, and api
make up-infra             # start only postgres and redis
make up-extraction        # start the stack plus GROBID (extraction profile)
make up-ai                # start the stack plus Ollama (ai profile)
make ps                   # show container status
make logs                 # follow logs for all services
make logs-api             # follow API logs
make logs-worker          # follow worker logs
make logs-agent           # follow agent logs

make down                 # stop containers but keep database volumes
make clean                # stop containers and delete ALL volumes/data (incl. models); destructive
make rebuild              # rebuild images --no-cache and recreate containers (keeps volumes/data)
make fresh CONFIRM=1      # clean slate: drop DB + library + frontend deps, keep models, then rebuild
make smoke                # assert the dev stack is healthy (api/frontend/worker/postgres/redis)

make migrate              # apply Alembic migrations in the API container
make migration MSG="..."  # create a new Alembic migration
make db-current           # show current Alembic revision
make db-history           # show migration history
make db-shell             # open psql in the postgres container

make fix                  # host-local Ruff autofix and format
make lint                 # host-local Ruff check + format check
make precommit            # run all pre-commit hooks on all files

make test                 # backend (api container) + agent (agent container) tests
make test-api             # run backend tests in the API container
make test-agent           # run agent tests in the agent container
make test-local           # run tests on the host, only if dependencies are installed
make test-safety          # deeper adversarial security/attack/web-stability battery (Batch S; @safety, NOT in make test/test-full)

make check                # lint + Docker backend/agent tests + migration parity
make ready                # fix + pre-commit + check + frontend-check (run before pushing)
make ci                   # mirror CI: lint, tests, migration parity, frontend, secrets

make bootstrap-admin      # create first owner account
make reset-admin-password # reset an owner/admin password from server console

make frontend-dev         # start the frontend dev server in Docker
make frontend-build       # build the frontend in Docker
make agent-help           # show local agent CLI help in Docker

make docs                 # compile LaTeX documentation
make source-archive       # create source archive
```

## Which command should I run?

| Situation | Command |
|---|---|
| First checkout | `make init` |
| Start everything | `make up` (core) / `make up-all` (+ GROBID + Ollama) |
| After a `git pull` (self-heals deps/ownership/migrations) | `make up-all` |
| Confirm the stack is healthy | `make smoke` |
| Stop without deleting data | `make down` |
| Build caches wedged / bad container state (keep data) | `make rebuild` |
| Guaranteed clean slate (drops DB + library, keeps models) | `make fresh CONFIRM=1` |
| Full local reset (drops everything incl. models) | `make clean` |
| Apply DB migrations | `make migrate` |
| Create DB migration | `make migration MSG="..."` |
| Fast local formatting | `make fix` |
| Run pre-commit manually | `make precommit` |
| Run Docker tests | `make test` |
| Run Docker lint + tests | `make check` |
| Before pushing | `make ready` |
| Create first owner | `make bootstrap-admin` |
| Recover owner/admin password | `make reset-admin-password` |
| See all commands | `make help` |

## First-time setup in detail

### 1. Clone the repository

```bash
git clone https://github.com/radoskov/paracord.git
cd paracord
```

### 2. Create local environment config

```bash
make init
```

This creates `.env` from `.env.example` if `.env` does not already exist.

Do not commit `.env`.

### 3. Install pre-commit hooks

```bash
pip install pre-commit
pre-commit install
```

Run all hooks once:

```bash
pre-commit run --all-files
```

If the hooks modify files, stage the changes and run again:

```bash
git add .
pre-commit run --all-files
```

### 4. Start the development stack

```bash
make up
```

This builds and starts the main Compose services.

### 5. Apply database migrations

```bash
make migrate
```

### 6. Create the first owner account

```bash
make bootstrap-admin
```

There is intentionally no unauthenticated browser-based credential recovery flow.

### 7. Verify the API

```bash
make health
```

## Normal code-change workflow

For ordinary backend, agent, script, frontend, or documentation changes:

```bash
# edit files

make fix
pre-commit run --all-files
make check

git status
git add .
git commit -m "..."
git push
```

Use `make ready` before pushing — it mirrors the CI surface so a green `ready` means a green CI.
`ready` runs `fix` + `precommit` + `check` + `frontend-check`, where:

- `check` = host-local Ruff lint + Docker backend/agent tests + **migration parity**
  (`test-migrations`, which applies Alembic against the compose Postgres; it self-skips if no
  Postgres is reachable).
- `frontend-check` = `npm ci` + Vitest component tests + `npm run build` in Docker.

Docker is the authoritative environment for tests (runtime + database); Ruff is pure static
analysis and runs on the host. `make check` alone is the faster backend-only subset.

## Database schema-change workflow

When changing SQLAlchemy models or database schema:

```bash
# edit models

make migration MSG="describe schema change"
make migrate
make check

git status
git add .
git commit -m "Add migration for ..."
```

Useful inspection commands:

```bash
make db-current           # current DB revision
make db-history           # migration history
make db-shell             # open psql
```

Do not create migrations from a random host-local Alembic environment unless you intentionally know why.

## Backend/API workflow

```bash
make up-api               # start postgres, redis, api
make logs-api             # inspect API logs
make shell-api            # open shell inside API container
make test-api             # run backend tests
make migrate              # apply migrations
```

For quick host-local API development, if dependencies are installed on the host:

```bash
make backend-dev
```

Docker remains the source of truth before pushing.

## Agent workflow

```bash
make up                   # start server and agent services
make agent-help           # show agent CLI help
make logs-agent           # inspect agent logs
make shell-agent          # open shell inside agent container
make test-agent           # run agent tests
```

The agent must never expose arbitrary filesystem browsing. It should operate through configured roots and known file IDs.

## Frontend workflow

```bash
make frontend-dev         # run frontend dev server in Docker
make frontend-build       # build frontend in Docker
```

If a contributor intentionally chooses host-local frontend tooling, they may use `npm` directly in `frontend/`, but Docker is preferred for consistent development.

## Lint and formatting workflow

Ruff is pure static analysis (no runtime, database, or services), so lint and format run
host-local. Versions are pinned via `.pre-commit-config.yaml` / `requirements-dev.txt`.

Auto-fix:

```bash
make fix
```

Check (no changes):

```bash
make lint
```

Manual equivalent:

```bash
ruff check backend agent scripts frontend config --fix    # fix
ruff format backend agent scripts frontend config

ruff check backend agent scripts frontend config          # check-only
ruff format --check backend agent scripts frontend config
```

CI should use check-only commands, not auto-fix commands.

## Test workflow

Authoritative test command:

```bash
make test
```

Full verification:

```bash
make check
```

Host-local tests are optional:

```bash
make test-local
```

Host-local tests are only for quick iteration. Docker tests are authoritative.

## Credential recovery workflow

Create the first owner:

```bash
make bootstrap-admin
```

Reset an owner/admin password:

```bash
make reset-admin-password
```

Credential recovery must be performed from the server console or inside the backend/API container. It must not be exposed through an unauthenticated web route.

## Documentation workflow

Compile the LaTeX manual:

```bash
make docs
```

## Cleanup workflow

Stop containers but keep database and volumes:

```bash
make down
```

Stop containers and delete volumes/data:

```bash
make clean
```

Use `make clean` only when you intentionally want to reset local state.

## Recommended before every push

```bash
make ready
git status
git add .
git commit -m "..."
git push
```

If `make ready` changed files, inspect and stage the changes before committing.

Do not enable LAN binding until authentication is implemented and verified.

---

<!-- consolidated from docs/runbooks/dev_containers.md -->

## Development & Evaluation Containers

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

make test                 # backend tests (api container) + agent tests (agent container)
make test-api             # run backend tests in the API container
make test-agent           # run agent tests in the agent container
make lint                 # host-local Ruff check + format check
make fix                  # host-local Ruff autofix and formatting
make check                # host-local lint + Docker tests
make ready                # local autofix + pre-commit + lint + tests

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

ruff check backend agent scripts frontend config
ruff format --check backend agent scripts frontend config
# lint checks run host-local (Ruff needs no runtime/services)

docker compose run --rm --no-deps api pytest backend/tests
docker compose run --rm --no-deps agent pytest agent/tests
# backend tests run in the API container; agent tests in the agent container

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
| `grobid` | optional PDF extraction service | `make up-extraction` (profile `extraction`) |
| `ollama` | optional local AI service | `make up-ai` (profile `ai`) |

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
make lint                 # Ruff check + format check
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
make frontend-test        # component tests (Vitest + jsdom)
```

> **Gotcha — root-owned files from in-container `npm install`.** The `frontend` container runs
> as root, so running `npm install` (or anything that writes `frontend/package-lock.json` /
> `node_modules`) *inside* the container creates **root-owned** files on the host bind mount.
> That breaks host-side tooling — e.g. the pre-commit `end-of-file-fixer` hook fails with
> `PermissionError` and the commit is rejected.
>
> When you change frontend dependencies, prefer one of:
> - run `npm install` **on the host** (if Node is available), so the lockfile is host-owned; or
> - after a containerized install, fix ownership: `sudo chown "$USER" frontend/package-lock.json`
>   (or re-create it host-owned: `cp f /tmp/x && rm f && mv /tmp/x f`).
>
> Node dependencies themselves live in the `paperracks_frontend_node_modules` volume (not the
> host), so only the lockfile is affected.

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

Start GROBID only when working on extraction (brings up the stack plus GROBID):

```bash
make up-extraction
# equivalent: docker compose --profile extraction up -d
```

GROBID should be internal to the Docker network or backend. Do not expose it directly to the LAN.

## Optional Ollama service

Start Ollama only when working on local AI (brings up the stack plus Ollama):

```bash
make up-ai
# equivalent: docker compose --profile ai up -d
```

Local AI features should be optional and should not block core library functionality.

## Overload protection knobs (D1)

Four owner-editable settings live on the `app_config` singleton and are edited from the admin
**Settings** tab (or `PATCH /api/v1/admin/app-config`). They overlay the built-in defaults; an
absent row reproduces the defaults.

| Setting | Default | Effect | Applies |
| --- | --- | --- | --- |
| `rate_limit_per_client_per_min` | 60 | Max requests/minute per client (bearer token, else IP). Over → HTTP 429. | immediately |
| `rate_limit_global_per_min` | 300 | Max requests/minute across all clients. Over → HTTP 429. | immediately |
| `max_batch_items` | 100 | Max items in one client import batch (BibTeX/RIS/CSL/agent manifest/citation batch). Over → HTTP 413. | immediately |
| `rq_worker_count` | 2 | Number of RQ extraction worker processes the supervisor launches. | **worker restart** |

Rate limiting is a shared Redis counter across API workers and **fails open** — if Redis is
unreachable the request is allowed rather than blocked (a dead Redis must never take the API down).
`/api/v1/health` and the docs/schema are exempt. Server-folder scans are exempt from `max_batch_items`
(a local scan is not a client batch); the local agent splits oversized scans into `max_batch_items`
chunks automatically (it reads the cap from `GET /api/v1/agents/me`).

The worker container runs a supervisor (`python -m app.workers.supervisor`) that reads
`rq_worker_count` **once at startup** and launches that many `rq worker … paracord` children
(restarting any that die; terminating them cleanly on SIGTERM). Because the count is read only at
start, changing it requires a worker-container restart:

```bash
# after saving a new rq_worker_count in the admin Settings tab
docker compose restart worker
make logs-worker            # confirms "launching N RQ worker(s)"
```

If the DB/config is unreachable at worker startup the supervisor logs a warning and falls back to
the default count (never zero workers).

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

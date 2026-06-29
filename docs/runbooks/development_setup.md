# Development Setup Runbook

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
make clean                # stop containers and delete volumes/data; destructive
make rebuild              # rebuild Docker images from scratch

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
| Start everything | `make up` |
| Stop without deleting data | `make down` |
| Full local reset | `make clean` |
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

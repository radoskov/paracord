# PaRacORD

**PaRacORD** — **Pa**per **Rac**ks for **O**rganization, **R**etrieval, and **D**iscovery — is a local-first, self-hostable scientific-paper library and literature-graph system for Linux.

It is designed around:

* a central server that can run on the local machine or another PC on the local network;
* one or more local workstation agents;
* GROBID-based PDF extraction;
* original-folder PDF references plus optional “teleport” into a managed server library;
* shelves, racks, tags, citation contexts, citation graphs, and scoped citation summaries;
* citation export;
* local summaries, topic modeling, keyword suggestions, and semantic search.

This repository is currently an implementation scaffold. It contains the intended project structure, core interfaces, placeholder services, documentation sources, runbooks, and agent-oriented work packages. It is not yet a production application.

## Core goals

* Keep PDFs in their original folders by default.
* Allow selected PDFs to be teleported to a managed server-side library store.
* Support an external server on the local network plus an on-PC local agent.
* Prevent arbitrary filesystem browsing from the browser or server.
* Extract metadata, full text, references, citation mentions, citation contexts, and PDF coordinates.
* Organize works into shelves and shelves into racks, with many-to-many membership.
* Provide local citation graphs and citation summaries scoped to the full library, rack, shelf, search result, or selected papers.
* Export citations for papers, shelves, racks, and selections in BibTeX, BibLaTeX, RIS, CSL JSON, Markdown, HTML, and free-text bibliography formats.
* Provide local AI summaries, user/external summaries, keyword suggestions, topic modeling, and semantic search.
* Require authenticated access. There is intentionally no guest/read-only anonymous mode.
* Provide server-local credential recovery through a command-line tool, not through an unauthenticated web endpoint.

## Repository layout

```text
backend/             FastAPI backend scaffold
agent/               Local workstation agent scaffold
frontend/            Web UI scaffold
config/              Example server and agent configuration
docs/                Markdown docs, LaTeX manual sources, runbooks, diagrams
scripts/             Operational helper scripts
SPECIFICATION.md     Full implementation specification
CHANGELOG.md         Project changelog
PROGRESS.md          Current build status and next steps
AGENTS.md            Coding-agent coordination guide
WORK_SPLIT.md        Suggested work packages for parallel agents
HINTS_FOR_AGENTS.md  Practical implementation hints and constraints
```

## Development philosophy

Docker Compose is the source of truth for runtime, migrations, tests, and deployment-like verification.

Host-local tools are allowed for fast source hygiene only:

* Ruff auto-fixing and formatting;
* pre-commit hooks;
* docs compilation;
* source archive creation.

The intended workflow is:

```bash
# Fast local source hygiene
make fix
pre-commit run --all-files

# Docker-based verification
make check

# Or the full local readiness flow
make ready
```

CI should check formatting/linting and run tests, but auto-fixes should happen locally before commit.

## First run

Create local environment values:

```bash
make init
```

This copies `.env.example` to `.env` if `.env` does not already exist.

Build and start the development stack:

```bash
make up
```

This starts the default Docker Compose services:

* `postgres`
* `redis`
* `api`
* `worker`
* `agent`
* `frontend`

Check service state:

```bash
make ps
```

Follow logs:

```bash
make logs
```

Apply migrations manually, if needed:

```bash
make migrate
```

The API container also applies migrations on normal server startup.

Run the verification pipeline (host-local lint + Docker tests):

```bash
make check
```

This runs:

```bash
make lint    # host-local Ruff check + format check
make test    # backend tests (api container) + agent tests (agent container)
```

Stop containers while keeping database volumes:

```bash
make down
```

Remove containers and named volumes/data:

```bash
make clean
```

Use `make clean` carefully. It is destructive.

## Common Make targets

Run:

```bash
make help
```

Important targets:

```text
make init                  Create .env from .env.example if missing
make build                 Build Docker Compose images
make up                    Build and start the full development stack
make up-api                Start Postgres, Redis, and API
make up-infra              Start only Postgres and Redis
make up-extraction         Start GROBID (required for PDF extraction; not started by `make up`)
make ps                    Show service status
make logs                  Follow all logs
make down                  Stop containers but keep volumes/data
make clean                 Stop containers and remove named volumes/data

make migrate               Apply Alembic migrations inside the API container
make migration MSG="..."   Create an autogenerated Alembic revision
make db-current            Show current Alembic revision
make db-history            Show Alembic migration history
make db-shell              Open psql in the Postgres container

make fix                   Auto-fix Ruff lint/formatting on the host
make lint                  Run Ruff lint + format checks on the host
make precommit             Run all pre-commit hooks on all files
make test                  Run backend (api container) + agent (agent container) tests
make test-api              Run backend tests in the API container
make test-agent            Run agent tests in the agent container
make test-local            Run host tests
make check                 Lint + Docker backend/agent tests + migration parity
make ready                 Auto-fix, pre-commit, then full backend + frontend checks
make ci                    Mirror CI locally (lint, tests, migrations, frontend, secrets)

make bootstrap-admin       Create the initial owner account
make reset-admin-password  Reset an owner/admin password from the server console
make docs                  Compile LaTeX documentation
make source-archive        Create a source archive
```

## Pre-commit hooks

Install once:

```bash
pip install pre-commit
pre-commit install
```

Run manually over the full repository:

```bash
pre-commit run --all-files
```

When a hook modifies files, `pre-commit` reports failure and stops. This is expected. Review the changes, stage them, and run the command again:

```bash
git status
git diff
git add .
pre-commit run --all-files
```

## Linting and formatting

Preferred local autofix:

```bash
make fix
```

Lint/format runs host-local — Ruff is pure static analysis and needs no runtime or
services (versions are pinned via `.pre-commit-config.yaml` / `requirements-dev.txt`):

```bash
make lint   # ruff check + ruff format --check
make fix     # auto-fix: ruff check --fix + ruff format
```

Equivalent explicit commands:

```bash
ruff check backend agent scripts frontend config
ruff format --check backend agent scripts frontend config
```

## Testing

The default project test command runs in Docker:

```bash
make test
```

Host-local tests are available for quick iteration when the host environment has the required Python dependencies installed:

```bash
make test-local
```

Use Docker-based tests before pushing.

## Database migrations

Create a migration:

```bash
make migration MSG="create works table"
```

Apply migrations:

```bash
make migrate
```

Show current revision:

```bash
make db-current
```

Open a database shell:

```bash
make db-shell
```

Migrations should be generated and applied inside Docker unless there is a specific reason to use a host-local environment.

## Server-local credential recovery

Credential recovery is intentionally not exposed through an unauthenticated web route.

Create the first owner:

```bash
make bootstrap-admin
```

Reset an owner/admin password from the server console:

```bash
make reset-admin-password
```

See `docs/runbooks/credential_recovery.md`.

## Security assumptions

The server and web browser must never receive arbitrary local filesystem access.

Files are accessed only through:

* configured server-side roots;
* managed library objects;
* local-agent file IDs;
* explicit teleport/upload flows.

The agent refuses raw path requests and exposes only files that it has indexed from its configured roots.

No real credentials, secrets, or personal data may be committed.

Light configuration such as URLs, IPs, ports, feature flags, and model names goes through `.env` or `config/*.local.yaml`.

Serious secrets are read from environment variables and never hardcoded.

User passwords are bcrypt-hashed.

See `docs/runbooks/secrets_management.md`.

## Documentation

The implementation manual source lives in `docs/latex/`.

Compile it with:

```bash
make docs
```

or directly:

```bash
cd docs
./compile_docs.sh
```

The script uses `latexmk` when available and falls back to repeated `pdflatex` runs.

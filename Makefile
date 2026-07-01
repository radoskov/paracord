# PaRacORD developer Makefile
#
# Philosophy:
# - Docker Compose is the source of truth for runtime, migrations, and tests.
# - Host-local tools are allowed only for fast source hygiene: Ruff, pre-commit,
#   docs, and archive generation.
# - CI should use check-only commands; local developer commands may auto-fix.

SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

COMPOSE ?= docker compose
COMPOSE_PROD ?= docker compose -f docker-compose.yml -f docker-compose.prod.yml
API_SERVICE ?= api
AGENT_SERVICE ?= agent
FRONTEND_SERVICE ?= frontend

PY_PATHS := backend agent scripts frontend config frontend config
PYTEST_PATHS := backend/tests agent/tests

ALEMBIC := alembic -c backend/alembic.ini
API_RUN := $(COMPOSE) run --rm $(API_SERVICE)
API_RUN_NODEPS := $(COMPOSE) run --rm --no-deps $(API_SERVICE)
AGENT_RUN := $(COMPOSE) run --rm --no-deps $(AGENT_SERVICE)
FRONTEND_RUN := $(COMPOSE) run --rm --no-deps $(FRONTEND_SERVICE)

NODE_IMAGE ?= node:24-bookworm-slim

.PHONY: help
help: ## Show this help.
	@echo "PaRacORD developer commands"
	@echo
	@echo "Available commands:"
	@grep -E '^[a-zA-Z0-9_.-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-24s %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Setup / lifecycle
# ---------------------------------------------------------------------------

.PHONY: init
init: ## Create .env from .env.example if missing.
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "Created .env from .env.example"; \
	else \
		echo ".env already exists"; \
	fi

.PHONY: build
build: ## Build all Docker Compose images.
	$(COMPOSE) build

.PHONY: up
up: init ## Build and start the full development stack.
	$(COMPOSE) up -d --build

.PHONY: prod-build
prod-build: init ## Build the production images (api + worker: gunicorn; frontend: nginx).
	$(COMPOSE_PROD) build

.PHONY: prod-up
prod-up: init ## Build and start the production stack (gunicorn + nginx static frontend).
	$(COMPOSE_PROD) up -d --build

.PHONY: prod-down
prod-down: ## Stop the production stack.
	$(COMPOSE_PROD) down

.PHONY: prod-smoke
prod-smoke: init ## Build+start the prod stack and assert the API health endpoint responds.
	$(COMPOSE_PROD) up -d --build postgres redis api
	@echo "Waiting for the API to become healthy…"
	@for i in $$(seq 1 30); do \
	  if $(COMPOSE_PROD) exec -T api curl -fsS http://localhost:8000/api/v1/health >/dev/null 2>&1; then \
	    echo "✅ prod smoke passed: /api/v1/health is responding"; exit 0; \
	  fi; \
	  sleep 2; \
	done; \
	echo "❌ prod smoke failed: API did not become healthy in time"; \
	$(COMPOSE_PROD) logs --tail=50 api; exit 1

.PHONY: backup
backup: ## Back up the database + managed library to ./backups (BACKUP_DIR overrides).
	@mkdir -p $${BACKUP_DIR:-./backups}
	@ts=$$(date +%Y%m%d-%H%M%S); out=$${BACKUP_DIR:-./backups}; \
	echo "Dumping database → $$out/db-$$ts.sql.gz"; \
	$(COMPOSE) exec -T postgres sh -c 'pg_dump -U $$POSTGRES_USER $$POSTGRES_DB' | gzip > $$out/db-$$ts.sql.gz; \
	echo "Archiving managed library → $$out/library-$$ts.tar.gz"; \
	$(COMPOSE) run --rm --no-deps -T -v $$(pwd)/$$out:/backup $(API_SERVICE) \
	  sh -c 'tar czf /backup/library-'$$ts'.tar.gz -C /app storage' 2>/dev/null || \
	  echo "(library volume archive skipped — start the stack first if you need it)"; \
	echo "✅ backup complete in $$out"

.PHONY: restore
restore: ## Restore the database from a dump: make restore RESTORE=backups/db-YYYYMMDD-HHMMSS.sql.gz
	@test -n "$(RESTORE)" || { echo 'Usage: make restore RESTORE=backups/db-YYYYMMDD-HHMMSS.sql.gz'; exit 1; }
	@echo "Restoring $(RESTORE) into the database (existing data is dropped)…"
	@gunzip -c $(RESTORE) | $(COMPOSE) exec -T postgres sh -c 'psql -U $$POSTGRES_USER -d $$POSTGRES_DB'
	@echo "✅ restore complete"

.PHONY: restore-dry-run
restore-dry-run: ## Validate a dump + report what a restore WOULD change, without writing: make restore-dry-run RESTORE=backups/db-...sql.gz
	@test -n "$(RESTORE)" || { echo 'Usage: make restore-dry-run RESTORE=backups/db-YYYYMMDD-HHMMSS.sql.gz'; exit 1; }
	@test -f "$(RESTORE)" || { echo "❌ dump not found: $(RESTORE)"; exit 1; }
	@echo "DRY RUN — no changes will be written."
	@echo "Dump file:      $(RESTORE)"
	@echo -n "Gzip integrity: "; gzip -t "$(RESTORE)" && echo "OK" || { echo "FAILED"; exit 1; }
	@echo -n "Target DB:      "; $(COMPOSE) exec -T postgres sh -c 'echo "$$POSTGRES_DB (user $$POSTGRES_USER)"' 2>/dev/null || echo "(postgres not running — start it to resolve the target DB)"
	@echo "Looks like a pg dump: "; gunzip -c "$(RESTORE)" | head -n 40 | grep -Eq 'PostgreSQL database dump|CREATE TABLE|COPY |INSERT INTO|SET ' && echo "  yes — SQL statements detected" || { echo "  ❌ no recognizable pg dump statements in the header"; exit 1; }
	@echo "Statements that WOULD run (counts from the dump):"
	@gunzip -c "$(RESTORE)" | grep -cE '^CREATE TABLE'  | sed 's/^/  CREATE TABLE: /'
	@gunzip -c "$(RESTORE)" | grep -cE '^COPY '         | sed 's/^/  COPY (bulk load): /'
	@gunzip -c "$(RESTORE)" | grep -cE '^INSERT INTO '  | sed 's/^/  INSERT INTO: /'
	@echo "✅ dry-run complete — dump is a valid, loadable pg dump. Re-run without -dry-run to apply."

.PHONY: up-api
up-api: init ## Start only runtime services needed by the API.
	$(COMPOSE) up -d --build postgres redis api

.PHONY: up-infra
up-infra: init ## Start only infrastructure services: Postgres and Redis.
	$(COMPOSE) up -d postgres redis

.PHONY: up-frontend
up-frontend: init ## Start the frontend service.
	$(COMPOSE) up frontend

.PHONY: up-extraction
up-extraction: init ## Start GROBID extraction profile.
	$(COMPOSE) --profile extraction up -d grobid

.PHONY: down-extraction
down-extraction: ## Stop GROBID extraction profile.
	$(COMPOSE) --profile extraction stop grobid
	$(COMPOSE) --profile extraction rm -f grobid

.PHONY: up-ai
up-ai: init ## Start Ollama AI profile.
	$(COMPOSE) --profile ai up -d ollama

.PHONY: down-ai
down-ai: ## Stop Ollama AI profile.
	$(COMPOSE) --profile ai stop ollama
	$(COMPOSE) --profile ai rm -f ollama

.PHONY: ps
ps: ## Show Docker Compose service status.
	$(COMPOSE) ps

.PHONY: logs
logs: ## Follow logs for all services.
	$(COMPOSE) logs -f

.PHONY: logs-api
logs-api: ## Follow API logs.
	$(COMPOSE) logs -f api

.PHONY: logs-worker
logs-worker: ## Follow worker logs.
	$(COMPOSE) logs -f worker

.PHONY: logs-agent
logs-agent: ## Follow agent logs.
	$(COMPOSE) logs -f agent

.PHONY: down
down: ## Stop containers but keep named volumes/data.
	$(COMPOSE) down

.PHONY: hard-down
hard-down: ## Stop containers but keep named volumes/data.
	$(COMPOSE) --profile '*' down

.PHONY: clean
clean: ## Stop containers and remove named volumes/data. Destructive.
	$(COMPOSE) down -v

.PHONY: rebuild
rebuild: ## Rebuild images from scratch.
	$(COMPOSE) build --no-cache

# Docker orphaned containers
.PHONY: docker-orphans
docker-orphans: ## Show containers still attached to the Compose default network.
	@docker ps -a --filter network=$(COMPOSE_PROJECT_NAME)_default

.PHONY: down-orphans
down-orphans: ## Stop stack and remove orphan containers.
	$(COMPOSE) down --remove-orphans

.PHONY: clean-orphans
clean-orphans: ## Stop stack, remove orphan containers, and remove volumes. Destructive.
	$(COMPOSE) down --remove-orphans -v
# ---------------------------------------------------------------------------
# Database / migrations
# ---------------------------------------------------------------------------

.PHONY: migrate
migrate: init ## Apply backend database migrations inside the API container.
	$(API_RUN) $(ALEMBIC) upgrade head

.PHONY: migration
migration: init ## Create a new Alembic revision. Usage: make migration MSG="message"
	@if [ -z "$(MSG)" ]; then \
		echo 'Usage: make migration MSG="describe change"'; \
		exit 2; \
	fi
	$(API_RUN) $(ALEMBIC) revision --autogenerate -m "$(MSG)"

.PHONY: db-current
db-current: init ## Show current Alembic revision.
	$(API_RUN) $(ALEMBIC) current

.PHONY: db-history
db-history: init ## Show Alembic migration history.
	$(API_RUN) $(ALEMBIC) history --verbose

.PHONY: db-shell
db-shell: init ## Open psql inside the Postgres container.
	$(COMPOSE) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

# ---------------------------------------------------------------------------
# Tests and checks
# ---------------------------------------------------------------------------

.PHONY: test
test: test-api test-agent ## Run backend and agent tests, each in its own container.

# Keep pytest's cache out of the bind-mounted source tree (it would be root-owned on the host).
PYTEST := python -m pytest -o cache_dir=/tmp/paracord-pytest-cache

.PHONY: test-api
test-api: init ## Run backend tests inside the API container.
	$(API_RUN_NODEPS) $(PYTEST) backend/tests

.PHONY: test-agent
test-agent: init ## Run agent tests inside the agent container.
	$(AGENT_RUN) $(PYTEST) agent/tests

.PHONY: test-migrations
test-migrations: init ## Run the migration<->model parity test against the compose Postgres.
	$(API_RUN) $(PYTEST) backend/tests/test_migration_parity.py -v

.PHONY: test-local
test-local: ## Run tests on the host interpreter.
	python -m pytest $(PYTEST_PATHS)

# Ruff is pure static analysis (no runtime/services needed), so lint/format are
# host-local. Versions are pinned via .pre-commit-config.yaml and requirements-dev.txt.
.PHONY: lint
lint: ## Run Ruff lint and format checks on the host.
	ruff check $(PY_PATHS)
	ruff format --check $(PY_PATHS)

.PHONY: fix
fix: ## Auto-fix Ruff lint and formatting on the host.
	ruff check $(PY_PATHS) --fix
	ruff format $(PY_PATHS)

.PHONY: precommit
precommit: ## Run all pre-commit hooks on all files.
	pre-commit run --all-files

.PHONY: check-secrets
check-secrets: ## Run the repository secret scanner.
	python scripts/check_secrets.py --all

.PHONY: openapi
openapi: init ## Regenerate the committed OpenAPI schema (backend/openapi.json) from app.openapi().
	$(API_RUN_NODEPS) python scripts/dump_openapi.py

.PHONY: openapi-check
openapi-check: init ## Fail if backend/openapi.json is stale relative to app.openapi().
	@$(API_RUN_NODEPS) sh -c 'python scripts/dump_openapi.py /tmp/openapi-check.json >/dev/null \
		&& diff -u backend/openapi.json /tmp/openapi-check.json' \
		&& echo "✅ OpenAPI schema is current" \
		|| { echo "❌ backend/openapi.json is out of date — run '\''make openapi'\'' and commit."; exit 1; }

.PHONY: check
check: lint test test-migrations openapi-check ## Host-local lint + Docker backend/agent tests + migration parity + OpenAPI freshness.

.PHONY: ready
ready: fix precommit check frontend-check ## Auto-fix, pre-commit, then full backend + frontend checks.

.PHONY: ci
ci: lint test test-migrations openapi-check frontend-check check-secrets ## Mirror the CI checks locally.

# ---------------------------------------------------------------------------
# Application commands
# ---------------------------------------------------------------------------

.PHONY: shell-api
shell-api: init ## Open a shell inside the API container.
	$(API_RUN) bash

.PHONY: shell-agent
shell-agent: init ## Open a shell inside the agent container.
	$(AGENT_RUN) bash

.PHONY: backend-dev
backend-dev: ## Run backend dev server on the host.
	cd backend && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

.PHONY: agent-help
agent-help: init ## Show agent CLI help in Docker.
	$(AGENT_RUN) python -m paperracks_agent.cli --help

.PHONY: agent-dev
agent-dev: agent-help ## Alias for agent-help.

.PHONY: frontend-dev
frontend-dev: init ## Start the frontend dev server in Docker.
	$(COMPOSE) up frontend

.PHONY: frontend-build
frontend-build: init ## Build the frontend in Docker.
	$(FRONTEND_RUN) npm run build

.PHONY: frontend-test
frontend-test: init ## Run frontend component tests (Vitest + jsdom) in Docker.
	$(FRONTEND_RUN) npm run test

.PHONY: frontend-install
frontend-install: init ## Install frontend dependencies in Docker from package-lock.json.
	$(FRONTEND_RUN) npm ci

.PHONY: frontend-lock-check
frontend-lock-check: ## Verify frontend package.json and package-lock.json are in sync using Docker Node.
	docker run --rm \
		-v "$(CURDIR)/frontend:/app/frontend" \
		-w /app/frontend \
		$(NODE_IMAGE) \
		npm ci --ignore-scripts --dry-run

.PHONY: frontend-lock
frontend-lock: ## Repair/refresh frontend/package-lock.json using Docker Node, without host npm.
	docker run --rm \
		-v "$(CURDIR)/frontend:/app/frontend" \
		-w /app/frontend \
		$(NODE_IMAGE) \
		npm install

.PHONY: frontend-check
frontend-check: frontend-install frontend-test frontend-build ## Run frontend install, tests, and build in Docker.

.PHONY: health
health: ## Check local API health endpoint.
	curl -fsS http://127.0.0.1:8000/api/v1/health

# ---------------------------------------------------------------------------
# Admin / operations
# ---------------------------------------------------------------------------

.PHONY: bootstrap-admin
bootstrap-admin: init ## Create the initial owner account inside the API container.
	$(API_RUN) python scripts/bootstrap_admin.py

.PHONY: reset-admin-password
reset-admin-password: init ## Reset an owner/admin password from the server console.
	$(API_RUN) python scripts/reset_admin_password.py

.PHONY: list-users
list-users: init ## List all user accounts from the server console (recovery tooling).
	$(API_RUN) python scripts/list_users.py

.PHONY: revoke-sessions
revoke-sessions: init ## Revoke a user's active sessions from the server console: make revoke-sessions USER=alice
	$(API_RUN) python scripts/revoke_sessions.py $(USER)

.PHONY: source-archive
source-archive: ## Create a source archive.
	python scripts/create_source_archive.py

.PHONY: zip
zip: source-archive ## Alias for source-archive.

# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------

.PHONY: docs
docs: ## Compile LaTeX documentation.
	cd docs && ./compile_docs.sh

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

PY_PATHS := backend agent scripts frontend config
PYTEST_PATHS := backend/tests agent/tests

ALEMBIC := alembic -c backend/alembic.ini
API_RUN := $(COMPOSE) run --rm $(API_SERVICE)
API_RUN_NODEPS := $(COMPOSE) run --rm --no-deps $(API_SERVICE)
AGENT_RUN := $(COMPOSE) run --rm --no-deps $(AGENT_SERVICE)
FRONTEND_RUN := $(COMPOSE) run --rm --no-deps $(FRONTEND_SERVICE)

NODE_IMAGE ?= node:24-bookworm-slim

# Compose project name (used to target this stack's named volumes in `make fresh`). Compose derives
# it from the directory name; override to match if you set COMPOSE_PROJECT_NAME.
PROJECT ?= $(notdir $(CURDIR))

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
init: ## Create .env from .env.example if missing, generating a random POSTGRES_PASSWORD.
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		pw=$$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c 32); \
		if [ -z "$$pw" ]; then pw=$$(openssl rand -hex 16); fi; \
		sed -i.bak "s/change_me_generated_on_init/$$pw/g" .env && rm -f .env.bak; \
		echo "Created .env from .env.example (generated a random POSTGRES_PASSWORD)"; \
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
	$(COMPOSE) exec -T postgres sh -c 'pg_dump --clean --if-exists -U $$POSTGRES_USER $$POSTGRES_DB' | gzip > $$out/db-$$ts.sql.gz; \
	echo "Archiving managed library → $$out/library-$$ts.tar.gz"; \
	$(COMPOSE) run --rm --no-deps -T -v $$(pwd)/$$out:/backup $(API_SERVICE) \
	  sh -c 'tar czf /backup/library-'$$ts'.tar.gz -C /app storage' 2>/dev/null || \
	  echo "(library volume archive skipped — start the stack first if you need it)"; \
	$(COMPOSE) exec -T $(API_SERVICE) python scripts/record_backup_event.py backup.created --artifact "db-$$ts.sql.gz" 2>/dev/null || true; \
	echo "✅ backup complete in $$out"

.PHONY: restore
restore: ## Restore the database from a dump: make restore RESTORE=backups/db-YYYYMMDD-HHMMSS.sql.gz
	@test -n "$(RESTORE)" || { echo 'Usage: make restore RESTORE=backups/db-YYYYMMDD-HHMMSS.sql.gz'; exit 1; }
	@echo "Restoring $(RESTORE) into the database (existing data is dropped)…"
	@echo "Stopping api + worker during the restore…"
	@$(COMPOSE) stop $(API_SERVICE) worker
	@gunzip -c $(RESTORE) | $(COMPOSE) exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U $$POSTGRES_USER -d $$POSTGRES_DB'
	@$(COMPOSE) start $(API_SERVICE) worker
	@$(COMPOSE) exec -T $(API_SERVICE) python scripts/record_backup_event.py restore.completed --artifact "$(RESTORE)" 2>/dev/null || true
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
	@echo -n "Drops existing objects first (--clean dump): "; gunzip -c "$(RESTORE)" | grep -qE '^DROP ' && echo "yes" || { echo "❌ no DROP statements — this dump predates --clean backups and would MERGE into existing data. Re-create it with 'make backup'."; exit 1; }
	@echo "Statements that WOULD run (counts from the dump):"
	@gunzip -c "$(RESTORE)" | grep -cE '^DROP '         | sed 's/^/  DROP (clean-first): /'
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

.PHONY: up-all
up-all: init ## start standard + extraction + ai
	$(COMPOSE) up -d --build
	$(COMPOSE) --profile extraction up -d grobid
	$(COMPOSE) --profile ai up -d ollama

.PHONY: down-ai
down-ai: ## Stop Ollama AI profile.
	$(COMPOSE) --profile ai stop ollama
	$(COMPOSE) --profile ai rm -f ollama

.PHONY: ai-update
ai-update: ## Update Ollama: pull the newest ollama/ollama:latest image + recreate the container.
	@echo "== Ollama version before =="; \
	$(COMPOSE) exec -T ollama ollama --version 2>/dev/null || echo "  (ollama not running)"
	$(COMPOSE) --profile ai pull ollama
	$(COMPOSE) --profile ai up -d ollama
	@echo "Waiting for Ollama to come back up..."; \
	for i in 1 2 3 4 5 6 7 8 9 10; do \
		if $(COMPOSE) exec -T ollama ollama --version >/dev/null 2>&1; then break; fi; sleep 2; \
	done; \
	echo "== Ollama version after =="; \
	$(COMPOSE) exec -T ollama ollama --version 2>/dev/null || echo "  (still starting — check 'make smoke')"; \
	echo "Pulled models persist in the paperracks_ollama volume — nothing to re-download."

.PHONY: build-ml-extraction
build-ml-extraction: ## Build the opt-in ML-extraction image (Nougat/Marker; torch, multi-GB). Enables ocr_backend=full_ml.
	docker build -f backend/Dockerfile --target ml-extraction -t paracord-api:ml-extraction .
	@echo "✅ Built paracord-api:ml-extraction. Run the api/worker from this image and set ocr_backend=full_ml to activate."

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
rebuild: init ## Rebuild images from scratch (no cache) and recreate containers. Use when build caches are wedged.
	$(COMPOSE) build --no-cache
	$(COMPOSE) up -d --force-recreate

.PHONY: fresh
fresh: init ## DESTRUCTIVE clean slate: drop DB + managed library + frontend deps volumes, keep Ollama models, then rebuild+start. Guard: make fresh CONFIRM=1
	@if [ "$(CONFIRM)" != "1" ]; then \
		echo "Refusing: 'make fresh' DROPS the database, the managed library, and the frontend node_modules"; \
		echo "(the Ollama models volume is kept). Re-run to confirm:  make fresh CONFIRM=1"; \
		exit 1; \
	fi
	$(COMPOSE) --profile '*' down
	@for vol in paperracks_postgres paperracks_library paperracks_frontend_node_modules; do \
		ids=$$(docker volume ls -q \
			--filter "label=com.docker.compose.project=$(PROJECT)" \
			--filter "label=com.docker.compose.volume=$$vol"); \
		if [ -n "$$ids" ]; then echo "Dropping volume: $$ids"; docker volume rm $$ids; \
		else echo "No volume for $$vol (nothing to drop)"; fi; \
	done
	@rm -rf frontend/dist
	$(COMPOSE) up -d --build

.PHONY: smoke
smoke: ## Dev-stack health assertions: postgres, redis, api /health, frontend, worker (grobid/ollama if profiled). Non-zero on failure.
	@fail=0; \
	echo "PaRacORD dev smoke test"; \
	if $(COMPOSE) exec -T postgres sh -c 'pg_isready -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"' >/dev/null 2>&1; then \
		echo "  ok   postgres: accepting connections"; else echo "  FAIL postgres: not ready"; fail=1; fi; \
	if $(COMPOSE) exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then \
		echo "  ok   redis: PONG"; else echo "  FAIL redis: no PONG"; fail=1; fi; \
	if curl -fsS http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1; then \
		echo "  ok   api: /api/v1/health 200"; else echo "  FAIL api: /api/v1/health not responding"; fail=1; fi; \
	if curl -fsS http://127.0.0.1:5173 >/dev/null 2>&1; then \
		echo "  ok   frontend: dev server serving on :5173"; else echo "  FAIL frontend: not serving on :5173"; fail=1; fi; \
	wid=$$($(COMPOSE) ps -q worker); \
	if [ -n "$$wid" ] && [ "$$(docker inspect -f '{{.State.Running}}' $$wid 2>/dev/null)" = "true" ]; then \
		echo "  ok   worker: running"; else echo "  FAIL worker: not running"; fail=1; fi; \
	if [ -n "$$($(COMPOSE) ps -q grobid)" ]; then \
		if curl -fsS http://127.0.0.1:8070/api/isalive >/dev/null 2>&1; then \
			echo "  ok   grobid: alive (extraction profile up)"; else echo "  FAIL grobid: up but not alive"; fail=1; fi; \
	else echo "  --   grobid: not started (extraction profile down)"; fi; \
	if [ -n "$$($(COMPOSE) ps -q ollama)" ]; then \
		if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then \
			echo "  ok   ollama: responding (ai profile up)"; else echo "  FAIL ollama: up but not responding"; fail=1; fi; \
	else echo "  --   ollama: not started (ai profile down)"; fi; \
	if [ "$$fail" -eq 0 ]; then echo "✅ smoke passed"; else echo "❌ smoke failed"; fi; \
	exit $$fail

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
test: test-api test-agent ## Run the FAST tier of backend + agent tests (skips @slow; see test-full).

.PHONY: test-full
test-full: test-api-full test-agent-full ## Run the FULL backend + agent suite, including @slow tests.

# Keep pytest's cache out of the bind-mounted source tree (it would be root-owned on the host).
PYTEST := python -m pytest -o cache_dir=/tmp/paracord-pytest-cache
# `slow` tests need a real Postgres, run a full multi-step acceptance flow, or are
# supplementary/forward-looking contract coverage (see the marker docstring in pyproject.toml).
# They still run in CI (bare `pytest`) and in every *-full target — only these fast targets skip them.
# `safety` tests (Batch S) are the DEEPER adversarial battery; they are deselected from BOTH the fast
# and the full core suites and run only via `make test-safety` (or `pytest -m safety`).
PYTEST_FAST := $(PYTEST) -m "not slow and not safety"
PYTEST_FULL := $(PYTEST) -m "not safety"
PYTEST_SAFETY := $(PYTEST) -m safety

.PHONY: test-api
test-api: init ## Run backend tests inside the API container (fast tier: skips @slow).
	$(API_RUN_NODEPS) $(PYTEST_FAST) backend/tests

.PHONY: test-api-full
test-api-full: init ## Run ALL backend tests inside the API container, including @slow (excludes @safety).
	$(API_RUN_NODEPS) $(PYTEST_FULL) backend/tests

.PHONY: test-agent
test-agent: init ## Run agent tests inside the agent container (fast tier: skips @slow).
	$(AGENT_RUN) $(PYTEST_FAST) agent/tests

.PHONY: test-agent-full
test-agent-full: init ## Run ALL agent tests inside the agent container, including @slow (excludes @safety).
	$(AGENT_RUN) $(PYTEST_FULL) agent/tests

.PHONY: test-safety
test-safety: init ## Run the DEEPER adversarial security/attack/web-stability battery (Batch S) in the API container. Postgres-gated probes skip cleanly when absent.
	$(API_RUN_NODEPS) $(PYTEST_SAFETY) backend/tests/safety

.PHONY: test-migrations
test-migrations: init ## Run the migration<->model parity test against the compose Postgres.
	$(API_RUN) $(PYTEST) backend/tests/test_migration_parity.py -v

.PHONY: e2e-install
e2e-install: ## Install the Playwright E2E deps + Chromium browser (needs network; run once).
	cd e2e && npm install && npx playwright install chromium

.PHONY: e2e
e2e: ## Run the Playwright end-to-end browser tests. Requires the dev stack up (make up).
	@# Self-heal the dev server first: a prior `frontend-test`/`compose run` can leave a stale Vite
	@# optimize cache that 504s ("Outdated Optimize Dep") and stops the SPA mounting during e2e.
	$(COMPOSE) exec -T $(FRONTEND_SERVICE) rm -rf node_modules/.vite
	$(COMPOSE) restart $(FRONTEND_SERVICE)
	@# Wait for the restarted dev server to boot AND finish (re)optimizing deps by fetching the app
	@# entry until it serves cleanly — otherwise the first journeys race the optimizer and go flaky.
	@echo "Warming up the frontend dev server…"
	@for i in $$(seq 1 60); do curl -sf -o /dev/null "$${E2E_BASE_URL:-http://127.0.0.1:5173}/src/main.ts" && break; sleep 1; done
	$(COMPOSE) exec -T $(API_SERVICE) python scripts/ensure_e2e_user.py
	$(COMPOSE) exec -T $(API_SERVICE) python scripts/configure_e2e_rate_limits.py enable
	sleep 6
	cd e2e && npx playwright test
	$(COMPOSE) exec -T $(API_SERVICE) python scripts/configure_e2e_rate_limits.py reset

.PHONY: e2e-list
e2e-list: ## Run the Playwright end-to-end browser tests. Requires the dev stack up (make up).
	$(COMPOSE) exec -T $(API_SERVICE) python scripts/ensure_e2e_user.py
	$(COMPOSE) exec -T $(API_SERVICE) python scripts/configure_e2e_rate_limits.py enable
	sleep 6
	cd e2e && bash -o pipefail -c 'npx playwright test --reporter=list 2>&1 | perl -pe "s/\r/\n/g; s/\e\[[0-9;?]*[ABCDGJKHf]//g"'
	$(COMPOSE) exec -T $(API_SERVICE) python scripts/configure_e2e_rate_limits.py reset

.PHONY: test-local
test-local: ## Run the FAST tier of tests on the host interpreter (skips @slow).
	python -m pytest -m "not slow" $(PYTEST_PATHS)

.PHONY: test-local-full
test-local-full: ## Run ALL tests on the host interpreter, including @slow.
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
check: lint test openapi-check ## Host-local lint + FAST backend/agent tests + OpenAPI freshness.

.PHONY: check-full
check-full: lint test-full test-migrations openapi-check ## Host-local lint + FULL backend/agent tests + migration parity + OpenAPI freshness.

.PHONY: ready
ready: fix precommit check frontend-test ## Auto-fix, pre-commit, then the FAST tier of backend + frontend checks. Run before every commit.

.PHONY: ready-full
ready-full: fix precommit check-full frontend-check ## Auto-fix, pre-commit, then the FULL backend + frontend checks (mirrors CI). Run before pushing/opening a PR.

.PHONY: ci
ci: lint test-full test-migrations openapi-check frontend-check check-secrets ## Mirror the CI checks locally (always the full suite).

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

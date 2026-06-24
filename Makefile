.PHONY: help build up down dev-up dev-down backend-dev agent-dev frontend-dev frontend-build migrate test test-local docker-test lint docker-lint check-secrets docs zip

help:
	@echo "PaperRacks developer commands"
	@echo "  make build        Build the api/agent container images"
	@echo "  make up           Build and start the full stack (postgres, redis, api, agent)"
	@echo "  make down         Stop the stack and remove volumes"
	@echo "  make dev-up       Start only local infrastructure (postgres, redis)"
	@echo "  make dev-down     Stop infrastructure"
	@echo "  make backend-dev  Run backend dev server on the host"
	@echo "  make agent-dev    Run agent CLI help on the host"
	@echo "  make frontend-dev Start the Svelte frontend in Docker"
	@echo "  make frontend-build Build the Svelte frontend in Docker"
	@echo "  make migrate      Apply backend database migrations"
	@echo "  make test         Run the test suite in the api container (Python 3.12)"
	@echo "  make test-local   Run the test suite on the host interpreter"
	@echo "  make lint         Run lint checks in the api container"
	@echo "  make check-secrets Scan tracked files for committed secrets"
	@echo "  make docs         Compile LaTeX docs"
	@echo "  make zip          Create source archive"

build:
	docker compose build

up:
	docker compose up -d --build

down:
	docker compose down -v

dev-up:
	docker compose up -d postgres redis

dev-down:
	docker compose down

backend-dev:
	cd backend && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

agent-dev:
	cd agent && python -m paperracks_agent.cli --help

frontend-dev:
	docker compose up frontend

frontend-build:
	docker compose run --rm --no-deps frontend npm run build

migrate:
	alembic -c backend/alembic.ini upgrade head

# Default test target runs in the container so it does not depend on the host
# interpreter/deps. Tests use SQLite, so no database service is required.
test: docker-test

docker-test:
	docker compose run --rm --no-deps api pytest

test-local:
	pytest backend/tests agent/tests

lint:
	docker compose run --rm --no-deps api ruff check backend agent scripts

check-secrets:
	python scripts/check_secrets.py --all

docs:
	cd docs && ./compile_docs.sh

zip:
	python scripts/create_source_archive.py

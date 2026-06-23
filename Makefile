.PHONY: help dev-up dev-down backend-dev agent-dev migrate test lint docs zip

help:
	@echo "PaperRacks developer commands"
	@echo "  make dev-up       Start local infrastructure"
	@echo "  make dev-down     Stop local infrastructure"
	@echo "  make backend-dev  Run backend dev server"
	@echo "  make agent-dev    Run agent CLI help"
	@echo "  make migrate      Apply backend database migrations"
	@echo "  make test         Run backend and agent tests"
	@echo "  make lint         Run lint checks"
	@echo "  make docs         Compile LaTeX docs"
	@echo "  make zip          Create source archive"

dev-up:
	docker compose up -d postgres redis grobid

dev-down:
	docker compose down

backend-dev:
	cd backend && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

agent-dev:
	cd agent && python -m paperracks_agent.cli --help

migrate:
	alembic -c backend/alembic.ini upgrade head

test:
	pytest backend/tests agent/tests

lint:
	ruff check backend agent scripts

docs:
	cd docs && ./compile_docs.sh

zip:
	python scripts/create_source_archive.py

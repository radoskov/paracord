# Changelog

All notable changes to PaperRacks should be documented in this file.

The format follows Keep a Changelog style conventions, but the project is currently pre-release.

## [Unreleased]

### Added

- Started the M1 core-library backend slice: added `sources`, `import_batches`,
  `shelf_works`, `rack_shelves`, and `tag_links` models plus an Alembic migration for the
  core file/work/source/organization tables.
- Added alias-only configured server-folder sources and folder imports. Imports scan a
  configured root, SHA-256 hash PDFs, create File/Location/Work/FileWorkLink rows, extract a
  PyMuPDF first-page text preview when available, deduplicate by file hash, and audit-log
  source creation/import completion.
- Added basic backend endpoints for sources, folder import batches, file metadata, manual
  work create/edit/search, shelves, racks, memberships, and tags.
- Added focused M1 service tests for configured-root alias handling, server-folder import
  persistence, deduplication, and audit logging.
- Added a Compose-managed frontend service (`frontend/Dockerfile`) with Docker-contained
  Node dependencies, `make frontend-dev`, `make frontend-build`, and runbook notes.
- Added the initial M1 Svelte workspace: login, library table, reading queue, source import
  controls, manual work creation, shelf/rack/tag controls, and file preview list.
- Added backend read endpoints for file listing, shelf works, and rack shelves to support
  the M1 frontend views.
- Added work search filters for shelf, rack, and tag membership and exposed them in the
  frontend library toolbar.
- Added a containerized development & evaluation stack: `backend/Dockerfile` (api server) and `agent/Dockerfile` (client), `docker compose` services for `postgres`/`redis`/`api`/`agent` (with healthchecks, a smart entrypoint that runs migrations only for the server, and opt-in `extraction`/`ai` profiles for GROBID/Ollama), `backend/requirements-dev.txt`, a `ci` GitHub Actions workflow (lint + test on Python 3.12), `make` targets (`build`/`up`/`down`/`test`/`lint`), and `docs/runbooks/dev_containers.md`. The full test suite (23 tests) and a live auth/role smoke test now pass in-container against real Postgres.
- Added role-based authorization (`require_roles` / `require_owner` dependencies) and owner-only admin endpoints under `/api/v1/admin`: list/create users, change a user's role, disable a user (with last-active-owner protection), and paginated audit-event access. New `user.created` (admin API), `user.role_changed`, and `user.disabled` audit events.
- Added an account-enumeration mitigation to login (constant-time bcrypt verification on the unknown/disabled-user path) and a startup assertion that no guest role is present in `security.allowed_roles`.
- Added a reusable FastAPI current-user dependency for bearer-token authentication.
- Protected all non-health, non-login API routers with the authentication dependency.
- Added API dependency tests for valid, missing, and invalid bearer tokens.
- Added revocable server-side bearer sessions for login/logout.
- Added persisted audit events for login success, login failure, and logout.
- Added password-reset session revocation for server-console credential recovery.
- Added authentication service tests for credential validation, token hashing, revocation, and audit persistence.
- Added Alembic configuration and the initial `users`/`audit_events` migration.
- Added a `user_sessions` migration for revocable tokens.
- Added `make migrate` for applying backend database migrations.
- Added server-console admin script tests using an isolated SQLite database.
- Added backend YAML settings loading with environment-variable override precedence.
- Added bcrypt password hashing and verification helpers.
- Added DB-backed server-console owner bootstrap and password-reset script skeletons with audit events.
- Added backend tests for settings loading and security helper behavior.

### Changed

- Bumped the target runtime to Python 3.12 (`pyproject.toml`, Dockerfiles, CI) and the Postgres image to `pgvector/pgvector:pg17`. `make test` now runs in the api container by default (`make test-local` runs on the host).
- Reconciled `SPECIFICATION.md` with the implemented scaffold: roles are `owner | editor | reader` (was `owner | member`), the repository-layout and per-agent work split now defer to `WORK_SPLIT.md` (A–J) and the actual `backend/ frontend/ agent/` layout, config examples use port 8000 and bcrypt, and the milestone plan was re-ordered to front-load the single-machine loop (the local agent moved to M5).
- Rewrote `ROADMAP.md` as a condensed mirror of the canonical `SPECIFICATION.md` §20 milestones and updated `PROGRESS.md`'s next-milestone section accordingly.
- Integrated supporting open-source tools into the spec: PyMuPDF (fast preview), YAKE/KeyBERT (keywords), OCRmyPDF/Tesseract (OCR fallback), anystyle/refextract (reference fallback), biblio-glutton (local consolidation), Nougat/Marker (optional ML extraction), and Zotero translation-server (URL metadata).
- Added usability features to the spec: reading queue, related-papers suggestions, live shelf/rack bibliography, and annotation/note full-text search.
- Made topic modeling and body summaries tiered (lightweight default, heavier opt-in): BERTopic is now optional and off by default with lightweight keyword extraction as the default; paper body summaries are Tier 0 abstract → Tier 1 extractive Method/Experiment/Results (sumy/TextRank, no LLM) → Tier 2 opt-in local-LLM abstractive (Ollama). Reflected in `config/server.example.yaml`.

### Fixed

- Fixed invalid `docker-compose.yml` YAML (the `${VAR:?…}` default messages contained an unquoted colon — "mapping value is not allowed in this context"); the guarded values are now quoted.
- Replaced unmaintained `passlib` with the maintained `bcrypt` library in `core/security.py` — `passlib` 1.7.x raised `AttributeError: module 'bcrypt' has no attribute '__about__'` against modern bcrypt, breaking all password hashing. Added an explicit 72-byte length guard.
- Fixed Alembic revision ids that exceeded the 32-char `alembic_version` column (migrations failed with `value too long for type character varying(32)` on a real Postgres).
- Made `test_config` hermetic (it now clears ambient settings env vars, so it passes inside the api container where `DATABASE_URL` is set).
- Registered the `app.models.ai` models (`Summary`, `TopicAssignment`) in `models/__init__.py` so the `summaries`/`topic_assignments` tables are no longer silently omitted from `Base.metadata` (Alembic autogenerate and `create_all`).
- Fixed `make test` collection: switched pytest to `--import-mode=importlib` and added the repo root to `pythonpath` so the two `test_security.py` modules (backend + agent) coexist and `scripts` is importable; added `scripts/__init__.py`.
- Made the `docker-compose.yml` Postgres credentials fail fast with a clear message (`${VAR:?…}`) when `.env` is missing, instead of silently breaking `make dev-up`.
- Refreshed `FILE_TREE.md` (secrets-policy files, CI workflow, new scripts) and annotated the not-yet-created owned paths in `WORK_SPLIT.md`.
- Improved `scripts/check_secrets.py`: in source files only quoted string literals are flagged (unquoted code references like `password=payload.password` no longer false-positive), config-style files still flag unquoted values, and prefixed key names (`DB_PASSWORD`, `access_token`, …) are now detected.

### Security

- Added a "Data egress and privacy" section to `SECURITY.md` and `SPECIFICATION.md` (§7.8): only opt-in, audit-logged bibliographic identifiers ever leave the machine; no PDF contents, collection structure, filesystem paths, or bulk exports are transmitted.
- Kept credential recovery as a server-console operation only.
- Owner bootstrap now refuses to create a second owner account.
- Added an authoritative secrets-and-credential-handling policy (`docs/runbooks/secrets_management.md`) and wired it into `SECURITY.md`, `AGENTS.md`, `HINTS_FOR_AGENTS.md`, `CONTRIBUTING.md`, the README, and the LaTeX security chapter.
- Added `scripts/check_secrets.py`, a dependency-free secret scanner, with `make check-secrets`, a pre-commit configuration, a plain git-hook installer (`scripts/install_git_hooks.sh`), and a `secret-scan` GitHub Actions workflow.
- Hardened `.gitignore` to exclude key material (`*.pem`, `*.key`, `secrets/`, token files) and any `.env.*` except `.env.example`.
- Removed the hardcoded Postgres dev password from `docker-compose.yml`; credentials now come from `.env`.

## [0.0.0] - 2026-06-23

### Added

- Created initial repository scaffold.
- Added FastAPI backend directory layout.
- Added local workstation agent directory layout.
- Added web frontend directory layout.
- Added Docker Compose development skeleton.
- Added configuration examples for server and agent.
- Added project progress report, agent guide, work split, and implementation hints.
- Added LaTeX documentation source tree and compile script.
- Added server-local credential recovery design and placeholder script.
- Added GROBID, PostgreSQL, Redis, pgvector, PDF.js, BERTopic, and local LLM integration placeholders.

### Security

- No guest role is defined.
- All filesystem access is routed through configured roots, managed library storage, or local agent file IDs.
- Credential recovery is specified as a server-console operation only.

### Known incomplete areas

- Database migrations are placeholders.
- API endpoints are skeletal.
- Frontend components are placeholders.
- GROBID parsing, citation graph construction, export rendering, topic modeling, and summarization are not implemented yet.

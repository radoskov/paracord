# Changelog

All notable changes to PaperRacks should be documented in this file.

The format follows Keep a Changelog style conventions, but the project is currently pre-release.

## [Unreleased]

### Added

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

### Security

- Kept credential recovery as a server-console operation only.
- Owner bootstrap now refuses to create a second owner account.

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

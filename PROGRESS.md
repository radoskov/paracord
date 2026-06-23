# Progress Report

## Current status

The project is at scaffold stage. The repository contains the planned module layout, design documentation, API surface placeholders, background worker placeholders, configuration examples, and agent work packages.

## Completed

- Product requirements consolidated into a full implementation specification.
- Server/agent architecture selected.
- No-guest access-control requirement captured.
- Server-local credential recovery requirement captured.
- Teleport workflow captured.
- Work/version/file/file-segment model captured.
- Citation context and local citation graph requirements captured.
- Citation export requirements captured.
- Local AI summary and topic modeling requirements captured.
- Initial backend, agent, frontend, documentation, and operations folder structure created.
- Backend settings now load supported values from server YAML with environment overrides.
- Backend password hashing and verification helpers are implemented with bcrypt.
- Server-console owner bootstrap and password reset scripts now touch the database and write audit events.
- Initial Alembic migration creates `users` and `audit_events`.
- Second Alembic migration creates revocable `user_sessions`.
- `make migrate` applies backend migrations.
- Backend unit tests cover settings loading and security helpers.
- Server-console admin script tests cover first-owner creation, duplicate-owner refusal, and password reset audit logging.
- Minimal login/logout endpoints create and revoke server-side bearer sessions.
- Password reset now revokes active sessions for the target account.
- Auth service tests cover credential validation, token hashing, session revocation, and audit persistence.

## In progress

- Backend API design stubs.
- Local agent protocol stubs.
- LaTeX implementation manual draft.
- Agent task partitioning.
- M0 developer skeleton hardening.

## Not started

- Full authentication hardening, authorization dependencies, and rate limiting.
- File-root scanner implementation.
- Agent registration and token rotation implementation.
- GROBID TEI parser implementation.
- Duplicate/version detection implementation.
- Citation graph materialization implementation.
- PDF.js integration.
- Export renderer.
- BERTopic and embedding pipeline.
- Local LLM summarization pipeline.
- Audit-log storage and admin views.
- End-to-end tests.

## Next milestone: M0 developer skeleton

Acceptance criteria:

1. `docker compose up` starts PostgreSQL, Redis, GROBID, and the backend service.
2. Backend serves `GET /api/v1/health`.
3. Server can create the first admin account through a server-console command.
4. The project can run tests with `make test`.
5. LaTeX docs compile with `docs/compile_docs.sh` on a machine with TeX installed.

Progress notes:

- `GET /api/v1/health` exists and has a test.
- Server-console admin scripts are DB-backed for users/audit events and password reset revokes active sessions.
- Alembic is initialized for the first security tables and sessions; broader domain models still need migrations.
- `python -m compileall backend/app backend/alembic scripts` passes in the current environment.
- `make test` is currently blocked locally because the active interpreter is Python 3.9.18 while the project requires Python 3.11+, and backend dependencies such as FastAPI and pydantic-settings are not installed.
- `ruff check backend agent scripts` is currently blocked locally because Ruff is not installed.

## Next milestone: M1 single-file import

Acceptance criteria:

1. Admin user can log in.
2. Agent can register with the server using a bootstrap token.
3. Agent can scan a configured folder and send a manifest.
4. Server can import one PDF by file ID.
5. Server can teleport one PDF into managed storage.
6. Backend queues GROBID extraction for that PDF.
7. Work, version, file, and location records are created.
8. Import activity is audit logged.

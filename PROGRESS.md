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
- Non-health, non-login API routers now require bearer-token authentication.
- Password reset now revokes active sessions for the target account.
- Auth service tests cover credential validation, token hashing, session revocation, and audit persistence.
- API dependency tests cover valid, missing, and invalid bearer tokens.
- Secrets-handling policy documented and enforced via a secret scanner, pre-commit hook, and CI workflow; hardcoded Postgres dev password removed from compose in favor of `.env`.
- Role-based authorization: `require_roles`/`require_owner` dependencies and owner-only admin endpoints for user management (list/create/role-change/disable) and audit-event access, with `user.created`/`user.role_changed`/`user.disabled` audit events and last-owner protection.
- Login account-enumeration mitigation (constant-time dummy verification on the no-user path) and a startup assertion that no guest role is configured.

## In progress

- Backend API design stubs.
- Local agent protocol stubs.
- LaTeX implementation manual draft.
- Agent task partitioning.
- M0 developer skeleton hardening.

## Not started

- Login rate limiting / failed-login lockout (role-based authorization is now implemented).
- In-app password-change endpoint (server-console reset exists; web change-password + its session revocation still pending).
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

## Tech debt and cleanups

Low-severity items found during the audit, not tied to a feature milestone. Address opportunistically.

- Remove or fully wire the dead `guest_access_enabled` setting (`backend/app/core/config.py`). A startup `assert_no_guest_roles` check now enforces that no guest role is present in `security.allowed_roles`.
- Migrate deprecated `datetime.utcnow()` to `datetime.now(timezone.utc)` across `services/auth.py`, `models/*`, `services/users.py`, and `scripts/reset_admin_password.py`.
- Guard bcrypt's silent 72-byte password truncation in `core/security.py` (length check or SHA-256 pre-hash).
- Add symlink-escape and `../` traversal test cases to `agent/tests/test_security.py` (the primitive is correct but currently untested).
- Remove or wire the unused agent config flags `follow_symlinks` / `teleport_enabled`.
- Note in `docs/architecture/api_surface.md` and `data_model.md` that they reflect current stubs and defer to `SPECIFICATION.md` §10 / §9.
- Relabel the `SPECIFICATION.md` front-matter Contents as a thematic overview (its numbers do not match the section numbers).

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

## Next milestone: M1 core library, organization, and files

See `ROADMAP.md` / `SPECIFICATION.md` §20 for the full plan. The local agent and teleport
moved to M5; M1 now delivers the single-machine value loop via server-folder import.

Acceptance criteria:

1. Admin user can log in.
2. A server-folder source can be added and scanned (single-machine mode, no agent required).
3. A folder of PDFs imports as file/work records with a PyMuPDF first-page preview.
4. Works can be created/edited and added to multiple shelves; shelves to multiple racks.
5. Works, shelves, and racks can be tagged.
6. Basic metadata search and filters work; library, shelf/rack, file, and reading-queue views render.
7. No arbitrary path endpoint exists.
8. Import activity is audit logged.

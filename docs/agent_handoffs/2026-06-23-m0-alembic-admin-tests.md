# Handoff: M0 Alembic security tables and admin tests

## Files changed

- `backend/alembic.ini`
- `backend/alembic/README.md`
- `backend/alembic/env.py`
- `backend/alembic/script.py.mako`
- `backend/alembic/versions/0001_create_users_and_audit_events.py`
- `backend/app/models/audit.py`
- `backend/app/models/user.py`
- `backend/tests/test_admin_scripts.py`
- `Makefile`
- `docs/runbooks/development_setup.md`
- `CHANGELOG.md`
- `PROGRESS.md`
- `ROADMAP.md`
- `FILE_TREE.md`

## Assumptions made

- The first migration should cover only the tables used by the current server-console scripts:
  `users` and `audit_events`.
- Production remains PostgreSQL-first, but the active security models can use SQLAlchemy portable
  `Uuid` and `JSON` types to support lightweight script tests.
- Full session revocation remains pending until a session/token persistence model is added.

## Tests added or skipped

- Added SQLite-backed admin script tests for owner bootstrap, duplicate-owner refusal, and password
  reset audit logging.
- Ran `python -m compileall backend/app backend/alembic scripts`; it passed.
- Ran `make test`; it is blocked in the current environment by Python 3.9.18, missing FastAPI,
  missing pydantic-settings, and the project requirement for Python 3.11+.
- Ran `ruff check backend agent scripts`; it is blocked because Ruff is not installed.
- Skipped a live PostgreSQL Alembic upgrade test because local infrastructure and Python
  dependencies are not available in the current environment.

## Security implications

- No web credential-recovery endpoint was added.
- The migration establishes the `users` and `audit_events` persistence surface needed for owner
  bootstrap and local password reset auditing.
- Existing no-guest role constraints remain unchanged.

## Next recommended task

- Add a session/token persistence model and implement authenticated login/logout with audit events,
  then update password reset to revoke active sessions.

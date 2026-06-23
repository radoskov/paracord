# Handoff: M0 config and server-console security helpers

## Files changed

- `backend/app/core/config.py`
- `backend/app/core/security.py`
- `backend/tests/test_config.py`
- `backend/tests/test_security.py`
- `scripts/bootstrap_admin.py`
- `scripts/reset_admin_password.py`
- `backend/README.md`
- `docs/runbooks/credential_recovery.md`
- `CHANGELOG.md`
- `PROGRESS.md`
- `ROADMAP.md`
- `FILE_TREE.md`

## Assumptions made

- Bcrypt through Passlib is acceptable for the MVP password hasher because the specification allows bcrypt or Argon2id.
- The first server-console admin scripts may use SQLAlchemy `create_all` for the existing `users` and `audit_events` tables until Alembic is initialized.
- Session revocation is documented as pending because no session/token persistence model exists yet.

## Tests added or skipped

- Added backend unit tests for YAML settings loading, environment override precedence, password hashing, and guest-role rejection.
- Skipped database-backed script integration tests because the migration/test database harness is not yet present.
- Ran `python -m compileall backend/app scripts`; it passed.
- Ran `make test`; it is blocked in the current environment by Python 3.9.18, missing FastAPI, missing pydantic-settings, and the project requirement for Python 3.11+.
- Ran `ruff check backend agent scripts`; it is blocked because Ruff is not installed.

## Security implications

- No guest or anonymous role was added.
- No web password-reset endpoint was added.
- Owner bootstrap refuses to create a second owner.
- Password reset records an `auth.password_reset_cli` audit event, but actual session invalidation still needs the future session table.

## Next recommended task

- Initialize Alembic and add a migration/test database harness for `users` and `audit_events`, then cover `bootstrap_admin.py` and `reset_admin_password.py` with integration tests.

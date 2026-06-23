# Handoff: M0 protect API stubs

## Files changed

- `backend/app/api/deps.py`
- `backend/app/api/v1/router.py`
- `backend/tests/test_api_deps.py`
- `CHANGELOG.md`
- `PROGRESS.md`
- `ROADMAP.md`
- `FILE_TREE.md`

## Assumptions made

- Health and login remain public.
- Logout keeps its endpoint-local token handling so it can revoke the current bearer token.
- Agent-specific token authentication will likely replace or supplement user auth for agent routes
  later, but the current stubs should not be anonymously reachable.

## Tests added or skipped

- Added dependency tests for valid, missing, and invalid bearer tokens.
- Ran `python -m compileall backend/app backend/alembic scripts`; it passed.
- Ran `make test`; it is blocked in the current environment by Python 3.9.18, missing FastAPI,
  missing pydantic-settings, and the project requirement for Python 3.11+.
- Ran `ruff check backend agent scripts`; it is blocked because Ruff is not installed.
- Full route-level tests were deferred until the Python 3.11 dependency environment is available.

## Security implications

- Non-health, non-login API routers now require a valid, active bearer token.
- The dependency rejects revoked sessions, expired sessions, disabled users, and missing users.
- No guest or anonymous library/content route was added.

## Next recommended task

- Add role/permission checks for the `owner | editor | reader` roles (owner-only operations
  first), starting with admin and agent enrollment flows.

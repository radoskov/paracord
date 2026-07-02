# Handoff: D1 overload protection + shared throttle (2026-07-02)

## Task name
D1 — shared, fail-open overload protection: Redis login throttle, request rate limiting, client
import-batch cap (+ agent chunking), and an admin-configurable RQ worker supervisor.

## Commits (all on `main`)
1. `backend: move login throttle to Redis sliding window with in-process fail-open`
2. `backend: add Redis-backed request rate limiting with admin-editable ceilings`
3. `backend,agent: cap client import batches with admin-editable limit and agent chunking`
4. `backend: add RQ worker supervisor with admin-configurable worker count`
5. `frontend: expose rate-limit, batch-cap and worker-count knobs in admin settings`
6. (docs) this handoff + runbook + PROGRESS

## Files changed

**Slice 1 — login throttle → Redis (fail-open):**
- `backend/app/services/login_throttle.py` — same public API (`lock_state`/`record_failure`/
  `clear`/`reset_all`) + injectable clock, now backed by a Redis sorted set (`paracord:login-throttle:{key}`,
  score = failure timestamp). `_redis()` pings a cached client; on any failure every op falls back
  to the original in-process dict. `reset_all` clears both stores.
- `backend/tests/test_login_throttle.py` — NEW; fake in-memory Redis drives the Redis path + a
  fail-open test (fakeredis is NOT installed, so tests monkeypatch `_redis`).

**Slice 2 — request rate limiting:**
- `backend/app/models/app_config.py` — `rate_limit_per_client_per_min` (60), `rate_limit_global_per_min`
  (300) columns + default constants.
- `backend/alembic/versions/0043_rate_limit_config.py` — NEW migration.
- `backend/app/services/app_config.py` — `effective_rate_limit_*` getters + `update_rate_limits`;
  refactored `_ensure_row`.
- `backend/app/services/rate_limit.py` — NEW. Redis fixed-window counter (per-minute key), per-client
  key = `client:tok:<sha256(token)[:16]>` when a bearer token is present else `client:ip:<ip>`, plus
  one `global` key. Increments per-client first (a throttled client never inflates the global
  window). Fails open when Redis is down or errors. `_effective_limits()` reads the ceilings via
  `SessionLocal` with a 5s in-process cache, and is only reached when Redis is up (so unit tests
  without Redis never touch the DB). `is_exempt()` covers health/docs/schema.
- `backend/app/main.py` — registers the `@app.middleware("http")` limiter + the `BatchTooLargeError`
  → 413 exception handler.
- `backend/app/api/v1/endpoints/admin.py` — `AppConfigOut`/`AppConfigUpdate` gained the new fields
  (all update fields optional → partial PATCH); `_app_config_out` helper; PATCH applies only supplied
  fields and calls `rate_limit.reset_cache()`.
- `backend/tests/conftest.py` — autouse `_rate_limit_fail_open` fixture forces `_redis()`→None for
  the whole API suite (the dev-stack Redis IS reachable from the test container, so without this the
  shared counter would couple tests / trip the global cap). This mirrors the D1 assumption that unit
  tests run without Redis. **Dedicated limiter tests re-monkeypatch `_redis` to a fake to exercise
  the enforced path.**
- `backend/tests/test_rate_limit.py` — NEW; per-client 429, global 429, Retry-After, window rollover,
  fail-open (Redis down + client raises), middleware 429, health exempt.

**Slice 3 — batch item cap + agent chunking:**
- `backend/app/models/app_config.py` — `max_batch_items` (100) column.
- `backend/alembic/versions/0044_max_batch_items.py` — NEW migration.
- `backend/app/services/app_config.py` — `BatchTooLargeError`, `effective_max_batch_items`,
  `enforce_batch_limit`, `update_max_batch_items`.
- Enforcement (`enforce_batch_limit` at the top of each): `services/bibtex.py` (`import_bibtex`),
  `services/bibliography_import.py` (`import_records` → RIS/CSL), `services/batch_import.py`
  (`commit_drafts`), `services/agent_files.py` (`ingest_manifest`). Server-folder scan
  (`import_folder`) is intentionally NOT capped.
- `backend/app/api/v1/endpoints/agents.py` — `GET /agents/me` now returns `max_batch_items` so the
  agent can chunk.
- `agent/paperracks_agent/agent_ops.py` — `sync` fetches the cap via `client.get_me()`
  (`_resolve_batch_cap`, defaults to `DEFAULT_MAX_BATCH_ITEMS=100` on any error) and sends the
  manifest in ≤cap chunks (`_send_manifest_chunked`; an empty scan still sends one empty manifest).
- `agent/tests/test_agent_ops.py` — `FakeClient` gained `get_me` + a `test_sync_chunks_oversized_manifest`.
- `backend/tests/test_batch_cap.py` — NEW; config round-trip, bibtex 413 over cap, 201 at cap,
  `/agents/me` reports the cap via the real enroll→approve flow.

**Slice 4 — RQ worker supervisor:**
- `backend/app/models/app_config.py` — `rq_worker_count` (2) column.
- `backend/alembic/versions/0045_rq_worker_count.py` — NEW migration.
- `backend/app/services/app_config.py` — `effective_rq_worker_count`, `update_rq_worker_count`.
- `backend/app/workers/supervisor.py` — NEW. `resolve_worker_count()` reads the effective count once
  (falls back to default on DB error, clamps ≥1). `_Supervisor` spawns N `rq worker … paracord`
  children, restarts a dead child, and on SIGTERM/SIGINT terminates children (SIGKILL after a 10s
  grace). `main()` is the container entrypoint.
- `docker-compose.yml` — worker `command` → `["python","-m","app.workers.supervisor"]`. The prod
  overlay defines no worker command, so it inherits this automatically.
- `backend/tests/test_worker_supervisor.py` — NEW; count resolution (config / clamp / DB-error
  fallback), worker command shape, spawn count, shutdown terminate + kill-on-timeout. Does NOT spawn
  real rq.

**Frontend + docs:**
- `frontend/src/api/client.ts` — `AppConfig` interface + `updateAppConfig(Partial<AppConfig>)`.
- `frontend/src/pages/AdminPage.svelte` — Settings tab: an "Overload protection" section with the
  two rate limits, `max_batch_items` ("Max papers per import batch"), and `rq_worker_count`
  ("Background worker processes", with a **"Restart the worker container to apply"** note).
- `frontend/src/pages/AdminPage.test.ts` — full-config mock + a new overload-save test (two Save
  buttons now, disambiguated with `getAllByRole`).
- `docs/runbooks/dev_containers.md` — "Overload protection knobs (D1)" section.
- `PROGRESS.md` — D1 entry.

## Fail-open design
- **Login throttle & rate limiter**: `_redis()` returns `None` on client-build failure or a failed
  `ping()`; the throttle then uses the in-process dict, and the limiter allows the request. Any
  exception mid-operation is caught and degrades the same way. A dead Redis therefore never locks
  users out and never blocks the API.
- **Rate-limit ceilings**: read from the DB only when Redis is already reachable, behind a 5s cache,
  so a healthy path adds no per-request DB hit and the unit-test (no-Redis) path never touches the DB.

## How the agent learns + applies the cap
`GET /agents/me` includes `max_batch_items`. `agent_ops.sync` calls `client.get_me()` once per
sync, defaults to 100 on any error/absent field, and splits the manifest into ≤cap chunks sent
sequentially. Server-side `ingest_manifest` still enforces the cap as defense-in-depth.

## Supervisor design
Apply-on-restart, no live polling. Reads `rq_worker_count` once at start (DB-error → default, clamp
≥1), spawns that many children, self-heals a crashed child, and shuts down cleanly on SIGTERM.

## Verification
- Backend fast tier: `docker compose exec -T api python -m pytest backend/tests -q -m "not slow"`.
- Migration parity: `make test-migrations` — 4 passed (new columns present).
- Agent: `make test-agent` — green incl. chunking test.
- Frontend: `make frontend-check` — 82 passed + build.
- `ruff check backend agent && ruff format --check backend agent` clean.

## Notes / deviations
- `backend/openapi.json` is now stale (app-config schema grew 4 fields). `openapi-check` is NOT in
  the D1 verify list, but run `make openapi` + commit if CI enforces it.
- Per-client rate-limit key uses the bearer-token hash (not a resolved user id) to avoid a DB/auth
  lookup in middleware; distinct sessions of one user get distinct buckets, which is acceptable for
  overload protection at this scale. Unauthenticated requests key by IP.
- `docs/AUDIT.md` and `docs/DISCUSSIONS.md` were left untouched (parent reconciles them).

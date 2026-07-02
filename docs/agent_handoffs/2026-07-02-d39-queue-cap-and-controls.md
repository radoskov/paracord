# Handoff: D39 queue-length cap + admin queue/worker controls (2026-07-02)

## Task name
D39 — pending-queue depth cap (fail-open guard on every job-creating request) + admin clear-queue
and reset-stuck-jobs controls, extending D1's overload protection.

## Commits (all on `main`)
1. `backend: reject new jobs when the queue is at capacity`
2. `backend: admin clear-queue and reset-stuck-jobs controls`
3. `frontend: queue-full toast + admin clear/reset buttons + max_queue_len setting`
4. `tests: queue cap, admin controls, config round-trip`
5. (docs) this handoff + PROGRESS

## Files changed

**Slice 1 — capacity guard:**
- `backend/app/models/app_config.py` — `max_queue_len` column (default 1000) + `_DEFAULT_MAX_QUEUE_LEN`.
- `backend/alembic/versions/0046_max_queue_len.py` — NEW migration (revises `0045_rq_worker_count`).
- `backend/app/services/app_config.py` — `effective_max_queue_len` getter + `update_max_queue_len`.
- `backend/app/workers/queue.py` — NEW `pending_queue_depth()` (returns the queued count, or `None`
  when Redis is unreachable; never raises).
- `backend/app/services/queue_capacity.py` — NEW. `assert_queue_has_capacity(db)`: measures the
  pending depth, rejects with 429 at/over `max_queue_len`, no-ops (allows) when the depth is `None`.
- Guard call added at the start of each job-creating endpoint: `imports.py` (folder, upload,
  identifier, bibtex, ris, csl), `files.py` (`/files/{id}/extract` incl. force-OCR), `works.py`
  (`/works/{id}/files` attach, `/works/{id}/extract` re-extract), `search.py` (`/search/reindex`),
  `agents.py` (`/agents/teleports/{id}/content`, `/agents/files/{id}/extract` — after the privilege
  check, before reading the uploaded bytes).
- `backend/app/api/v1/endpoints/admin.py` — `AppConfigOut`/`AppConfigUpdate` + `_app_config_out` +
  PATCH handler gained `max_queue_len`.
- `backend/tests/conftest.py` — autouse `_queue_capacity_fail_open` fixture forces
  `queue.pending_queue_depth`→`None` for the whole API suite (mirrors `_rate_limit_fail_open`), so
  the guard is a no-op in unit tests and decoupled from any live queue state. Dedicated cap tests
  re-monkeypatch the depth to exercise the enforced path.

**Slice 2 — admin controls:**
- `backend/app/workers/queue.py` — NEW `empty_queue()` (drops pending jobs, returns count),
  `recover_stuck_jobs()` (requeues StartedJobRegistry, clears FailedJobRegistry), and the
  `WORKER_PROCESS_RESET_HINT` constant. Both never raise; report `available: False` when Redis is down.
- `backend/app/api/v1/endpoints/jobs.py` — NEW admin-only `POST /jobs/clear-queue` and
  `POST /jobs/reset-workers`; each records an audit event (`queue.cleared`, `queue.workers_reset`)
  and commits.

**Slice 3 — frontend:**
- `frontend/src/api/client.ts` — `AppConfig.max_queue_len`; `onQueueFull` constructor callback
  (threaded through `withToken`); request wrapper fires it on a 429/503 whose detail matches
  `/queue is full/i` (so it does NOT fire on the rate-limit 429); `clearQueue()`/`resetWorkers()`.
- `frontend/src/App.svelte` — `onQueueFull` handler + a fixed-position dismissible "queue full"
  toast (auto-dismiss 8s), wired into the shared `ApiClient`.
- `frontend/src/pages/JobsPage.svelte` — admin-only (`$canManageUsers`) Clear queue / Reset workers
  buttons with confirm + result message (set *after* `refresh()`, which clears `message` on success).
- `frontend/src/pages/AdminPage.svelte` — "Max pending jobs in the queue" field in the Overload
  protection section, saved via the existing app-config PATCH.

**Tests:**
- `backend/tests/test_queue_cap.py` — NEW; config round-trip, guard reject-at-cap + fail-open
  (service and HTTP layers, incl. a real dead-Redis fail-open), admin clear-queue/reset-workers
  auth (403 non-admin) + audit + graceful-when-Redis-down, and the queue helpers' unavailable shape.
- `frontend/src/pages/JobsPage.test.ts` — admin controls hidden for non-admin, clear/reset confirm
  flow + result text + restart hint, cancel path.
- `frontend/src/api/client.additional.test.ts` — `onQueueFull` fires on queue-full 429 but not on a
  rate-limit 429; `clearQueue`/`resetWorkers` hit the right endpoints.
- `frontend/src/pages/AdminPage.test.ts` — config mocks + overload-save assertion gained
  `max_queue_len`.

## Assumptions made
- `/search/reindex` is synchronous today but was in the task's guard list, so it is guarded too
  (harmless; a full queue means the box is already loaded).
- Bibtex/RIS/CSL/identifier imports are metadata-only (no extraction job) but were explicitly listed,
  so they carry the guard for uniform behaviour and future-proofing.
- Owner+admin is the correct gate for the recovery controls (`require_admin` server-side,
  `$canManageUsers` client-side), matching the other queue/admin actions.

## How the guard fails open
`pending_queue_depth()` pings Redis and returns the queued count, or `None` on any exception (dead
Redis, build failure). `assert_queue_has_capacity` returns immediately (allows) when the depth is
`None`, so an unmeasurable queue never blocks a request. Unit tests run with the autouse fixture
forcing `None`, so the whole suite stays green without Redis; the dedicated tests monkeypatch a
large depth to prove the 429 and restore the real measurement against a closed port to prove the
fail-open HTTP path.

## How "reset workers" recovers stuck jobs given the process/container boundary
The API cannot restart the RQ worker *processes* — they run under the supervisor (D1) in the worker
container. `recover_stuck_jobs()` instead fixes the queue *state* in Redis: it requeues every job
stranded in the StartedJobRegistry (its worker died mid-job) so it runs again, and clears the
FailedJobRegistry noise. The endpoint response and log carry a `note` that a full process reset is
`docker compose restart worker`, which the UI surfaces in the Reset-workers confirmation + result.

## Tests added / skipped
Added (above). None skipped. Full backend suite run (not just fast tier per the task): 722 passed.

## Security implications
- Both recovery endpoints are `require_admin` (owner/admin) and audited; no new unauthenticated
  surface, no filesystem path input.
- The guard is fail-open by design (availability over strictness) — consistent with D1/D7; a full or
  unreachable queue cannot lock the instance, only slow new work.

## Verification
- Full backend suite: `docker compose exec -T api python -m pytest backend/tests -q` — **722 passed**.
- Migration parity: `make test-migrations` — **4 passed** (new column present, autogenerate-clean).
- Agent: `make test-agent` — **34 passed** (agent code untouched; guard is server-side).
- Frontend: `make frontend-check` — green + build.
- `ruff check backend agent && ruff format --check backend agent` — clean.

## Notes / deviations
- `backend/openapi.json` is now stale (app-config schema gained `max_queue_len`; two new job
  endpoints). `openapi-check` is not in the D39 verify list; run `make openapi` + commit if CI
  enforces freshness (same caveat as D1).
- The queue-full toast keys off the detail phrase `queue is full` (not status alone) so it never
  fires on D1's rate-limit 429, which shares the 429 status.
- `docs/AUDIT.md` and `docs/DISCUSSIONS.md` left untouched (parent reconciles them).

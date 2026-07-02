# Handoff: Track A audit batch — D6, D8, D9, D10, D11, D12 (2026-07-03)

## Task
Implement six Track A (correctness + security) audit fixes from `docs/AUDIT.md`. All external-service
paths must fail open / degrade gracefully; the SQLite-without-Redis unit suite must stay green.

## Commits (all on `main`, not pushed)
- `fffb378` backend: SSRF-guard admin-set ollama_url (D6)
- `4c47a7f` backend: make enrichment resilient per source (D8)
- `09596a7` backend: make folder import per-file resilient with up-front batch commit (D9)
- `299168d` backend: gate worker startup on migrations at head (D10)
- `cfda5b5` backend: idempotent default-shelf backfill on startup (D11)
- `98ce137` backend: skip dim-mismatched model in multimode clustering (D12)

## Per-item summary

### D6 — ollama_url SSRF guard
- `backend/app/core/config.py` — new `allow_external_ollama` Setting (`ALLOW_EXTERNAL_OLLAMA`, default False).
- `backend/app/services/ai_config.py` — `_ollama_host_is_local()` (loopback IP / `localhost` / bare
  single-label docker-service name) + `_validate_ollama_url()`; `_validate` now takes `settings` and
  validates `ollama_url`. Empty value clears the override (falls back to the loopback default).
  Rejects non-http(s) schemes and non-local hosts unless the opt-in is set. Surfaces as 400 via the
  existing endpoint `ValueError` handler.
- Tests: `backend/tests/test_ai_admin.py` (loopback/service accepted, external rejected, opt-in path,
  host classification).

### D8 — enrichment per-source resilience
- `backend/app/services/metadata_enrichment.py` — `enrich_work` builds a list of `(source, callable)`
  and queries each in its own try/except; a failing source is appended to a new `failed` list and
  logged, the rest still run. Return dict gains `"failed"`. Chunk/embed enqueue path untouched (D7).
- `backend/app/workers/jobs.py` — `enrich_work_job` now returns the enrich result (RQ job result) so a
  partial failure is visible; still enqueues chunk/embed in `finally`.
- Tests: `backend/tests/test_enrichment.py`.

### D9 — folder import transaction (CONTRACT CHANGE)
- `backend/app/services/storage.py::import_server_folder` — now: (1) commits the `ImportBatch` row up
  front in its own txn (survives a later crash); (2) imports each file inside `db.begin_nested()` so a
  bad file rolls back only itself and is counted in `stats["errors"]`; (3) marks owed extractions
  (D7) + finalizes status/stats in one commit at the end. A catastrophic (non-per-file) error rolls
  back, finalizes the batch as `failed` in its own commit, and re-raises.
  **Behavior change: partial imports are now visible** — a scan hitting some unreadable files commits
  the good files instead of rolling the whole batch back on the first error.
- `stats` dict gained an `"errors"` key (response shape is otherwise unchanged; `ImportBatchRead.stats`
  is a free-form `dict`, so no OpenAPI change).
- `backend/app/api/v1/endpoints/imports.py::import_folder` — dropped the now-redundant marking loop
  (moved into the service so the marker lands in the same commit as the files); still enqueues +
  reports `extraction_queued`.
- Note: the owed-marking loop is guarded by a `metadata_assertions` table-presence check so the narrow
  unit-test schema (which omits that table) still works.
- Tests: `backend/tests/test_m1_core_library.py` (bad-file isolation, up-front batch commit, updated
  exact-stats assertion), `backend/tests/test_api_flows.py` (updated stats assertion).

### D10 — worker waits for migrations
- `backend/app/workers/supervisor.py` — `_alembic_script_heads()`, `_alembic_db_heads()`,
  `migrations_at_head()`, and `wait_for_migrations()` (bounded loop, default timeout
  `PARACORD_MIGRATION_WAIT_TIMEOUT=300`s; **fails open** — logs a warning and starts anyway rather than
  wedging the container on a misconfigured/unreachable DB). Called at the top of `main()` before
  spawning children. The worker container runs `python -m app.workers.supervisor`, which does NOT go
  through the entrypoint's migration step (only the api container migrates), so this gate is what
  keeps the worker off a stale schema.
- Tests: `backend/tests/test_worker_supervisor.py`.

### D11 — startup default-shelf backfill
- `backend/app/services/default_shelf.py::backfill_loose_papers_onto_default` — places every loose
  paper (on no shelf) onto the default shelf; idempotent (a paper already on any shelf is skipped),
  no-ops on empty/narrow schema, does not commit.
- `backend/app/main.py` — FastAPI `lifespan` runs it (guarded) then commits, before the existing D7
  sweep. Safe across API workers: a concurrent double-insert on the `(shelf_id, work_id)` PK surfaces
  as an IntegrityError the guard logs and rolls back.
- Tests: `backend/tests/test_default_shelf.py`.

### D12 — multimode clustering dimension safety
- `backend/app/services/topic_modeling.py::_paper_dense_vectors` — per model, enforces one fixed
  dimension (registry `column_for` dim, else the first vector's length). A per-work vector that
  doesn't match → the whole model is skipped with a logged warning (no padding/truncation). If every
  model is skipped, returns `(None, None)` so the caller falls back to the TF-IDF baseline.
- Tests: `backend/tests/test_topic_modeling.py`.

## Fail-open behavior (per external dependency)
- **D6**: no request path — pure validation; empty value degrades to the loopback default.
- **D8**: any source raising is caught and recorded; the work is still chunked/embedded.
- **D9**: enqueue is best-effort (Redis down → `extraction_queued=False`, recovery sweep retries via
  the owed marker); per-file errors are contained; batch row is durable.
- **D10**: DB unreachable → the wait loop retries, then starts anyway after the timeout.
- **D11**: backfill wrapped in try/except in the lifespan; a hiccup/race never blocks startup.
- **D12**: no external call on the guard itself; a dim mismatch degrades to TF-IDF.

## Verification
- Full backend suite: `docker compose exec -T api python -m pytest backend/tests -q` → **738 passed**.
- `ruff check backend agent` + `ruff format --check backend agent` → clean.
- `backend/openapi.json` regenerated → unchanged (no API surface change).
- No migration added/touched, so `make test-migrations` was not required.

## Notes / deviations
- None. D9 is the one intended behavior-contract change (partial-import visibility), matching the
  AUDIT entry.

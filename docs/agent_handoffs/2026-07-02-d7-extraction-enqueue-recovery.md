# Handoff: D7 extraction-enqueue visibility + self-healing recovery (2026-07-02)

## Task name
D7 — make a dropped extraction enqueue (Redis down at import time) visible everywhere and
self-healing, so imports never silently fail to extract.

## Files changed

**Backend — durable marker, deterministic id, sweep (commit `backend: durable owed-extraction …`
+ `tests: …` + `backend: shorten 0042 …`):**
- `backend/app/models/file.py` — new nullable `File.extraction_requested_at` marker.
- `backend/alembic/versions/0042_file_extraction_owed.py` — NEW migration (real downgrade). NOTE:
  revision id is `0042_file_extraction_owed` (25 chars) — the descriptive
  `..._requested_at` id was 33 chars and overflowed alembic's `version_num VARCHAR(32)` (caught by
  `test_backfill_gave_every_existing_user_a_personal_group`). Keep future ids ≤ 32 chars.
- `backend/app/workers/queue.py` — `enqueue_extraction` now uses deterministic id
  `extract-{file_id}` and a live-job guard (`_live_extraction_job_id`) so a re-enqueue of a
  queued/started file is a no-op; `queue_status` gained `redis_reachable`/`worker_count`
  (`Worker.count(queue=...)`, scoped to our queue)/`queued`, degrading to
  `redis_reachable=False` without raising.
- `backend/app/workers/jobs.py` — `extract_pdf_job` clears the marker on terminal success AND on
  terminal failure (marker means "still owed", not "ever failed").
- `backend/app/workers/recovery.py` — NEW; `owed_extraction_file_ids(db)` +
  `sweep_owed_extractions()` (idempotent, tolerates a down Redis by skipping).
- `backend/app/main.py` — FastAPI `lifespan` runs the sweep on startup (guarded).
- `backend/app/api/v1/endpoints/jobs.py` — NEW admin `POST /jobs/reprocess-pending` (runs the sweep).

**Backend — surface `extraction_queued` + set marker transactionally (commit
`backend: surface extraction_queued …`):**
- `backend/app/services/storage.py` — NEW `mark_extraction_requested(file)`.
- `backend/app/api/v1/endpoints/imports.py` — `ImportBatchRead.extraction_queued` +
  `IdentifierImportResponse.extraction_queued`; folder import and `upload_pdf` mark the file(s) in
  the same commit and report whether the enqueue succeeded.
- `backend/app/api/v1/endpoints/works.py` — `WorkFileRead.extraction_queued`; `upload_work_file`
  and the work-level re-extract trigger set the marker before commit.
- `backend/app/api/v1/endpoints/files.py` — `POST /files/{id}/extract` sets the marker + commits
  before enqueue.
- `backend/app/api/v1/endpoints/agents.py` — `upload_for_extraction`, teleport-content and
  `offer_teleport` set the marker and return `extraction_queued`.

**Agent (commit `agent: keep items retryable …`):**
- `agent/paperracks_agent/agent_ops.py` — `EXTRACT_QUEUE_FAILED` local state; on
  `extraction_queued=false` the item is NOT advanced to `extracting` and is re-attempted next sync.
- `agent/paperracks_agent/web.py` — GUI file list surfaces the local `extract_queue_failed` state.

**Frontend (commit `frontend: jobs queue-health semaphore …`):**
- `frontend/src/api/client.ts` — `extraction_queued?` on `ImportBatch`/`IdentifierImportResponse`/
  `WorkFile`; `redis_reachable?`/`worker_count?`/`queued?` on `QueueStatus`.
- `frontend/src/pages/JobsPage.svelte` — red/yellow/green semaphore + status line.
- `frontend/src/pages/ImportPage.svelte` — warning banner when `extraction_queued` is false.

**Tests:** `backend/tests/test_d7_extraction_recovery.py` (NEW, 11), `agent/tests/test_agent_ops.py`
(+1), `frontend/src/pages/JobsPage.test.ts` (+3).

## Assumptions made
- Identifier import creates a metadata-only work (no PDF), so its `extraction_queued` is always
  true (nothing to queue) — kept for a uniform response contract.
- Sweep placement is the API lifespan. Multiple API workers each run it, which is safe because the
  deterministic job id + live-job guard make re-enqueue idempotent. There is a tiny check-then-
  enqueue race under true concurrency (two processes both see "no job" and both push): acceptable
  at the documented scale (single user + a few LAN users); the test proves the sequential guarantee.
- Marker cleanup on failure is intentional: the file is left `extract_failed` and NOT re-swept
  (a retry is an explicit user re-extract). "Owed" strictly means "no terminal attempt yet".

## Invariants (with the tests that prove them)
1. **Owed vs never-requested:** `owed_extraction_file_ids` selects only rows with the marker set
   and status not `extracting`. A file exists with the marker NULL whenever nobody asked to extract
   it — e.g. `attach_uploaded_pdf_to_work` (the `PUT` attach) leaves it NULL; only the upload/
   extract paths set it. Proven by
   `test_owed_query_selects_only_marked_non_extracting_files`.
2. **No double-enqueue on a sweep/re-extract collision:** deterministic id `extract-{file_id}` +
   live-job guard. Proven by `test_double_enqueue_yields_a_single_job` (two enqueues → one queued
   job).

## Tests added or skipped
- Backend fast tier: 519 passed / 167 deselected. Migration parity (`make test-migrations`): 4
  passed (Postgres). Agent: 33 passed. Frontend: 81 passed + build green. Slow suite NOT run
  (per test tiers). The two Redis-dependent backend tests auto-skip when no Redis is reachable.

## Security implications
- No security-boundary changes. `POST /jobs/reprocess-pending` is `require_admin`. The queue-health
  endpoint still returns only aggregate counts/worker numbers (no new data exposure). The agent
  never gains new privileges; a queue failure just keeps an item retryable.

## Next recommended task
Consider surfacing the owed/pending count on the Jobs tab (e.g. "N papers awaiting extraction")
sourced from the sweep, and a yellow "queued but not draining" heuristic tracked across polls.

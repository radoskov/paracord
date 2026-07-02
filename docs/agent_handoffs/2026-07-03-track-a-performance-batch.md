# Handoff: Track A performance batch — D13, D14, D15, D19, D20, D22 (2026-07-03)

## Task
Implement six Track A (performance) audit fixes from `docs/AUDIT.md`. All external-service paths must
fail open / degrade gracefully; the SQLite-without-Redis unit suite must stay green and deterministic.
These are perf changes, not behavior changes, except D15's async contract for full-library scans.

## Commits (all on `main`, not pushed)
- `85d2ca4` backend: keep topic views read-only + numpy kNN graph (D19, D20)
- `4e99971` backend: commit HNSW provisioning before the reindex backfill loop (D22)
- `fb65689` backend: always run full-library duplicate scans in the background (D15)
- `fb56f5d` backend: batch embeddings (embed_many) + queue /search/reindex (D14)
- `f29c346` backend: rebuild BM25 index in background from chunks, serve stale meanwhile (D13)

(D19 and D20 rewrite the same `build_topic_graph` read path, so they share one commit; the openapi
`unindexed_work_count` addition landed with the D15 commit's regen.)

## Per-item summary

### D13 (HIGH) — BM25 rebuild off the read path + built from chunks
- `backend/app/services/bm25_index.py`
  - `build_index` reads body text from `work_chunks` (one bulk `SELECT work_id, section, text`,
    grouped in Python) instead of re-parsing TEI per work. Title/abstract come from the `works` row;
    chunks with section `title`/`abstract` are excluded so they aren't double-counted against the
    overlap-carrying body chunks. An un-chunked work is still indexed by its title+abstract. Removed
    the now-dead `_work_field_tokens` (and its `iter_work_sections` TEI parse).
  - Persistence key is now signature-independent (`_disk_key(db)`) so the on-disk index lives at a
    fixed path a background rebuild overwrites in place; `load_index(directory, key)` returns whatever
    is on disk tagged with its stored signature (dropped the signature-match param).
  - `get_index`: SQLite/tests build synchronously on signature change (deterministic). Postgres serves
    the persisted/last-known index; when the signature moved it enqueues a background rebuild and
    serves the stale index meanwhile (never rebuilds inline). Only a cold start (nothing built yet)
    builds synchronously. New `_enqueue_rebuild()` (best-effort) and `rebuild_persisted_index(db)`
    (the job entry point).
- `backend/app/workers/jobs.py` — new `rebuild_bm25_job()` (build + persist via a fresh session).
- `backend/app/workers/queue.py` — `BM25_REBUILD_JOB` / `BM25_REBUILD_JOB_ID = "bm25-rebuild"`,
  `enqueue_bm25_rebuild()` (coalesced via the fixed id + `_live_extraction_job_id` reuse), label added.
- Stale-serve behavior: after an edit, the next Postgres search returns the previous index and a
  background job refreshes the on-disk copy; the API process mmap-loads the fresh copy on a later
  search once the signature matches. Correctness trade-off matches the AUDIT entry.
- Tests: `test_bm25_index.py` (built-from-chunks / no TEI re-parse; Postgres serve-stale + enqueue,
  driven by monkeypatching `_is_postgres`; updated the Methods-vs-Intro test to `rechunk_work` first;
  `load_index`/prune test updated for the 2-arg signature); `test_pg_integration.py` (`_disk_key`/
  `load_index` signatures).

### D14 — batched embeddings + queued reindex
- `backend/app/services/embeddings.py` — `EMBED_BATCH_SIZE = 64`; `embed_many(provider, texts)`
  module helper (uses the provider's `embed_many` if present, else per-text `embed`); `embed_many`
  methods on HashBow (list-comp), SentenceTransformer (`.encode(list, normalize_embeddings=True)`),
  Ollama (`POST /api/embed` with list `input`; falls back to per-text `embed` on old daemons / bad
  shape).
- `backend/app/services/chunk_embeddings.py::backfill_chunk_embeddings` and
  `semantic_search.py::ensure_work_embeddings` — collect the pending texts and embed a batch per
  round-trip (order preserved); `commit_every` semantics unchanged.
- `backend/app/api/v1/endpoints/search.py::reindex_embeddings` — routes to `enqueue_reindex()`
  (returns `{status: queued, queued: true, job_id, ...}`); falls back to the synchronous in-request
  build when the queue is unavailable (`queued: false` + provider provenance). Still 200 either way.
- Tests: `test_semantic_search.py` (`embed_many` matches per-text; provider batch path called once;
  reindex enqueues when a live id is returned and does NOT build inline; the build-then-search test
  forces the sync fallback).

### D15 (CONTRACT) — full-library duplicate scan always background
- `backend/app/api/v1/endpoints/duplicates.py::scan_duplicates` — a scan with no `work_id`/`file_id`
  is always enqueued (queued shape; 503 when the queue is down), ignoring the now-deprecated
  `background` hint. Single-work/-file scans stay synchronous/inline.
- **Behavior change:** a full-library scan response is the async `{queued: true, job_id, candidates:
  [], counts: 0}` shape instead of inline candidates.
- Tests: `test_duplicates_api.py` (full scan forced to background even with `background=false`; 503
  when queue unavailable; single-work stays synchronous).

### D19 — topic views require pre-indexed vectors (read-only)
- `backend/app/services/topic_modeling.py::_paper_dense_vectors` — now returns
  `(vectors, kept_works, label, skipped)`. A chunk-column model that has no pre-indexed vector for a
  paper no longer embeds it inline on the read path: the paper is skipped and counted. Column-less
  models (SQLite / doc-level) still embed inline (unchanged; keeps the D12 tests + fake-provider
  topic tests working). `_model_topics_embedding` filters documents/TF-IDF to the surviving set and
  reports `unindexed_work_count`.
- `backend/app/services/topic_graph.py::build_topic_graph` — nodes are only the indexed papers;
  `summary.unindexed_works` (+ a note) reports the skipped count.
- `backend/app/api/v1/endpoints/ai.py::TopicModelResponse` — new `unindexed_work_count: int = 0`.
- Tests: `test_topic_graph.py` (skip + count + note); `test_topic_modeling.py` (D12 tests updated to
  the 4-tuple return).

### D20 — numpy topic-graph cosine
- `backend/app/services/topic_graph.py::_knn_edges` — normalize the paper vectors once, take
  `M @ M.T`, exclude the diagonal, keep each paper's top-k above the threshold. Stable argsort +
  `round(4)` reproduce the prior O(n²) loop's edges exactly.
- Tests: `test_topic_graph.py::test_knn_edges_numpy_matches_pure_python` compares the numpy edges to
  an inline pure-Python reference on a well-separated fixture.

### D22 — HNSW provisioning in its own short transaction
- `backend/app/workers/jobs.py::reindex_embeddings_job` — provisions the model's chunk column + HNSW
  index (`register_provider`) and **commits** before the per-chunk backfill loop, so the ALTER/CREATE
  INDEX locks aren't held for the whole job. `register` is idempotent, so the backfill's own
  provision call finds the column already present. No-op off Postgres.
- Tests: `test_jobs.py::test_reindex_job_provisions_before_backfill` (monkeypatched ordering:
  provision → commit → backfill).

## Fail-open / degradation behavior (per external dependency)
- **D13**: Redis down → `enqueue_bm25_rebuild` is a no-op and the search keeps serving the stale/
  last-known index; a cold start still builds synchronously. Persistence is best-effort.
- **D14**: Ollama `/api/embed` missing/odd → per-text `embed` fallback. Queue down → `/search/reindex`
  runs the pipeline inline. Batching never changes vectors, only the round-trip count.
- **D15**: queue down → full scan returns 503 (explicit, matches the existing queued path) rather than
  running a minutes-long request.
- **D19**: no external call on the guard; un-indexed papers are skipped, not embedded on the read path.
- **D20**: pure numpy; no external dependency.
- **D22**: no external call; off Postgres the provisioning commit is a no-op.

## Verification
- Full backend suite: `docker compose exec -T api python -m pytest backend/tests -q` → **749 passed**.
- `ruff check backend agent` + `ruff format --check backend agent` → clean.
- `backend/openapi.json` regenerated (D14 reindex `queued`/`job_id`; D15 scan description; D19
  `unindexed_work_count`) and committed; `openapi-check` current.
- No migration added/touched, so `make test-migrations` was not required.

## Notes / deviations
- D15 is the one intended behavior-contract change (full-scan response becomes the async queued
  shape), matching the AUDIT entry.
- Redis is reachable from the test container, so `/search/reindex` would enqueue during tests; an
  autouse conftest fixture (`_reindex_runs_inline`, mirroring the existing `_rate_limit_fail_open` /
  `_queue_capacity_fail_open`) forces the synchronous fallback for the API suite. Tests that assert
  the queued path monkeypatch `enqueue_reindex` back to a live id.
- D19 keeps inline embedding for column-less models (SQLite / no pgvector column): the "un-indexed
  skip" applies to chunk-column models, which is where the 20k-inline-embed problem actually occurs.
- Next recommended: the D13 stale-serve window is unbounded if searches stop before a rebuild lands;
  a periodic warm/rebuild tick (or warming on library open, which already calls `get_index`) keeps it
  fresh. Not required by the audit.

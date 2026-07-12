# 09 — Computational Efficiency

[← Security](08_security.md) · [User workflows →](10_user_workflows.md)

The codebase is already unusually well-optimized: the hot `GET /works` path is batch-loaded (no
N+1), graph/summary/viz work is node-capped, ML providers are process-cached, embeddings and index
builds are pushed onto RQ workers off the read path, and there's a pgvector ANN fast path. This
document ranks the *remaining* rough edges and records what was checked and found sound, so
optimization effort goes where it matters.

> Scale assumption: mostly single-user, must support a few LAN users. "High/Medium/Low impact" is
> relative to that scale and to whether the code is on an interactive read path or a background job.

---

## 1. Ranked hotspots

### High impact — interactive read path

**H1 · Pure-Python cosine recomputes norms every comparison.**
`services/embeddings.py:cosine_similarity` (driven from `semantic_search.py`). Stored vectors are
already L2-normalized at write time, yet the query norm is recomputed N times, each candidate norm
redundantly, and N JSON float-arrays are parsed to Python lists per query. This is the interactive
semantic-search path whenever pgvector is unavailable (SQLite/dev, Postgres without the extension,
un-mirrored models).
*Fix:* a dot product suffices for unit vectors; better, one numpy matmul over a cached parsed matrix
(the pattern `topic_graph._knn_edges` already uses). *Mitigation today:* pgvector ANN is the default
on Postgres, so this bites mainly dev/SQLite and un-mirrored models.

### Medium impact — background jobs & occasional actions

**M1 · N+1 per-candidate author lookup in reference matching.**
`services/reference_matching.py` → `_work_author_names` issues a `MetadataAssertion` query per
candidate inside the fuzzy loop; `run_matching_for_references` repeats per reference. A
"rescan references" job over a few thousand refs fans out into many small queries. Bounded (RQ job,
off the request path). *Fix:* batch-load author assertions for all blocking candidates; only look up
authors for survivors of the cheap year/identifier gates.

**M2 · Whole-corpus jobs load everything into one transaction.**
`scan_duplicates_job` and `rescan_reference_matches_job` iterate every work/file/reference in a
single session — O(N) memory + one large commit. *Fix:* paginate with `commit_every`.

**M3 · BM25 index build materializes the whole corpus in RAM.**
`services/bm25_index.py` builds the CSR matrix from every chunk + title/abstract in Python dicts.
Well-mitigated (background job, coalesced, mmap-persisted, shared read-only, serves stale during
rebuild) so it's a worker-memory concern, not latency. *Fix (only for large libraries):* chunked
matrix-build passes.

**M4 · Library-scope extractive summary concatenates all abstracts unbounded.**
`services/summarization.py` (extractive path) scores every sentence over the full concatenation (the
LLM path truncates to 12000 chars). An explicit on-demand action, so occasional, but a large library
makes it a multi-second synchronous request. *Fix:* cap/stream the combined text, or route
library-scope summaries through the queue.

**M5 · `access.can_modify_work` re-queries inside a loop.**
`services/access.py:can_modify_work` re-runs `granted_target_ids`/`user_group_ids` (two queries
each) per shelf, and `_governing_shelves` is N+1 when iterating works one by one. The list path is
fine (it uses `can_modify_shelf_precomputed` + SQL `visible_*_query`), but per-object loops over many
works (e.g. batch shelf adds) pay it. *Fix:* a per-request grant-set cache.

### Low impact

- **L1 · Substring `ILIKE '%…%'` filters** (`endpoints/works.py`: `abstract:`/`summary:`/`fulltext:`/
  `file:`) do unindexed scans, but SQL-side behind EXISTS. *Fix if hot:* Postgres trigram/GIN, or
  delegate to BM25.
- **L2 · `citation_summary._missing_works` per-id `db.get` loop** — resolves from the session
  identity map today (no extra SQL), but fragile. *Fix:* single `where(Work.id.in_(...))`.
- **L3 · `chunk_embeddings.embed_work_chunks` is one embed call per chunk** (not `embed_many`) — many
  HTTP round-trips for Ollama on a large paper. Inconsistent with the batched backfill path.
- **L4 · Unbounded in-process caches** — `citation_summary._SUMMARY_CACHE`,
  `visualization._LAYOUT_CACHE`, `external_preview._PREVIEW_CACHE` are plain dicts that only grow.
  Memory creep on a long-lived process (worker/api). *Fix:* LRU-bound them.
- **L5 · `citing_papers` GC loop is N+1**; `web_find`/`external_preview` per-call live HTTP with no
  caching of provider detection.

## 2. Verified sound (were on the checklist, not problems)

- **ML model loading** is memoized in a module-level `_PROVIDER_CACHE` keyed by `(kind, model, url)`
  — loaded once per process. (Note: N worker children each hold their own copy → N × model RAM; the
  default hash-BOW has no such cost. No explicit GPU pin — device is left to auto-detect.)
- **Embedding computation** is batched (Ollama `/api/embed`, ST `.encode(list)`, backfill batch 64);
  only the *query* is embedded on the read path.
- **GROBID / OCR** are off the request path — sync calls with 60–120 s timeouts inside RQ workers,
  batch `processCitationList`, OCR a bounded subprocess.
- **`GET /works`** uses SQL `order_by`/`offset`/`limit` and batch-loads shelves/racks/files/tags/
  reference-counts in a fixed number of grouped queries per page — no N+1, no in-memory pagination.
- **Citation graph** builds edges from grouped queries; PageRank/Brandes are node-capped
  (`MAX_NEIGHBORHOOD_NODES=500`, topic graph 400, summary 500); topic kNN is a single numpy matmul.
- **Queue / concurrency** — deterministic job ids coalesce duplicate enqueues; live-job checks
  prevent double-runs; index reads never block on a rebuild.

## 3. The dominant cost at scale is extraction, not queries

For realistic libraries the wall-clock cost is dominated by the **ingestion pipeline**, not by
serving:

1. A single **GROBID** container is the shared throughput bottleneck (one sync ~seconds-to-120 s call
   per PDF). Parallelizing via `rq_worker_count` only helps up to GROBID's capacity — scale GROBID
   itself (or run multiple instances behind a balancer) before adding workers.
2. **OCR** is the heaviest per-file CPU step (page rasterization at 300 dpi + tesseract); bounded by
   `ocr_timeout_seconds`.
3. **Embedding backfill/reindex** over a large corpus with an ST/Ollama model is CPU/GPU- and
   round-trip-bound; it commits periodically so a restart keeps progress.

If you need to profile, start there — see the GPU-optimization and performance skills for a
measurement-first approach, and remember the read path is already lean.

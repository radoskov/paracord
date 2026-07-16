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
`services/embeddings.py:cosine_similarity` is now a thin alias for the shared
`services/vector_math.py:dense_cosine` (embeddings.py:269-274), called per-candidate from
`semantic_search.py` (lines 270 and 361). Stored vectors are already L2-normalized at write time,
yet `dense_cosine` (vector_math.py:23-32) still recomputes both norms from scratch every call, each
candidate norm redundantly, and N JSON float-arrays are parsed to Python lists per query. This is the
interactive semantic-search path whenever pgvector is unavailable (SQLite/dev, Postgres without the
extension, un-mirrored models).
*Fix:* a dot product suffices for unit vectors; better, one numpy matmul over a cached parsed matrix
(the pattern `topic_graph._knn_edges` already uses). *Mitigation today:* pgvector ANN is the default
on Postgres, so this bites mainly dev/SQLite and un-mirrored models.

### Medium impact — background jobs & occasional actions

**M1 · N+1 per-candidate author lookup in reference matching (fixed for the full-library job).**
`services/reference_matching.py:_work_author_names` (line 167) still issues one `MetadataAssertion`
query per candidate when called without a prebuilt map. The "rescan references" job — the "a few
thousand refs" case this used to hit — no longer does: `rescan_reference_matches_job`
(`workers/jobs.py:411-472`) now calls `build_match_indexes` (reference_matching.py:474-504) once to
batch-load every work's author names into a `MatchIndexes.author_names` dict, and `_author_ok`/
`resolve_and_persist` (reference_matching.py:141-165, 382-407) accept that prebuilt map so the loop
does dict lookups instead of queries. The remaining N+1 is confined to
`run_matching_for_references` (reference_matching.py:435-456), which doesn't pass `author_names` —
called from `extraction.py:249` (per-extraction, small ref counts) and `endpoints/works.py:1356,1701`
(orphaned-refs / single-work rematch), not the whole-library case. *Fix if it matters:* thread
`author_names` through those call sites too, or drop this item.

**M2 · Whole-corpus jobs load everything into one transaction (partially fixed).**
`scan_duplicates_job` (`workers/jobs.py:393-408`) still iterates every work then every file in a
single session with one final commit — O(N) memory + one large commit, as described. But
`rescan_reference_matches_job` (`workers/jobs.py:411-472`) no longer does: it now commits every
`_RESCAN_COMMIT_EVERY = 500` references/external-papers (`workers/jobs.py:17`), with each row
individually try/excepted so one poisoned row only loses its batch. *Fix (still open):* paginate
`scan_duplicates_job` with the same `commit_every` pattern.

**M3 · BM25 index build materializes the whole corpus in RAM.**
`services/bm25_index.py` builds the CSR matrix from every chunk + title/abstract in Python dicts.
Well-mitigated (background job, coalesced, mmap-persisted, shared read-only, serves stale during
rebuild) so it's a worker-memory concern, not latency. *Fix (only for large libraries):* chunked
matrix-build passes.

**M4 · Library-scope extractive summary concatenates all abstracts unbounded.**
`services/summarization.py:summarize_scope` — the plain `extractive` summary type still joins every
work's abstract into one string (`combined = " ".join(abstracts)`, line 1023) and scores every
sentence over it in one `summarize_extractive` call (line 1163) with no cap. The `local_llm` path no
longer truncates: it now maps each full-text paper to a digest, then packs those digests into
`LLM_INPUT_CHAR_BUDGET`-sized chunks (11000 chars, line 39) and reduces them map-reduce style
(lines 1074-1131) — a deliberate 2026 change ("Scope summaries CHUNK to this budget (map-reduce)
instead of silently truncating like v1 did.", lines 36-38). An explicit on-demand action, so
occasional, but a large library still makes the plain extractive path a multi-second synchronous
request. *Fix:* cap/stream the combined text for the extractive path too, or route library-scope
summaries through the queue.

**M5 · `access.can_modify_work` re-queries inside a loop.**
`services/access.py:can_modify_work` re-runs `granted_target_ids`/`user_group_ids` (two queries
each) per shelf, and `_governing_shelves` is N+1 when iterating works one by one. The list path is
fine (it uses `can_modify_shelf_precomputed` + SQL `visible_*_query`), but per-object loops over many
works (e.g. batch shelf adds) pay it. *Fix:* a per-request grant-set cache.

### Low impact

- **L1 · Substring `ILIKE '%…%'` filters** (`services/works_query.py:219-246`, parsed by
  `services/search_query.py`: `abstract:`/`summary:`/`fulltext:`/`file:`) do unindexed scans, but
  SQL-side behind EXISTS. *Fix if hot:* Postgres trigram/GIN, or delegate to BM25.
- **L2 · Fixed** — `citation_summary._missing_works` (line 390) now loads scope works via
  `_load_works` (citation_summary.py:279-283), a single `select(Work).where(Work.id.in_(ids))`
  query (comment: "one IN() query, not a per-id loop"), not a per-id loop.
- **L3 · `chunk_embeddings.embed_work_chunks` is one embed call per chunk** (`chunk_embeddings.py:96-100`,
  `provider.embed(chunk.text)` in a `for` loop; not `embed_many`) — many HTTP round-trips for Ollama
  on a large paper. Inconsistent with `backfill_chunk_embeddings` (chunk_embeddings.py:126), which
  does batch via `embed_many`.
- **L4 · Fixed** — `citation_summary._SUMMARY_CACHE` (citation_summary.py:68),
  `visualization._LAYOUT_CACHE` (visualization.py:83), `external_preview._PREVIEW_CACHE`
  (external_preview.py:38) are now `BoundedTTLCache` instances (`app/utils/bounded_cache.py`: LRU
  eviction beyond `maxsize` + a TTL per entry), not plain unbounded dicts.
- **L5 · `citing_papers` GC loop is still N+1** (`citing_papers.py:460-467`, one query per
  previously-linked external paper id). `external_preview` now caches its fetched result per
  (doi, arxiv_id) for `PREVIEW_TTL_SECONDS` (900s, external_preview.py:35-38, 108-111) — the "no
  caching" note no longer applies to it. `web_find.py` still does per-call live HTTP with no
  result caching.

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

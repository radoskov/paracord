# Handoff — Structure-audit fixes, S-batch (2026-07-13)

Implements the owner-decided items from `docs/WORKPLAN_2026-07-12_structure-audit-discussion.md`
(decisions recorded at its top). On `main`, **not pushed**. One commit per S-item; full backend
suite green in-container (1137 passed / 4 skipped, `-m "not safety"`); frontend vitest 274 + build
green; alembic chain **base→0067** (plus a 0064↔0063 down/up round-trip) verified on a scratch
pgvector:pg17 container. Migrations 0064–0067 are **not yet applied to the live DB** (entrypoint
applies on next restart; rebuild images first — rapidfuzz from the previous batch still pending
too).

## What landed (commit per item)

- **S5** (`f926fb7`) — `duplicates/scan`: an absent selector now means "skip that entity type";
  previously a work-only scan swept every File row synchronously in the request.
- **S6/S7** (`ec5bdb4`) — per-work jobs (enrich/chunk/embed/topic/keywords) enqueue with
  `Retry(max=2, interval=[30,120])`; deterministic failures stay non-retried (jobs catch them
  without raising). `enrich_work_job` now RAISES when every source failed (was silently returned),
  so total transient outages retry + flag. `metadata_enrichment._get` does one in-request retry on
  429/503 honoring a capped `Retry-After`.
- **S10** (`39ea01a`) — `utils/bounded_cache.BoundedTTLCache` (LRU+TTL, thread-safe) replaces the
  three unbounded module dicts: summaries 128/15 min, viz layouts 32/30 min, previews 256/15 min.
  Content-versioned keys (summary signature, viz vector_hash) still self-invalidate.
- **S11** (`9ec3965`) — preferences: one YAML file per user under `preferences.d/`; legacy shared
  file still read as a lazy-migration fallback. Kills the multi-process lost-update race.
- **S3** (`4472477`) — ONE canonical arXiv parser (`utils.normalization.split_arxiv_id`; the other
  two modules delegate). Extraction/import-staging now normalize the DOI they promote. Migration
  **0065** backfills works/references/external_papers identifiers (collision-guarded against the
  unique indexes; colliding pairs left for the duplicate queue). Backfill verified with seeded
  collision cases.
- **S1/S2** (`ed2a886`) — `services/scope_resolution.py`: query-returning scope resolver with the
  shadow filter + visibility clamp in SQL, `count_scope_works` for size pre-checks, and
  **required** `visible_ids`. summarization/topic_modeling delegate (their copies were
  byte-identical). Citation-graph/export resolvers (extra scope types) are follow-up candidates.
- **S8/S9** (`eb99402`) — full-library rescan job builds in-memory indexes once
  (`build_match_indexes`: identifier keys, relaxed title blocks, author names) and matches
  references + external papers via dict lookups; 500-row commit batches with per-row guards.
  `resolve_external_paper` gained `clear_on_miss` (full sweep clears, targeted rescans don't).
- **S12** (`097e438`) — three-outcome citing fetch (listed / authoritative-zero / unanswered);
  authoritative zero replaces the cache and stamps new `works.citing_fetched_at/_source`
  (migration **0066**); failures keep the cache. Register entry in doc 11 updated (uncommitted).
- **S20** (`aef8985`) — citing fetch pages OpenAlex (cursor) and S2 (offset) up to
  `app_config.citing_papers_fetch_cap` (default 1000, migration **0067**, Admin → Settings UI
  field added).
- **S15/S16** (`9335aa2`) — `summarize_scope_job`/`topic_model_job` stubs are real (recompute the
  requesting user's visibility from `actor_user_id`); both AI endpoints count the scope via the
  shared resolver and, above `app_config.ai_scope_job_threshold` (default 100, same migration,
  admin UI), enqueue per-scope deterministic jobs and answer `202 {queued, job_id}`; queue-down
  falls back inline. New `GET /ai/summaries/latest` read path; Insights page polls the job and
  refreshes (summary) / prompts a topic-graph refresh.
- **S4** (`9dc4e05`) — `app/errors.py` domain errors + one app-level handler;
  `shelf_membership` migrated (same HTTP behavior, framework-free for workers/CLI).
  `build_works_query` (+ its private helpers) moved to `services/works_query.py`
  (saved_filters no longer imports from the endpoint layer; works.py re-exports the name).
  `export_service.authors_by_work` promoted public for venue_author_summary.

## Deploy checklist (live instance)

1. `docker compose build api worker frontend`.
2. Restart — entrypoint applies migrations 0064–0067 (0065 is the data backfill; it is
   collision-guarded and was tested against seeded collisions, but it does rewrite identifier
   columns — the standard `make backup` first is prudent).
3. Optionally `POST /works/references/rescan-all` once (now also backfills external-paper matches
   and benefits from the S3 normalization).

## Assumptions

- S9: the whole library's match-relevant fields fit in RAM (owner-confirmed hard assumption).
- S6: "deterministic failures don't retry" is enforced by the jobs' own catch-without-raise
  paths; any *new* job error path that should not retry must follow that pattern.
- S16: one threshold for all backends (owner said don't differentiate).

## Tests

Added/updated: `test_bounded_cache.py` (new), `test_scope_resolution.py` (new),
`test_domain_errors.py` (new), plus additions in `test_queue.py`, `test_enrichment.py`,
`test_normalization.py` (three-parsers-agree table), `test_library_sort_and_preferences.py`,
`test_reference_rescan_config.py` (end-to-end batched rescan), `test_external_citations.py`
(three outcomes, paging, admin cap), `test_library_pagination.py` (new app-config knobs),
`test_summarization.py` (queued routing + job visibility), `AdminPage.test.ts`. Fixture
adaptations in `test_batch_import.py` / `test_auth_hardening.py` (domain errors, `_get` probe).

## Security implications

- S2 makes the visibility clamp a required parameter of scope resolution (typo-proofing the IDOR
  class the register documented once).
- S15 jobs re-derive the requesting user's SEE-set inside the worker — a queued job cannot widen
  visibility beyond what the requester could see (test-covered).
- No new egress surfaces; S20 only pages the existing OpenAlex/S2 endpoints (same `_get` guard).

## Still open (needs owner discussion)

- **S13/S14** — reference consolidation job (conflict policy + trigger + unique-index commitment).
- **S17/S18/S19** — docs source-of-truth, docs commit hygiene, example-YAML pruning. The
  `docs/reference/*` working-tree edits (incl. the S12 register update) remain uncommitted.

## Next recommended task

The consolidation job (S13/S14) — it closes the last known correctness gap in the citation
subsystem (duplicate canonical rows + the find-or-create race) and unlocks the `dedup_key`
unique index.

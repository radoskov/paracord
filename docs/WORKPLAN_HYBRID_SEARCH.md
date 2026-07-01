# Workplan — Hybrid Search (BM25F+ ⊕ chunk-level ANN ⊕ RRF)

> **Status:** proposed, awaiting go-ahead. Design: [HYBRID-SEARCH-DESIGN.md](./HYBRID-SEARCH-DESIGN.md).
> Not yet merged into the live [WORKPLAN.md](./WORKPLAN.md). One product decision is still open
> (which embedding models get columns — default MiniLM-384 + nomic-768).
>
> Standing constraints apply: commit to `main`, no runtime web-UI pip installer (activate-when-present
> + documented `make` target + admin toggle), keep the SQLite-testable path for portable logic,
> Postgres-only tests for the vector/ANN path, explicit `git add`, tests + migration parity green
> before each commit.

## Guiding split

- **Lexical (BM25F+)** is DB-agnostic (pure-Python + numpy/scipy) → runs under SQLite tests and prod.
- **Semantic (chunk-level pgvector ANN)** is Postgres-only → dedicated Postgres test job.
- **Fusion (RRF)** is pure logic → SQLite-testable.

---

## Phase HS1 — Chunking + `work_chunks` schema
- [ ] Migration: `work_chunks(id, work_id FK, section, position, text, created_at)` + indexes.
- [ ] Section-aware chunker over stored TEI: ~256–512 tokens, ~10–15% overlap, skip
      references/acknowledgments, keep section label. Deterministic.
- [ ] Background job: (re)chunk a work on import / re-extraction; idempotent, replaces prior chunks.
- [ ] Tests (SQLite): chunk boundaries, overlap, section skipping, idempotency.

## Phase HS2 — Multi-column per-model embeddings + ANN
- [ ] Migration (Postgres-only, best-effort like `0019_pgvector`): add one **constrained** pgvector
      column per supported model to `work_chunks` (default `vec_minilm vector(384)`,
      `vec_nomic vector(768)`) + per-column HNSW index. No-op on SQLite.
- [ ] `embed_chunks` service: embed each chunk under the active model; write its column.
- [ ] **Backfill-on-activation** job: when a model is first enabled, fill its column for all chunks.
- [ ] Provider wiring reuses existing `resolve_embedding_provider` (sentence-transformers / Ollama),
      including the honest degraded-provider surface.
- [ ] Tests (Postgres job): column population, ANN index used, backfill fills only NULLs.

## Phase HS3 — Selectivity-adaptive filtered semantic search
- [ ] `semantic_search_chunks(query, visible_ids, model)`: embed query → rank chunks.
- [ ] Strategy switch on `|visible|/N`: **low** → pre-filter + exact over visible chunks; **high** →
      ANN with `WHERE work_id = ANY(:visible)` + pgvector iterative index scan.
- [ ] Chunk→paper roll-up (max chunk score per paper) with the matching passage/section attached.
- [ ] Remove the `limit*5` over-fetch hack from `endpoints/search.py`.
- [ ] Tests (Postgres job): exact recall at low selectivity (no cliff), ANN path at high selectivity,
      access-control leak checks.

## Phase HS4 — BM25F+ lexical engine
- [ ] Custom eager-sparse **BM25F+** scorer: per-field weighted `t̃f` (title/abstract/keywords/methods/
      conclusion ≫ intro/related-work) with per-field length norm + BM25+ δ; precomputed scipy sparse.
- [ ] Fields sourced from TEI section labels; tokenizer/stemmer via `bm25s`.
- [ ] Persistence: mmap-friendly numpy arrays + `term→col` vocab dict; **atomic versioned** save.
- [ ] Runtime: mmap matrix read-only (shared across workers); vocab dict per worker; matrix+vocab
      reloaded together on version bump.
- [ ] `POST /search/warm` (library-open) pre-faults pages; single RQ worker rebuilds after N imports /
      on schedule.
- [ ] Replace naive `_lexical_search` token-overlap path.
- [ ] Tests (SQLite): field weighting suppresses intro terms, δ long-doc behavior, mmap load/reload,
      version invalidation, access post-filter.

## Phase HS5 — RRF fusion + modes + API/UI
- [ ] `POST /search` fuses BM25F+ (paper rank) ⊕ semantic (paper rank) via RRF (k=60); optional α.
- [ ] `mode ∈ {lexical, semantic, hybrid}` (default hybrid); response carries per-paper score,
      matching passage/section (semantic), and provider provenance.
- [ ] Frontend: mode selector; warm-on-open call; show matching passage + why-related.
- [ ] Tests: RRF ranking correctness, mode routing, both-engine union filtering.

## Phase HS6 — Admin / activation / docs
- [ ] AI & Models tab: per-model activation + backfill status; BM25F+ field-weight config (sensible
      defaults); index-rebuild status/trigger.
- [ ] `make` target(s) for the AI image extra (sentence-transformers) + Ollama profile; detection via
      existing `model_management.detect_providers`. No runtime pip installer.
- [ ] Update SPECIFICATION.md + this doc; fold ticked items into WORKPLAN.md.

---

## Open decision before HS2
Which embedding models get a column (default: **MiniLM-384 + nomic-768**). The schema/migration in
HS2 is written against whatever set is chosen; adding a model later is a migration, not a config
change.

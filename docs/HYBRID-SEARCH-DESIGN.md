# Hybrid Search Design (lexical + semantic + fusion + filtering)

> Design agreed in the B1/B3 discussion (2026-07-01). Supersedes the storage/model-switching note
> in [B1-B3-ML-DEPTH.md](./B1-B3-ML-DEPTH.md) §1.1/§4.1. Grounded in two sources:
> - TigerData, *Hybrid search* — the RRF fusion recipe (BM25 + vector, Reciprocal Rank Fusion).
> - Amanbayev, Tsan, Dang, Rusu, *Filtered Approximate Nearest Neighbor Search in Vector Databases*
>   (arXiv:2602.11443, Feb 2026) — the pre/post/runtime **filtering taxonomy**.
>
> **Scope:** this is the *search* axis (semantic search + related papers + lexical search). Topic
> clustering (B1) is a separate axis and is unchanged by this document.

---

## 1. Overview

Three user-selectable modes:

- **Lexical-only** — keyword/BM25; use when you know the exact terms.
- **Semantic-only** — dense embeddings; use for pure concept search.
- **Hybrid** (default) — fuse both; use to find the best match for *something* when unsure of phrasing.

**Architecture (Arch A):**

- **Lexical:** document-level **BM25F+** — a custom, eager-sparse scorer with per-field length
  normalization (the "F", so title/abstract/methods/conclusion outweigh intro/related-work) and the
  BM25+ δ lower-bound (the "+", so long papers aren't unfairly penalized).
- **Semantic:** **chunk-level** dense retrieval — papers are split into passages, each embedded, so
  search finds the *relevant passage*, not just an on-topic paper.
- **Fusion:** **Reciprocal Rank Fusion (RRF)** at the **paper level**. The semantic side aggregates
  its best chunk(s) into a per-paper score; BM25F+ scores papers directly; RRF combines the two
  rankings. RRF is rank-based, so the unbounded BM25 scores and the [0,1] cosine scores never need
  normalizing to a common scale.

```
                 ┌───────────────────────────┐
   query ───┬───▶│ BM25F+ (doc-level, lexical)│──rank of papers──┐
            │    └───────────────────────────┘                  │
            │    ┌───────────────────────────┐                  ▼
            └───▶│ dense ANN (chunk-level)    │─chunks─▶ roll-up ─▶ RRF ─▶ papers
                 └───────────────────────────┘  to papers    (k=60)
```

Why Arch A (not chunk-level BM25 for both): BM25 finds a keyword match in a full document
regardless of chunking, and keeping it document-level **preserves BM25F's per-field weighting** —
the mechanism that suppresses "random utterances from intro/related-work." Chunking is what the
*semantic* side needs for passage recall; the lexical side doesn't benefit from it.

---

## 2. Lexical engine — BM25F+

### 2.1 What the index contains

An inverted index over **terms** (single tokens; bag-of-words — not phrases):

- **Postings**: per term, the list of `(document, per-field term-frequency)`.
- **Document frequency** per term → IDF.
- **Per-field lengths** per document + per-field corpus averages (for BM25F normalization).
- **N** (document count).

### 2.2 Scoring (BM25F+)

Per-field term frequencies are combined into a weighted pseudo-frequency **before** saturation, each
field normalized by its own average length; then the BM25+ δ lower-bound + saturation is applied
once:

```
t̃f(t,d) = Σ_field  w_field · tf(t, field) / (1 − b_field + b_field · len_field / avglen_field)

score(q,d) = Σ_{t∈q}  IDF(t) · [ (t̃f · (k1+1)) / (t̃f + k1) + δ ]
```

- `w_field`: field weights (title ≫ abstract ≈ keywords ≈ conclusion ≈ methods ≫ intro/related-work).
- Per-field `b_field`: length normalization per field (so the abstract is judged against average
  *abstract* length, not the whole PDF).
- `δ`: BM25+ lower bound so a long paper that genuinely discusses a term can't score below a short
  paper that lacks it.

Only query terms contribute; only documents in the query terms' postings are scored.

### 2.3 Why custom, not a library call

`bm25s` supports the BM25+/BM25L *saturation variants* but has **no field concept**. The
"replicate-tokens-per-field" trick approximates BM25F but collapses to a single combined document
length — discarding exactly the per-field length normalization we want. So we implement the ~100-line
BM25F+ scorer ourselves. Because a term-document score depends only on document statistics (not the
query), we still **precompute it into a scipy sparse matrix** (eager scoring) — keeping `bm25s`-class
query speed (sub-millisecond to low-ms at our scale) while getting exact BM25F+.

Implemented directly on `numpy`/`scipy` (a regex tokenizer + a `scipy.sparse` CSR matrix); `bm25s`
itself is not a dependency, since it offers no BM25F and we precompute the CSR ourselves.

### 2.4 Persistence, memory, and sharing

- **Footprint:** ~25–50 MB for ~1,000 full-text papers; ~90–140 MB at 5,000 (postings grow linearly,
  vocabulary sublinearly per Heaps' law). Small.
- **Persistence:** the eager sparse matrix is saved as **mmap-friendly numpy** arrays
  (`data`/`indices`/`indptr`).
- **Sharing across API workers:** each worker **memory-maps the matrix read-only** →
  the OS page cache holds **one physical copy** shared across all workers. The **vocabulary dict**
  (`term → column-id`) is **loaded per worker** (~15 MB each) for O(1) lookups — duplicated but
  trivial, and avoids a slower mmap'd lookup. Matrix + vocab are versioned as a unit and reloaded
  together on a version bump.
- **Warming:** the frontend signals "library opened" (`POST /search/warm`) to pre-fault the mmap
  pages so the first query is hot; never load per query. Under memory pressure the kernel reclaims
  page-cache pages automatically, so an explicit idle-eviction reaper is optional.
- **Updates (single-writer):** one RQ worker rebuilds the index after N imports / on a schedule,
  writes a **new versioned file**, and does an **atomic rename**. Readers mmap read-only and never
  conflict with the writer (it's a different file); they pick up the new version on next access.
- **Access filtering (lexical side):** post-filter — return a generous top-k, intersect with the
  caller's `visible_work_ids`. Fine at this scale.

---

## 3. Semantic engine — chunk-level dense ANN

### 3.1 Chunking

Section-aware chunks derived from the GROBID/TEI structure: cap ~256–512 tokens with ~10–15% overlap,
**skip references/acknowledgments**, keep the section label per chunk. ~20 chunks/paper →
~20,000 chunks per 1,000 papers.

### 3.2 Storage — multi-column, per-model, constrained

A new `work_chunks` table (chunk → work, section label, text, position). Embeddings live **per
chunk**, with **one constrained pgvector column per supported model**, each with its own ANN index:

```
work_chunks(
  id, work_id, section, position, text,
  vec_minilm  vector(384),   -- sentence-transformers/all-MiniLM-L6-v2
  vec_nomic   vector(768),   -- ollama:nomic-embed-text
  ...                        -- one column per supported model
)
-- per-column ANN index, e.g. USING hnsw (vec_minilm vector_cosine_ops)
```

Rationale (corrects B1-B3-ML-DEPTH §1.1/§4.1):

- **Storage is cheap** — pgvector stores `float4` = 4 B/dim. Per 1,000 papers × ~20 chunks: 384-dim
  ≈ 30 MB, 768-dim ≈ 60 MB per model. Keeping several models is tens of MB. Negligible.
- **Constrained columns → real ANN.** A fixed dimension per column allows an HNSW/ivfflat index
  (an *unconstrained* column cannot be ANN-indexed). At ~20k chunks this is where ANN earns its keep
  (brute-force Python cosine over 20k vectors is ~50–100 ms; ANN is sub-ms).
- **No reindex on model switch.** Switching the active model = query a different column. Old vectors
  are kept intentionally (they're cheap), so nothing is deleted.
- **Backfill-on-activation.** The real cost is embedding each chunk under a model once. The first
  time a model is enabled, a background job backfills its column; thereafter it's instant and
  permanent.

### 3.3 Filtering — selectivity-adaptive (from the paper)

The paper's taxonomy: **pre-filter** (filter → search survivors; exact), **post-filter** (search →
drop non-matching; recall cliff at low selectivity), **runtime-filter** (filter during traversal;
needs a specialized index). pgvector defaults to post-filter, which is why the current
`limit*5`-over-fetch hack exists and can still under-fill when a user sees little of the library.

Replacement — pick strategy by selectivity of `visible_work_ids`:

- **Low selectivity** (user sees a small fraction) → **pre-filter + exact**: brute-force cosine over
  just the visible chunks. Exact (no recall cliff) and fast *because* the set is small.
- **High selectivity** → **ANN with the allow-list pushed down** (`WHERE work_id = ANY(:visible)`),
  using recent pgvector's **iterative index scan** to avoid under-filling k.

A single selectivity threshold switches between them. This makes access control **exact** instead of
best-effort and removes the over-fetch guess.

---

## 4. Fusion — RRF

```
RRF(paper) = Σ_engine  1 / (k + rank_engine(paper)),   k = 60
```

- Each engine (BM25F+ paper ranking; semantic paper ranking after chunk→paper roll-up, e.g. max
  chunk score) contributes `1/(k+rank)`. Papers found by both rank highest.
- Rank-based → no score normalization needed across BM25 (unbounded) and cosine ([0,1]).
- Optional weight α to bias lexical↔semantic if ever wanted; default equal.
- Access filtering applied to the fused set (both engines already filter; the union is re-checked).

---

## 5. Testing split

- **SQLite** (fast, in-process) for the bulk of the suite: access control, models, API behavior, and
  the **BM25F+** engine (DB-agnostic — builds the CSR in-memory; disk mmap persistence is Postgres-only).
- **Postgres** (dedicated CI job + prod) for the **vector/ANN** path (constrained columns + ANN
  indexes are Postgres-only) and migrations.

Rationale: SQLite is a fast *test double* for portable logic; Postgres is the production database
(concurrency/MVCC for multi-user + background workers, pgvector/ANN, robustness). See
B1-B3-ML-DEPTH §Q3 discussion.

---

## 6. Open product choice (not blocking the design)

Which embedding models get a column (each ANN-indexed, backfilled on activation):

- **Default recommendation:** MiniLM-384 (in-process, no daemon) + nomic-768 (Ollama). Covers the
  no-daemon and daemon paths; ~90 MB/1,000 papers combined at chunk level.
- Alternatives: MiniLM only (simplest), or add a 1536-dim slot for a larger model later (migration).

This is the remaining B3 decision; the architecture supports whichever set is chosen.

---

## 7. References

- TigerData — *Hybrid search* (RRF recipe): https://www.tigerdata.com/docs/build/examples/hybrid-search
- Amanbayev, Tsan, Dang, Rusu — *Filtered Approximate Nearest Neighbor Search in Vector Databases:
  System Design and Performance Analysis*, arXiv:2602.11443 (Feb 2026).
- Lù — *BM25S: Orders of magnitude faster lexical search via eager sparse scoring*, arXiv:2407.03618.

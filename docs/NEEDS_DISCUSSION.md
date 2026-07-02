# Needs discussion / deferred items

> **SUPERSEDED (2026-07-02): read `docs/DECISIONS.md` instead.** Every item below was
> re-verified against the code and merged into that consolidated audit + decision list.
> Already closed there: 2c (default-shelf hooks) and 3a (provider cache) were implemented;
> the rest map to decisions D6, D12, D19–D22, D31–D34, D38. This file is kept only as the
> original record of the B1-era reasoning.

Companion to `WORKPLAN_B1_AND_ISSUES.md`. This collects everything that was **deferred, decided-with-an-assumption, or surfaced by the code audit as a design/behavior choice** during the autonomous implementation of B1 + the 24 issues, plus the follow-up test review and full audit. Nothing here blocks what shipped; these are decisions for you to make when you have a moment.

Status legend: **[decided]** you already chose this; **[assumption]** I picked a sensible default, confirm or override; **[open]** genuinely needs your call.

---

## 1. Decisions you already made (recorded for the record) — [decided]
- **Default shelf (#1):** real "Inbox" shelf, admin-configurable access level, ephemeral membership; existing loose papers migrated onto it; papers fall back to it when removed from their last real shelf.
- **Tab caching (#9):** Option A — keep tabs mounted, hide with CSS; polling gated on active tab; Cytoscape `resize()` on show.
- **Embeddings (#21):** dynamic per-model registry with runtime `ALTER TABLE`, slug allowlist, model cap, add + delete; users pick a single model or "multimode" (RRF across all) for search and clustering.

---

## 2. Items I deferred during implementation (out of the shipped scope)

### 2a. #15 — Library "Shelves" / "Racks" columns — [open, backend not done]
The workplan called for library-table columns listing each paper's shelves + racks. I **did not implement** this: doing it well needs a batched serialization change in `list_works` (one grouped query for shelf/rack names across the page, SEE-filtered, attached to `WorkRead`) to avoid an N+1. It's a moderate, perf-sensitive change to a hot endpoint. Everything else in the 24 issues shipped. **Decision:** want the columns? If so I'll add the batched query + the two columns in `columns.ts`.

### 2b. #20 — Per-section BM25 scores in lexical results — [assumption: deferred]
Search now shows a normalized **relevance %** (fixing the "weird percentages") and semantic/hybrid hits carry the matching `section`. Exposing *which section matched for lexical (BM25F) hits* would need `bm25_index` to return per-field contributions (today they collapse to one scalar). Lower value since semantic/hybrid already show section. **Decision:** worth surfacing per-field lexical scores, or is the current section-on-semantic-hits enough?

### 2c. Default-shelf auto-placement covers the main creation paths only — [assumption]
Auto-placement (#1) is wired into manual create + batch/BibTeX/RIS-CSL imports + the shared add/remove paths. **Not yet hooked:** single identifier-lookup import, agent/file ingestion, and duplicate-resolution merges (a merged paper could end up loose). The migration backfilled all *existing* loose papers, and the `loose → open` safety net still holds, so nothing is broken — but a paper created through those specific paths could momentarily be loose. **Decision:** hook the remaining creation paths (small, one line each) — recommended.

### 2d. AI provider wiring nuance (#10) — [assumption]
Scope summaries now support `local_llm`, but the LLM only runs when the admin has set `summary_provider = local_llm` (not just a model). The admin AI panel exposes `summary_provider`; confirm the UX makes it clear that BOTH the provider and the model must be set. (This matches the work-level behavior.)

---

## 3. Audit — needs-discussion findings (design / behavior trade-offs)

### 3a. Embedding provider caching — [open, recommended] · efficiency
`SentenceTransformerProvider` reloads the model weights on **every** `resolve_embedding_provider` call (every search/reindex; ×N in multimode). Memoizing providers per `(kind, model, url)` would be the single biggest latency win for the sentence-transformers path. The reason it's not already done: a cached live model holds memory for the process lifetime and must be evicted when a model is unregistered/deleted. **Decision:** add a provider cache with eviction on `unregister`/model-delete? (Ollama is unaffected — it's just an HTTP client.)

### 3b. Topic-graph / topic-clustering re-embedding on the read path — [open] · efficiency + stability
When a paper has no stored chunk vectors for the selected model, the topic backend/graph embeds its text **inline, one paper at a time** (N sequential Ollama calls, or N un-batched ST encodes). Search deliberately keeps embedding *off* the read path; topic modeling currently does the opposite. Options: (a) batch the fallback (ST `.encode(list)`, reuse one Ollama client); (b) require pre-indexed chunk vectors and skip un-indexed papers instead of embedding inline. **Decision needed on the policy.** The pgvector `avg()` mean-pool fast path is already used whenever chunk vectors exist.

### 3c. Topic-graph O(n²·dim) pure-Python cosine — [assumption: acceptable at scale] · efficiency
The similarity graph does n² Python cosine calls (norms recomputed each call), bounded by `MAX_NODES=400`. Fine at a few hundred papers; a numpy matrix (`M @ M.T` after one normalize) would be ~100–1000× faster and is the right fix if multimode/large scopes become common. Left as-is because it touches the shared `_cosine`/vector format. **Decision:** do the numpy rewrite now or wait until it bites?

### 3d. Admin-configured `ollama_url` has no SSRF guard — [open] · security (low)
The find-on-web egress guards (internal-IP block, shadow-library denylist) are **not** applied to the admin-set `ollama_url`, which the server fetches (tags/embeddings/pull). It's admin/owner-only and defaults to loopback, so it's self-inflicted for a single-user host — but it's an inconsistency. A blanket internal-IP block would break the legitimate `localhost`/Docker-service-name default. **Decision:** validate scheme/host and require explicit opt-in for non-loopback/remote hosts? (Depends how much you trust the admin role vs. the host network.)

### 3e. Runtime index build locks `work_chunks` during a long backfill — [open] · stability
`register()` runs `CREATE INDEX ... hnsw` inside the reindex job's transaction; on a large `work_chunks` it holds a table lock for the whole HNSW build, blocking concurrent chunk writes (imports) until commit. Options: provision the column/index in a short dedicated transaction that commits before the long backfill, or document model-activation as a maintenance operation. (Truly non-blocking needs `CREATE INDEX CONCURRENTLY`, which can't run in a transaction — larger change.) **Decision:** split the provisioning transaction, or document?

### 3f. Rolling-deploy window for the "no free-floating papers" invariant — [open] · stability (low)
Migration 0037 backfills loose papers, but the invariant is otherwise enforced only in the *new* app code. During a rolling deploy where the migration has run but an old app instance is still serving, a paper created by the old code lands loose (and old code treats loose = world-visible) until touched. **Decision:** run the backfill idempotently on startup / as a follow-up job, or enforce deploy ordering (code-with-invariant first, then migrate)?

### 3g. Multimode search cost — [assumption: acceptable] · efficiency
Multimode does M pgvector KNN queries + M `Work` hydrations + M `COUNT(*)` per search (M = active models). The per-model query embedding is unavoidable (different vector spaces). Mostly resolved by 3a (provider cache); the M `COUNT(*)`s could be computed once. Low priority. **Decision:** fold the COUNT/hydration optimization in with 3a, or leave it?

### 3h. Ragged / zero-vector dense clustering — [assumption: guarded] · stability (low)
If a model's recorded dim ever disagrees with its live output dim, concatenated multimode vectors could be ragged and yield plausible-but-wrong cosines (dicts tolerate missing keys as 0). A paper with empty title+abstract and no chunk vectors becomes an all-zero isolated node (handled cosmetically by the "hide singletons" toggle). I added a guard for malformed pooled rows; enforcing a consistent per-model dim (pad/truncate) is the fuller fix. **Decision:** enforce dim strictly?

---

## 4. Audit — "all-clear" spec gaps I recommend but did NOT implement

These are pre-existing **specification** features (independent of the 24 issues) the audit found missing. Each is a smallish, unambiguous build, but they are net-new features, so I left them for your go-ahead rather than expanding scope unprompted. Listed most-valuable first.

- **Audit-event wiring (§7.6):** several required events are never emitted — `shelf.created/modified`, `rack.created/modified`, `paper.metadata_edited`, `annotation.created/edited`, `job.started/completed/failed`, backup/restore events. Each is a one-line `record_event` at a known site. (Held back because adding events can shift audit-count test expectations — wants a quick pass + test update.)
- **Summary provenance columns (§8.14.2):** `summarization.py` already computes `provider_requested/provider_used/fallback` but the `Summary` model has no columns for them, so they're only transient (surfaced in the response, not persisted). A small migration would persist them (plus source-section/hash/user/params).
- **Annotation JSON export (§8.8.7):** annotation export supports `markdown|text`; the spec *requires* `json`. Small, self-contained.
- **Additional search operators (§14.2):** `abstract:`/`fulltext:`/`summary:`, `has:grobid`/`has:ocr`, `file:…`, `duplicate:`/`version:`/`warning:*` are defined but unparsed.
- **Missing export formats/targets (§8.13):** LaTeX `\cite` commands, Pandoc-Markdown citation list; import-batch / missing-references export targets; unresolved-reference strings in exports.
- **Import can target a rack (§8.1):** imports carry only `target_shelf_id`, not `target_rack_id`.

**Decision:** want me to implement this batch (I'd do audit-events + provenance columns + annotation-JSON first)? They're spec-conformance, not part of the 24 issues.

---

## 5. Larger spec gaps — genuinely needs-discussion (bigger features)

The audit rated these **high**-value but **large**; they are real product features, not mechanical fixes:

- **Citation summaries (§8.11)** — per-shelf/rack citation analytics (missing frequently-cited works, bridge papers, most-cited local/external, isolated papers, chronological distribution, etc.), cached + versioned. Currently only node/edge counts exist; the `citations.py` router is empty. This is a substantial feature.
- **Citation-graph depth (§8.9)** — the spec lists 8 graph modes, 4 edge-context modes, and rich visual encodings (PageRank sizing, color-by-shelf/tag/topic/status, edge thickness by mention count, warning badges) plus a per-work neighborhood endpoint. Today: `local_only`/`include_external` + `collapse_versions`, degree-based sizing. The #8 upgrades added fcose/tooltips/hide-singletons/topic-graph but not the full mode matrix.
- Smaller spec deltas the audit noted: duplicate detection missing preprint↔published + same-file-different-paths kinds (§8.4); external-title promotion skips the normalized-similarity check (§8.12.3); CSV/TSV + watched-folder + Zotero ingestion (§8.1); `CitationMention` lacks paragraph/sentence FKs + `extraction_confidence` (§8.10); backup/restore is CLI-only (no REST endpoints, §10.2); live auto-invalidated shelf/rack bibliography (§8.17.3); reference-string fallback parser (anystyle/refextract, §8.2).

**Decision:** which (if any) of §8.11 / §8.9 do you want scoped into a future workplan?

---

## 6. Confirmed sound by the audit (no action) — for your confidence
SQL-identifier interpolation across the new registry/chunk/topic raw SQL is airtight (slug allowlist + `^vec_[a-z0-9_]+$` re-check, int-cast dims, bound values). Visibility clamps are applied consistently on every work-returning endpoint incl. the new search/topic-graph/file endpoints (no IDOR). find-on-web SSRF + shadow-library guards intact. Default-shelf placement is monotonic-narrowing (never widens visibility). Auth basics (bcrypt, owner-immutability, admin-can't-manage-admins) clean. Migrations 0036–0039 are reversible and parity-checked.

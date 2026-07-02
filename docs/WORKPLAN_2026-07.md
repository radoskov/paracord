# PaRacORD — Workplan 2026-07

Consolidated plan for the next build round, from the 2026-07-02/03 decisions. Three tracks:
**A** autonomous AUDIT fixes (owner delegated "resolve on your own"), **B** the D31 spec-conformance
batch, **C** the D38 visualization module. Sources: `docs/AUDIT.md`, `docs/DISCUSSIONS.md`,
`docs/VISUALIZATION_DESIGN.md`. Standing rules: commit per slice on `main`; **full suite (+ E2E)
green before any push**; push only on explicit owner approval.

Status keys: ☐ todo · ◐ in progress · ✔ done. IDs match `docs/AUDIT.md`.

---

## Track A — Autonomous AUDIT fixes

Grouped by theme; each is small and self-contained unless flagged. The three **⚠ flagged** items
carry a behavior-contract or infra change — I'll implement them but they're called out so they're
not a surprise.

### Security
- ☐ **D2 — CSP + security headers** on `frontend/nginx.conf` (`Content-Security-Policy`,
  `X-Frame-Options DENY`, `X-Content-Type-Options nosniff`, `Referrer-Policy no-referrer`).
  Everything is bundled/self-hosted so a `'self'` policy should hold — **needs one manual smoke of
  the built bundle** (fonts/workers/inline styles) before it's trusted.
- ☐ **D3 — agent plaintext-HTTP guard** ⚠(minor UX): warn, and refuse unless
  `allow_insecure_http: true`, when `server_url` is non-loopback `http://`; short INSTALL note on
  TLS via reverse proxy.
- ☐ **D5 — random `POSTGRES_PASSWORD`**: `make init` generates one instead of the literal default;
  `.env.example` keeps a clearly-fake placeholder.
- ☐ **D6 — `ollama_url` SSRF guard**: validate scheme+host; allow loopback / docker-service names
  freely; require an explicit opt-in for other hosts.
- ☐ **D4 — non-root containers** ⚠(infra): add a non-root `USER` to each image + `chown` app dir +
  storage. Risk: existing volumes are root-owned → include a one-time ownership-fix step and verify
  the stack still reads/writes storage before considering it done.

### Correctness / robustness
- ☐ **D8 — enrichment per-source resilience**: catch per source, record which failed in the job
  result, continue with the rest (chained indexing already guaranteed by D7).
- ☐ **D9 — folder import transaction** ⚠(contract): commit the batch row first, per-file savepoints,
  finalize at the end — partial imports become visible instead of an all-or-nothing rollback.
- ☐ **D10 — worker waits for migrations**: gate the worker/supervisor start on
  `alembic current == head` (wait loop in the entrypoint).
- ☐ **D11 — startup loose-paper backfill**: run migration 0037's backfill idempotently on startup
  (closes the rolling-deploy window; makes the no-loose-papers invariant airtight).
- ☐ **D12 — strict multimode dims**: skip-with-warning on a per-model dim mismatch rather than
  padding (padding hides a real registry bug).

### Performance
- ☐ **D13 — BM25 rebuild off the read path** (HIGH): enqueue the rebuild as a background job and
  serve the stale index meanwhile; build from the materialized `work_chunks` instead of re-parsing
  TEI. (Unbounded index-file growth already fixed.)
- ☐ **D14 — batch embedding**: add `embed_many()` (Ollama batch input); route the legacy
  `POST /search/reindex` to the queued job.
- ☐ **D15 — background full-library dup scan** ⚠(contract): force `background=true` for
  whole-library scans (queued path exists); keep sync for single-work scans.
- ☐ **D16 — frontend batch ops**: chunked `Promise.all` (concurrency ~6) for select-N mutations.
- ☐ **D17 — Cytoscape toggle**: show/hide elements on the live instance; re-layout only on explicit
  request (no full rebuild per checkbox).
- ☐ **D19 — topic read path**: require pre-indexed vectors, skip un-indexed papers with an
  "N not indexed — reindex" notice (keeps reads read-only).
- ☐ **D20 — numpy topic cosine**: replace the O(n²) Python loop with `M @ M.T` after one normalize.
- ☐ **D22 — HNSW provisioning**: split the `CREATE INDEX` into its own short transaction (small now
  that reindex commits are batched).

### Dependency / ops hygiene
- ☐ **D24 — backend lockfile**: compile a hash-pinned lock (`uv pip compile` / `pip-compile`)
  installed by the Dockerfiles; keep `requirements.txt` as intent. Bump `httpx2` 2.4.0 → 2.5.0.
- ☐ **D29 — frontend majors**: verify Vite 8 / TS 6 / pdfjs 6 / vitest 4 / jsdom 29 are stable
  releases; pin back any pre-stable. (Verify online.)
- ☐ **D30 — ops polish** (optional): a `slim` backend image without the OCR toolchain; runtime
  `config.js` API-base injection so the prod frontend needn't rebuild on address change.

---

## Track B — D31 spec-conformance (items 1–5; item 6 dropped)

- ☐ **B1. Audit-event wiring (§7.6)** + persistence/UX: emit the missing events (`shelf.*`,
  `rack.*`, `paper.metadata_edited`, `annotation.*`, `job.*`, backup/restore) — one `record_event`
  per site; update the count-asserting tests. Events already persist in Postgres and the endpoint
  already paginates; **add** an append-only JSONL **file sink** (mounted volume) and confirm the
  admin UI exposes the existing pagination.
- ☐ **B2. Summary provenance columns (§8.14.2)**: migration adding `provider_requested`,
  `provider_used`, `fallback` (+ source-section labels / content hash / user + params) to `Summary`;
  persist them on creation.
- ☐ **B3. Annotation JSON export (§8.8.7)**: add a `json` branch to the annotation-export endpoint.
- ☐ **B4. Additional search operators (§14.2)**: parse `abstract:`/`fulltext:`/`summary:`,
  `has:grobid`/`has:ocr`, `file:…`, `duplicate:`/`version:`/`warning:*`; map each to the works query
  (`fulltext:` searches chunk/TEI body text).
- ☐ **B5. Export formats (§8.13)**: LaTeX `\cite` renderer + Pandoc-Markdown citation list; wire the
  import-batch / missing-references export targets + unresolved-reference strings.
- ~~B6 import-to-rack~~ — dropped (papers live on shelves, not racks).

Order: B1 → B2 → B3 (safe first batch) → B4 → B5.

---

## Track C — D38 visualization module

Decisions locked (see `VISUALIZATION_DESIGN.md` §6): **ECharts** (lazy-loaded, canvas+WebGL, full
interactions), **both axes independently selectable**, all 2a encodings + velocity axis, **PCA
default + UMAP opt-in**, 2d = co-citation + topic-river + heatmap (no citation-over-time), citation
counts fetched + shown. Extensible **provider (server) → normalized `VizPayload` → view-registry
(frontend)** architecture. Phased, each phase shippable:

- ☐ **P1 — Citation counts (prerequisite).** `Work.citation_count` + `_source` + `_fetched_at`
  (migration); parse in Crossref/OpenAlex/S2 connectors with a source priority; show in the paper
  metadata view; periodic/opt-in refresh; degrade cleanly for papers with no resolvable id.
- ☐ **P2 — Viz scaffold + temporal citation map.** Server provider layer + `VizPayload`; frontend
  view-registry + lazy ECharts + shared theme; the Litmaps-style scatter with both-axis dropdowns
  (year / local-degree / citation-count first), size/color/shape encodings, optional citation edges.
- ☐ **P3 — Embedding-cluster map.** Server-side PCA-2D (numpy), cached per (scope, model,
  embedding-version); topic/cluster coloring; node cap + sampling for large scopes.
- ☐ **P4 — Citation summaries (§8.11) analytics.** On the same computed layer: most-cited local /
  external, frequently-cited-but-missing, bridge papers, isolated papers, chronological
  distribution; cached + versioned; endpoints + UI. (This is the README headline feature.)
- ☐ **P5 — More views + graph depth.** Co-citation / bibliographic-coupling network; topic river;
  similarity heatmap; §8.9 network depth (PageRank/centrality sizing, color-by, edge thickness,
  neighborhood endpoint); **UMAP opt-in** layout (image extra; numba/llvmlite).

---

## Sequencing

1. **Track A first** (mostly small, de-risks the codebase; D13 HIGH is the priority). The ⚠ items
   (D3, D4, D9, D15) I'll do but flag results for review.
2. **Track B** (self-contained spec conformance; B1–B3 batch, then B4–B5).
3. **Track C** phase by phase (P1 → P5); the largest, landed incrementally.

Each item/phase: commit per slice on `main`, full backend suite + `frontend-check` (+ E2E where UI
changes) green, then batch for an owner-approved push.

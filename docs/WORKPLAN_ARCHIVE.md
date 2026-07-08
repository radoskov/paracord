# PaRacORD — Work Plan Archive

**Consolidated 2026-07-08.** A digest of **completed** workplan points, drawn from the nine per-round
workplan documents that preceded the consolidation (all now in `documentation_archive.zip`). This is
the historical record; the live backlog is [`WORKPLAN.md`](WORKPLAN.md), the running log with commit
hashes is [`../PROGRESS.md`](../PROGRESS.md), and resolved technical issues are in the archive section
of [`AUDIT.md`](AUDIT.md).

Everything below is **DONE and verified** in code + `PROGRESS.md` at consolidation time. Grouped by
the source workplan.

---

## Master execution plan (`WORKPLAN.md`, 2026-06-29) — Stages 1–9
- **Stages 1–5** — A1 managed-path fix, A3 `make ready`, B1 GROBID coordinates, the PDF.js reader,
  the Cytoscape citation graph, the Stage-4 frontend IA/UX overhaul (Batches 1/2/3), and Stage-5
  agent manifest + hash-verified teleport plus the **agent redesign v2** (SPEC §32) S1–S5.
- **Stage 6** — H2 embeddings moved off the read path; summary/topic provider interface; semantic
  dual-mode search (2026-06-30).
- **Stage 7** — H3 fuzzy dedup, auth hardening, security-doc truthfulness, export polish, view-audit
  events, ops backup/restore.
- **C3/C4 remainder** — migration 0017 core FKs + audit JSONB.
- **UI/agent/reader round (2026-06-30)** Phases 1–4, items 1–22 — all done.
- **Testing-feedback batch 2** Phases A–G + follow-ups — server import roots, find-on-web allowlist
  (v2/v2.1 backend + frontend).
- **Access-control / batch-import / modeling round** Phases H–N — Groups, the full role ladder,
  rack/shelf ACLs, batch import, per-paper topic/keyword buttons, the AI & Models tab, shelf-membership
  UI.
- **Gap-analysis all-clear** AC1–AC3.
- **Approved B-items** B2/B4/B5/B6/B7 — fallback surfacing; real CSL via citeproc-py (commit
  `cc33525`, 7 bundled `.csl` styles); OCR; graph scopes + version collapse; saved filters.
- **Hybrid search** HS1–HS6 (see below).

## Hybrid search (`WORKPLAN_HYBRID_SEARCH.md`) — HS1–HS6
All six phases done (commits `b77dafa` / `5134791` / `d030ee6` / `55a930c` / `fb5fe66` + HS6):
`work_chunks` passage table + section-aware chunking; per-model dimension-constrained pgvector
columns (`vec_minilm(384)` / `vec_nomic(768)`) + HNSW ANN + dynamic embedding-model registry;
BM25F+ document scorer off the read path with atomic-rename versioning; selectivity-adaptive
filtering; RRF fusion behind a unified `POST /search` (`mode: lexical|semantic|hybrid`, default
hybrid); `POST /search/warm` + admin hybrid-status readout.

## `WORKPLAN_NEXT.md` — Stage 8 + Stage 9
- **Stage 8** — DB-backed, GUI-managed AI providers (8A–8F).
- **Stage 9** — pgvector/H7 gating, the Postgres integration suite, CSL styles, library + graph-scope
  export, the ML-extraction seam, API happy-path E2E.

## `WORKPLAN_2026-07.md` — Tracks A/B/C
- **Track A** — 19 audit fixes (D2–D6, D8–D22, D24, D29). *(Exceptions still open: D3, D30 — see
  `WORKPLAN.md`/`AUDIT.md`.)*
- **Track B** — D31 spec-conformance B1–B5 (audit-event wiring + JSONL sink, summary provenance,
  annotation JSON export, new search operators, LaTeX/Pandoc export). *(B6 dropped by design.)*
- **Track C** — D38 visualization module P1–P5 (citation counts; viz scaffold + temporal map; PCA
  cluster map; §8.11 citation summaries; co-citation / coupling / topic-river / heatmap; §8.9 depth;
  UMAP opt-in).

## `WORKPLAN_2026-07-06.md` — feature/UX batches (PROGRESS 2026-07-07)
- **R** — reader dim/dark tuning + reference-box overhaul + scroll mode.
- **W** — Docker/Makefile robustness (out-of-box + after-change).
- **T** — T1 selected-state token, T2 load-as-template.
- **L** — L1–L4 (inbox out of menus, remove Insights search, jump-to-selected, scope-summary model).
- **P** — P1a/P1b/P2/P3 (refresh consistency, metadata match %, arXiv/DOI persistence).
- **C** — C1/C2/C3a–c citation-summary enrichments.
- **S** — `make test-safety` + `backend/tests/safety/` (10 files / 158 tests).
- **D** — duplicate-resolution overhaul: merge / link / unmerge, merge-shadow model, migration 0053.

## `WORKPLAN_2026-07-07_batch6.md` + `issue_batch_6.md` (2026-07-07/08)
- **All-clear** A1–A6 (commits `081852c`, `4e0d977`, `e6f970d`, `4ccef25`, `d24aae7`; A5 agent prune
  in Batch A).
- **Needs-discussion** B1–B8, all built: B8 per-paper stored summary; B5 lexical-index staleness +
  Rebuild button; B2 reindex-vs-no-PDF messaging; B3 default citation edges + threshold; B4 overlap
  markers; B1 viz help; B6 `index_only` server stub + agent Help tab (migration 0054); B7 weighted
  per-paper reference graph — plus the B7 extension (selectable Y axis, commits `62b8071` / `b24e2bc`).

## `WORKPLAN_B1_AND_ISSUES.md` (2026-07-01, "planning only" — subsequently absorbed & built)
Verified done in code: default/Inbox shelf (`access_settings.default_shelf_id`); main-file
(`works.main_file_id`, migration 0038); force-OCR (`extraction.force_ocr` + ocrmypdf); tabbed admin;
arrow-key tab nav; search own tab; topic graph + fcose layout; multimode embeddings; topic modeling;
batch-import Preview; **per-file detach** (`DELETE /works/{id}/files/{file_id}` + `removeFile` UI,
verified 2026-07-08). *(Only the Ollama pull-progress bar remains as an open polish item — see
`WORKPLAN.md` L6.)*

## `READY_FULL_WARNINGS_ASSESSMENT.md` — `make ready-full` warning triage
- **Done:** W1 echarts `^5.5.1`→`^6.1.0` (0 vulns); W2 pgvector `vector` type recognized in reflection
  (via a `pgvector` runtime dep + `import pgvector.sqlalchemy` in `app/db/base.py`); W3 jsdom
  navigation log silenced (by strengthening the export-test assertion); W8 Makefile `PY_PATHS`
  deduped.
- **Owner-decided SKIP:** W4/W5 `frontend/.npmrc`, W6 Vite `chunkSizeWarningLimit`, W7 baked-restore
  message.

## Notes carried forward
- **CSL styles are real** (citeproc-py + 7 bundled `.csl` files, commit `cc33525`); earlier "future"
  notes in `WORKPLAN.md`/`WORKPLAN_NEXT.md` were stale.
- **ML extraction (Nougat/Marker/`full_ml`) was intentionally de-scoped** (AUDIT D35), not left
  pending; only `umap-learn` survives, for the opt-in embedding-cluster layout.
- The stale `Co-Authored-By: Claude Opus 4.8` commit-trailer instruction in
  `WORKPLAN_B1_AND_ISSUES.md` is **superseded** — current policy is no Co-Authored-By trailer.

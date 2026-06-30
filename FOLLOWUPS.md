# Stage 8/9 follow-ups — detail (scratch, untracked)

> Working notes on the four items I flagged as "seams/approximations rather than the full thing."
> For each: **Intended** (what the full feature should do), **Now** (what's actually in the code
> today), **Needs** (what to build to close the gap). Deliberately not committed.

---

## 1. Citation styles (CSL / citeproc)

**Intended.** Render bibliographies and in-text citations in *real* Citation Style Language styles —
the same engine Zotero/Pandoc use. A user picks any of the ~2,600 CSL styles (APA 7th, IEEE, Nature,
Chicago author-date, a specific journal's style, …); the output is correct down to punctuation,
author-count truncation ("et al."), title casing, locale-specific terms ("and"/"&", "pp."), and
disambiguation. We already emit CSL-JSON (the standard interchange), so the data side is ready to
feed a real processor.

**Now.** A hand-rolled approximation of **three** styles only.
- `backend/app/services/export_service.py` → `render_styled()` / `_styled_entry()` builds APA-/IEEE-/
  Chicago-*like* strings by hand (`STYLES = ("apa", "ieee", "chicago")`).
- Exposed as the `styled` export format (`FORMAT_MEDIA`) + the request's `style` field; the frontend
  `ExportDialog` shows a style picker (`CITATION_STYLES` in `client.ts`).
- `citeproc-py` is already a declared dependency (`backend/requirements.txt`) but is **not used** for
  this — the approximations are pure Python string-building. They're "close enough to read," not
  citation-accurate, and only three styles exist.

**Needs.**
- Bundle (or fetch) CSL **style files** (`*.csl` XML) and a **locale** file (`locales-en-US.xml`).
  Options: vendor a curated handful into the repo, or add the `citeproc-py-styles` package which
  ships the full CSL styles repo (~tens of MB).
- Wire `citeproc-py`: build a `CitationStylesStyle(style_path)` + `CitationStylesBibliography` over a
  `CiteProcJSON` source built from our existing CSL-JSON renderer (`_render_csl_json`), render the
  bibliography (and optionally in-text cites).
- Replace `render_styled()` to delegate to citeproc; keep the hand-rolled path only as a no-style
  fallback. Surface the available style list to the GUI (so the picker isn't hard-coded to 3).
- Tests: golden-output tests for a couple of styles; handle missing/invalid style id (400).
- Decide where style files live and how they're updated (a CSL styles version pin).

---

## 2. ML extraction backend (Nougat / Marker)

**Intended.** For hard or scanned PDFs that GROBID handles poorly, route extraction to an ML model
(Meta's **Nougat** or **Marker**) that turns the PDF into structured markdown/text, then feed that
into the same metadata/reference/preview persistence path. Selectable + downloadable from the same
Admin "AI & Models" panel as the other providers, with availability detection and model caching;
ideally auto-chosen when `needs_ocr`/low text-layer quality is detected.

**Now.** Only the **seam + detection** — no actual ML extraction happens.
- `backend/app/core/config.py` → `extraction_backend: str = "grobid"` setting exists.
- `backend/app/services/model_management.py` → `detect_providers()` reports an `"extraction"` group
  with `grobid` (always) and `nougat`/`marker` (importable?), each with an "install in the AI image
  extra" hint.
- **The worker still always uses GROBID.** `backend/app/workers/jobs.py::extract_pdf_job` calls
  `GrobidClient` unconditionally; `extraction_backend` is never read there. There is no Nougat/Marker
  code path, no model download wired for them, no routing.

**Needs.**
- An **extractor interface** (e.g. `extract(pdf_path) -> structured text/TEI-ish`) with a GROBID
  implementation (today's) and Nougat/Marker implementations (opt-in imports).
- `extract_pdf_job` selects the backend from the effective config (extend `ai_config` to include
  `extraction_backend`, mirroring the other providers) and/or from document signals (`needs_ocr`,
  `text_layer_quality` already on the `File` model) with graceful fallback to GROBID.
- Map ML output (markdown) into our persistence: either a markdown→TEI adapter or a parallel storage
  path; references/metadata extraction from markdown is non-trivial (GROBID gives structured TEI for
  free).
- Heavy packages (`nougat-ocr`/`marker-pdf` + torch) ship in the opt-in AI image extra; model weights
  download via the existing pull mechanism (extend `model_management.pull_model` for these).
- Add an `"extraction"` selector to the Admin AI panel; tests with a fake extractor.

---

## 3. pgvector ANN (H7)

**Intended.** Sub-linear nearest-neighbour search at scale via a pgvector **ANN index**
(`hnsw`/`ivfflat`), making semantic search fast on large libraries, and making a real fixed-dim
embedding model the default. An ANN index requires a **fixed-dimension** `vector(d)` column.

**Now.** A correct but **un-indexed, exact** pgvector path, default off.
- `backend/alembic/versions/0019_pgvector.py` creates the `vector` extension and adds an
  **unconstrained** `embeddings.vector_pg vector` column (no dimension → accepts any provider's dim).
- `backend/app/services/semantic_search.py`: when `pgvector_enabled` (default **off**,
  `core/config.py`), indexing dual-writes `vector_pg` and search ranks with `ORDER BY vector_pg <=>
  CAST(:q AS vector)`. This pushes the distance computation into Postgres (faster than pulling all
  rows into Python) but is still an **O(n) sequential scan** — no ANN index, because an unconstrained
  column can't be indexed.
- No new Python dependency (raw-SQL `CAST(... AS vector)`). Falls back to JSON + Python cosine on
  SQLite or any error. Verified by `backend/tests/test_pg_integration.py::test_pgvector_ranking_when_enabled`.

**Needs.**
- Commit to a **fixed embedding dimension** (i.e. a default real model, e.g. MiniLM = 384-dim), then
  migrate `vector_pg` to typed `vector(384)` and create
  `CREATE INDEX ... USING hnsw (vector_pg vector_cosine_ops)`.
- Handle **multi-model coexistence / dimension changes**: today vectors of different dims share one
  column (queries filter by `model_name` first, so comparisons stay same-dim). A typed/indexed column
  forces one dim — need either per-model columns/tables or "active model only gets the index," plus a
  reindex/migration when the model (hence dim) changes.
- Make pgvector the default once a real model ships (Stage 6 left `hash_bow` as default); decide ops
  story (the `pgvector/pgvector` image is already in `docker-compose.yml`, so the extension is
  available).
- Benchmarks + a recall/latency test; tune `hnsw` params.

---

## 4. End-to-end testing

**Intended.** Browser-level E2E that drives the **actual UI** (Svelte frontend + backend together):
log in through the form, import a PDF, open the reader, edit metadata, organize into a shelf, export
— catching breakage the API tests can't (routing, rendering, wiring, the things a real user clicks).

**Now.** An **API-level** happy path only.
- `backend/tests/test_e2e_happy_path.py` drives the flow over HTTP through the FastAPI app
  (login → create works → reindex + semantic/lexical search → styled selection export →
  `paper.viewed` audit). No browser, no Svelte components exercised.
- Frontend tests are component/unit-level `vitest` (`frontend/src/**/*.test.ts`), not full-journey.

**Needs.**
- A **Playwright** (or Cypress) harness with a headless browser, pointed at a running stack (compose),
  with seeded data / a test user.
- Tests for the critical journeys: sign-in, import (PDF upload + arXiv/DOI), library master–detail
  edit, reader open + annotate, shelves/racks organize, duplicates review, export, Admin AI panel.
- CI wiring (browser install, stack up, artifacts on failure). Decide fixtures/reset between tests.

---

## Quick status table

| Item | Implemented now | Gap to "full" |
|------|-----------------|---------------|
| CSL styles | 3 hand-rolled styles via `styled` export | real citeproc-py + bundled `.csl`/locale, all styles |
| ML extraction | config flag + capability detection | actual Nougat/Marker extractor + worker routing + output mapping |
| pgvector | exact `<=>` over unconstrained column, default off | fixed-dim typed column + HNSW index + default-on + model/dim story |
| E2E | API happy-path test | Playwright browser journeys + CI |

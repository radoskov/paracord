# Workplan — B1 topic modeling + 24 issues

Status: **planning** (no code written yet). Created 2026-07-01.

This plan turns the **B1 topic-modeling decision** and **24 reported issues** into an
ordered, dependency-aware set of rounds. It is grounded in a read of the actual code
(file:line references are current as of this date). Each item lists: the **problem**, the
**root cause**, the **plan**, **files touched**, and **acceptance criteria**.

Companion docs: `HYBRID-SEARCH-DESIGN.md`, `WORKPLAN_HYBRID_SEARCH.md`, `B1-B3-ML-DEPTH.md`.

---

## Locked decisions (from design discussion)

- **B1**: make the `embedding` topic backend *real* (mean-pool chunk vectors → k-means).
  **Keep** `tfidf` keyword clustering as a selectable option. **Defer** BERTopic (keep the
  enum value + an honest "not installed" note; do not implement now).
- **#1 Default shelf**: a *real* shelf with an **admin-configurable access level**;
  membership is **ephemeral** (auto-removed once a paper joins any other shelf).
  **Retroactively** migrate today's loose papers onto it. When a paper is removed from every
  real shelf, it **falls back onto the default shelf** — papers are never free-floating.
- **#9 Tab caching**: **Option A** — keep all tabs mounted, hide inactive ones with CSS.
  Two mandatory caveats: (1) gate any polling/interval on "am I the active tab";
  (2) call Cytoscape `resize()` + re-layout when a graph tab becomes visible.
- **#21 Embeddings**: **dynamic per-model registry**. Admins pull any embedding model; each
  model gets its own **dimension-constrained pgvector column** created by **runtime DDL**
  (`ALTER TABLE … ADD COLUMN vec_<slug> vector(<dim>)` + HNSW index), tracked in a registry
  table. A **slug allowlist** + **max-model cap** bound it; models (and their columns/indexes)
  can be **deleted** so the cap is never a dead end. Users pick **any single model** *or*
  **"multimode"** (RRF fusion across all available model columns) for **both search and
  clustering**.

---

## Round map (dependency order)

| Round | Theme | Items | Depends on |
|------|-------|-------|-----------|
| 0 | Test hygiene | #11 | — |
| 1 | Embedding foundation | #2, #21 | — |
| 2 | B1 topic modeling | B1, #18 | 1 |
| 3 | Frontend tab foundation | #9, #23 | — |
| 4 | Search quality + UX | #20, #3 | 1, 3 |
| 5 | AI summaries | #10 | — |
| 6 | Graphs | #6, #7, #8 | 1 |
| 7 | Organization | #1, #13, #14, #15 | — |
| 8 | Paper view + files | #16, #17 | — |
| 9 | Find-on-web + model UX | #4, #5, #19, #12 | 3 |
| 10 | Admin + OCR visibility | #24, #22 | — |

Rounds 3, 5, 7, 8, 9, 10 are largely independent and can be parallelized after Round 1.
Round 2 needs Round 1; Round 4 needs Rounds 1 and 3; Round 6 needs Round 1.

---

## Round 0 — Test hygiene

### #11 — AI tests assert Ollama is unavailable and fail when it's running
- **Root cause**: `backend/tests/test_ai_admin.py:48` asserts
  `body["embedding"]["ollama"]["available"] is False` and `:76` asserts
  `ollama_reachable is False`. These hardcode a CI-without-Ollama world; a running daemon
  flips them.
- **Plan**: mock the Ollama reachability probe (patch the `/api/tags` / reachability call) so
  both tests are deterministic regardless of host. Add a second parametrization asserting the
  **reachable** branch too (so we cover both states).
- **Files**: `backend/tests/test_ai_admin.py`; possibly a small fixture in `conftest.py`.
- **Acceptance**: both tests pass whether or not a local Ollama daemon is running.

---

## Round 1 — Embedding foundation

This round replaces the hardcoded 2-model scheme (`vec_minilm`, `vec_nomic`) with a
registry, and fixes the silent-failure bugs. It unblocks B1 (Round 2), search (Round 4),
and the topic graph (Round 6).

### #2 — Ollama semantic search fails silently
- **Root cause**: `OllamaProvider` (`backend/app/services/embeddings.py:88-102`) sends the raw
  model name to `/api/embeddings` (`nomic-embed-text` misses Ollama's registry vs
  `nomic-embed-text:latest`); any error is caught at `:150` and **silently** degraded to
  hash-BOW. Empty model → hardcoded `nomic-embed-text` default (`:148`). No check that the
  model is embedding-capable, so `qwen` 500s then also degrades silently.
- **Plan**:
  1. **Tag normalization**: if the model name has no `:`, append `:latest` before calling.
  2. **Pre-validate**: on provider init (or model selection in admin), query `/api/tags`;
     if the model is absent, raise a clear error instead of degrading.
  3. **Embedding-capability check**: attempt a 1-token probe embed at selection time; if the
     endpoint 500s (generation-only model like `qwen`), reject the selection with a message.
  4. **Surface degradation on reindex**: `search.py` reindex endpoint currently returns
     `{"indexed", "status}` with no provenance — add `degraded`/`degraded_reason`
     (`ResolvedEmbeddingProvider` already carries these; the search-query path already
     surfaces them — mirror it here).
  5. Fix the misleading "provider default" label in the admin UI (show the resolved model).
- **Files**: `backend/app/services/embeddings.py`, `backend/app/services/semantic_search.py`,
  `backend/app/api/v1/endpoints/search.py`, `backend/app/services/ai_config.py`, and the
  admin AI panel (`frontend/src/components/AiModelsPanel.svelte`).
- **Acceptance**: selecting `nomic-embed-text` (no tag) works; selecting `qwen` is rejected
  with a clear message at selection time; reindex reports degradation; empty field shows the
  actual resolved model, not a misleading "provider default".

### #21 — Dynamic multi-model embedding registry + multimode RRF
- **Root cause / gap**: models are hardcoded — `CHUNK_MODEL_COLUMNS`
  (`backend/app/services/chunk_embeddings.py:23-26`) + fixed migration
  `0035_work_chunk_vectors.py`. No way to add a model without code + a migration; no
  multimode.
- **Plan**:
  1. **Registry table** `embedding_models` (migration): `slug` (PK, `[a-z0-9_]` allowlisted),
     `model_name`, `provider`, `dim`, `column_name`, `created_at`, `active`. Seed with the two
     existing models so nothing is lost.
  2. **Runtime provisioning service**: on first index of a new model, run best-effort DDL
     (Postgres only, mirroring `0035`): `ALTER TABLE work_chunks ADD COLUMN vec_<slug>
     vector(<dim>)` + `CREATE INDEX … USING hnsw (vec_<slug> vector_cosine_ops)`, then insert
     the registry row. Guard with: slug allowlist (SQL-injection-safe interpolation, reuse the
     `_ALLOWED_COLUMNS` whitelist pattern), a **max-model cap** (config, e.g. 6), and a
     collision check.
  3. **Deletion**: admin action drops the column + HNSW index + registry row (Postgres DDL,
     best-effort), freeing a cap slot. Also clears document-level `Embedding` rows for that
     model.
  4. **`chunk_column_for(model_name)`** and `CHUNK_MODEL_COLUMNS` become **registry-backed
     lookups** instead of a literal dict.
  5. **Multimode RRF**: a search/cluster mode that runs the semantic query against **every**
     active model column and fuses per-model rankings with RRF (reuse `hybrid_search._fuse`
     shape). Users pick a single model **or** `multimode`.
  6. Config: `max_embedding_models` cap; admin endpoints to list/add(pull+register)/delete.
- **Files**: new migration + `backend/app/services/chunk_embeddings.py`,
  `backend/app/services/semantic_search.py`, `backend/app/services/hybrid_search.py`,
  new `backend/app/services/embedding_registry.py`,
  `backend/app/api/v1/endpoints/search.py` + admin AI endpoints, `AiModelsPanel.svelte`,
  `InsightsPage.svelte`/new search tab (model selector incl. "multimode").
- **Risks/guards**: runtime DDL runs outside Alembic — must be idempotent (`IF NOT EXISTS`),
  wrapped in try/except with logging, Postgres-only (SQLite path stays doc-level), and never
  block a request. Cap + allowlist + deletion prevent unbounded/hostile column growth.
- **Acceptance**: admin pulls `mxbai-embed-large`, indexes, and it becomes a selectable search
  model; deleting it drops the column/index and frees a slot; "multimode" returns fused
  results over all active models; SQLite tests still pass (degrade to doc-level).

---

## Round 2 — B1 topic modeling (real embeddings)

### B1 + #18 — `embedding` topic backend actually uses embeddings
- **Root cause**: `_model_topics_embedding` (`backend/app/services/topic_modeling.py:~322`)
  runs **TF-IDF + k-means** and only echoes `embedding_model` for provenance — so #18
  ("can't use Ollama for topics") is a *bug*, not a feature limit.
- **Plan**:
  1. `embedding` backend: for each paper in scope, **mean-pool its chunk vectors** (from the
     selected model's registry column, Round 1) into a paper-level vector; run the existing
     deterministic k-means on those dense vectors. Support `multimode` (mean-pool per model,
     then either concatenate or fuse cluster assignments — start with per-model then RRF-style
     consensus; document the choice).
  2. Keep **TF-IDF top terms** only for human-readable **cluster labels**.
  3. **Keep `tfidf` backend** exactly as-is (selectable option).
  4. **BERTopic**: keep the enum value; return an honest "not installed / deferred" note
     rather than silently aliasing to TF-IDF (current behavior is misleading).
  5. Fallback: if no real embeddings exist (default hash-BOW, no chunk columns), fall back to
     TF-IDF and **say so** in the response (`backend`/`degraded` field).
- **Files**: `backend/app/services/topic_modeling.py`,
  `backend/app/api/v1/endpoints/ai.py` (widen/adjust `backend` handling, model selector),
  tests.
- **Acceptance**: with nomic (or MiniLM) active, `embedding` clustering differs from `tfidf`
  and is driven by dense vectors; `tfidf` still works; BERTopic returns a clear deferred note;
  hash-BOW falls back to TF-IDF with an explicit flag.

---

## Round 3 — Frontend tab foundation

### #9 — Tab state lost on switch (Option A)
- **Root cause**: `App.svelte:183-207` uses `{#if}/{:else if}` so inactive tabs **unmount**
  (state, scroll, results destroyed).
- **Plan**: render all tabs and toggle visibility with a `hidden`/`display:none` class instead
  of `{#if}`. Add the two caveats: (1) a `activeTab` store; each polling loop checks it before
  fetching; (2) graph components call Cytoscape `resize()` + re-run layout in an effect that
  fires when they become visible. Lazy-mount on first visit, then keep mounted. Reset on
  logout/close is acceptable (per requirements).
- **Files**: `frontend/src/App.svelte`, graph components (`CitationGraph.svelte`, new topic
  graph), any component with `setInterval`/polling (jobs panel).
- **Acceptance**: switching away and back preserves search results, modeled topics, scroll,
  and open modals; graphs re-fit correctly; background polling pauses on inactive tabs.

### #23 — Arrow-key tab navigation
- **Plan**: keyboard handler on the tab bar (Left/Right to move between visible, role-filtered
  tabs; respect input focus so typing in fields isn't hijacked).
- **Files**: `frontend/src/App.svelte`.
- **Acceptance**: Left/Right arrows cycle tabs when focus isn't in a text field.

---

## Round 4 — Search quality + UX

### #20 — Unstable percentages + missing section scores
- **Root cause**: no normalization. Lexical = unbounded BM25 sum (`bm25_index.py:246-270`) →
  shows >1000%; semantic = cosine [0,1]; hybrid = raw RRF (~0.016–0.03) → shows 1–3%
  (`search.py:127-132`). Per-section BM25F contributions are collapsed to one scalar
  (`bm25_index.py:256-270`) and never returned.
- **Plan**:
  1. **Normalize for display per mode**: semantic → `score×100` (already [0,1]); lexical →
     min-max normalize within the result set (top hit = 100%) *or* show a raw relevance bar,
     not a "%"; hybrid → normalize fused RRF within the result set. Label consistently as a
     **relative relevance** bar, not an absolute probability.
  2. **Expose per-section scores**: track per-field contributions in the BM25F index and
     return them so the UI can show which section(s) matched (chunk-level semantic already
     returns `section` — `chunk_search.py:102-105`).
- **Files**: `backend/app/services/bm25_index.py`,
  `backend/app/services/hybrid_search.py`, `backend/app/api/v1/endpoints/search.py`, search UI.
- **Acceptance**: all three modes show sane, comparable relevance bars; results display a
  section indication.

### #3 — Semantic search: own tab + clickable, actionable, persistent results
- **Root cause**: search lives inside `InsightsPage.svelte:296-341`; results are read-only
  `<strong>` text with no actions; re-runs on each mount.
- **Plan**:
  1. Promote search to its **own top-level tab** (add to `App.svelte` `TABS`).
  2. Make each result **clickable** with actions: view metadata & citations, **jump to paper
     in Library**, **add to shelf**, **open in reader** (reuse `PdfReader.svelte`).
  3. **Persist** query + results via Round 3 (kept mounted) so tab switches don't re-run.
  4. Add the model selector (single model / multimode) from Round 1 and the mode selector
     (lexical/semantic/hybrid).
- **Files**: `frontend/src/App.svelte`, new `SearchPage.svelte` (extracted from Insights),
  reuse `PdfReader.svelte`, selection/shelf stores.
- **Acceptance**: search is its own tab; clicking a result offers metadata/citations, jump,
  add-to-shelf, and read; results survive tab switches.

---

## Round 5 — AI summaries

### #10 — Scope summary always "extractive"
- **Root cause**: `summarize_scope` is hardcoded to extractive
  (`summarization.py:347,361`); endpoint only accepts `Literal["extractive"]` (`ai.py:34`).
  A working `local_llm` Ollama branch already exists for **work-level** summaries
  (`summarization.py:218-238`).
- **Plan**: port the `local_llm` branch to `summarize_scope` (combine abstracts → `_ollama_
  summarize` → fall back to extractive on error); widen the endpoint `Literal` to
  `"extractive" | "local_llm"` (and `"abstract"` if trivially supported); reuse the LLM-model
  selection + capability guard from Round 1.
- **Files**: `backend/app/services/summarization.py`,
  `backend/app/api/v1/endpoints/ai.py`, tests.
- **Acceptance**: with an Ollama LLM configured, scope summaries are LLM-generated (not
  "extractive"), with graceful fallback + a clear model_name in the response.

---

## Round 6 — Graphs

### #6 — Topic / embedding-similarity graph
- **Gap**: only a citation graph exists; embeddings are exposed only as a per-paper "related"
  list (`semantic_search.py:206-249`), not a graph.
- **Plan**: new endpoint building a similarity graph over a scope — nodes = papers, edges =
  **inverted semantic distance** between paper vectors (mean-pooled chunk vectors, selected
  model or multimode). Use **kNN** (each paper → top-K neighbors above a similarity threshold)
  to keep it sparse; edge weight = similarity. Render with the same Cytoscape component
  (shared with #8 upgrades).
- **Files**: new `backend/app/services/topic_graph.py` (or extend `citation_graph.py`
  patterns) + endpoint in `graph.py`; frontend graph component (shared).
- **Acceptance**: a topic graph groups semantically-similar papers with weighted edges;
  respects visibility (`visible_work_ids`).

### #7 — Hide singletons (both graphs)
- **Root cause**: all nodes rendered, including degree-0 (`citation_graph.py:95`).
- **Plan**: a **toggle** (frontend, default on) that filters nodes with no edges (and, for
  the topic graph, single-node clusters). Prefer client-side filtering so it's instant and
  works for both graph types.
- **Files**: `CitationGraph.svelte` / shared graph component; optional backend flag.
- **Acceptance**: toggling "hide singletons" removes isolated nodes from both graphs.

### #8 — Graph layout + interactivity upgrade
- **Root cause**: `cose` force layout melts down with `include_external` (hundreds of leaf
  "star" nodes hanging off single papers); no hover tooltips (`CitationGraph.svelte`).
- **Plan**:
  1. Switch force layout to **`fcose`** (Cytoscape extension; far better on large/sparse
     graphs). Keep circle/grid/hierarchy options.
  2. **Cap/cluster external nodes**: e.g. collapse a paper's external citations beyond N into
     a single "+K external" node, or hide external leaves by default with a toggle.
  3. **Hover tooltips**: show title/year/venue/DOI on node hover (data already on nodes:
     `label`, `year`, `doi`, `deg`).
  4. **Click actions**: local node → jump to paper in Library (already wired); external node →
     offer **import** (find-on-web / batch import lookup by DOI/arXiv).
- **Files**: `frontend/package.json` (add `cytoscape-fcose`), `CitationGraph.svelte` (+ shared
  graph component), possibly `citation_graph.py` for external capping.
- **Acceptance**: external-citation graphs are readable; hovering shows metadata; clicking an
  external node offers import.

---

## Round 7 — Organization (shelves, racks, library)

### #1 — No free-floating papers: ephemeral default shelf
- **Root cause**: today a paper on no shelf is treated as **open** (`access.py:174-181,
  244-284`); no default-shelf concept exists.
- **Plan**:
  1. **Default shelf**: create a real shelf (migration/bootstrap). Store its id +
     configurable access level in the `AccessSettings` singleton
     (`access_settings.py`; add `default_shelf_id`). Admin tab sets its access level.
  2. **Auto-place new papers**: hook `create_work` (`works.py:553-576`) and
     `commit_drafts` (`batch_import.py`) so a paper with no explicit shelf is added to the
     default shelf.
  3. **Ephemeral membership**: when a paper is added to any *other* shelf, remove it from the
     default shelf (a shared helper invoked by all add-to-shelf paths).
  4. **Fallback on removal**: when a paper is removed from its last real shelf, re-add it to
     the default shelf (never free-floating).
  5. **Retroactive migration**: one-time — place all current loose papers onto the default
     shelf.
  6. Once no free-floating papers can exist, the `loose → open` branches become a safety net;
     keep them but document that they should not trigger in normal operation.
- **Files**: migration + `backend/app/models/access_settings.py`,
  `backend/app/services/access_settings.py`, `backend/app/services/access.py`,
  `backend/app/api/v1/endpoints/works.py`, `backend/app/services/batch_import.py`, a shared
  shelf-membership helper (likely in `organization` service), admin UI.
- **Acceptance**: new papers land on the default shelf; adding to another shelf removes them
  from default; removing from all shelves re-adds to default; existing loose papers are
  migrated; changing the default shelf's access level changes new papers' visibility.

### #13 — Clickable shelves/papers to jump
- **Plan**: in "papers in this shelf" and "shelves in this rack" lists, make rows navigate —
  paper → Library tab (open detail); shelf → Shelves tab (select). Reuse selection stores.
- **Files**: `ShelvesPage.svelte`, `RacksPage.svelte`, `App.svelte` (cross-tab navigation).
- **Acceptance**: clicking a paper/shelf jumps to the right tab with it selected/open.

### #14 — Search bar within shelf/rack views
- **Plan**: a simple client-side **lexical title filter** in the shelf view (filter papers)
  and rack view (filter shelves).
- **Files**: `ShelvesPage.svelte`, `RacksPage.svelte`.
- **Acceptance**: typing filters the in-view list by title substring.

### #15 — Library columns: shelves + racks
- **Plan**: add `shelves` and `racks` columns to the registry (`columns.ts:33-41`), rendering
  a comma-separated list. Backend: include shelf/rack membership in the library list payload
  (or a lightweight join) respecting visibility.
- **Files**: `frontend/src/lib/columns.ts`, `PaperTable.svelte`, library list endpoint +
  serializer.
- **Acceptance**: optional "Shelves" and "Racks" columns show each paper's memberships.

---

## Round 8 — Paper view + files

### #16 — Select main file + quick-read button
- **Root cause**: no "main file" concept; reader falls back to `files[0]`
  (`WorkDetail.svelte:524`).
- **Plan**: add a `main_file_id` on the work (nullable; defaults to first added file). A
  **"Read"** button below the title opens the main file in `PdfReader` (+ a "New tab" next to
  it). Keep the per-file Read/New-tab buttons for individual files. Add a "Set as main file"
  action in the file list.
- **Files**: migration (works.main_file_id), `works.py` endpoint, `WorkDetail.svelte`.
- **Acceptance**: a Read button by the title opens the main file; main file is selectable and
  persists; per-file read still works.

### #17 — Remove file
- **Root cause**: no remove-file control (`WorkDetail.svelte:749-784` has attach/read/
  re-extract only).
- **Plan**: per-file **Remove** button → DELETE endpoint detaching the file from the paper
  (confirm; respect modify permission; handle main-file reassignment if the removed file was
  main). Decide retention (detach vs delete stored blob) — default detach, keep blob per
  existing "files stay in library" design unless orphaned.
- **Files**: file/works endpoint, `WorkDetail.svelte`.
- **Acceptance**: a file can be removed from a paper with confirmation; main-file pointer
  updates if needed.

---

## Round 9 — Find-on-web + model UX

### #4 — Cache the find-on-web popup
- **Root cause**: `WorkDetail.svelte:275-426` resets state on open and re-runs the search
  every time.
- **Plan**: cache results **per paper** in a store keyed by work id; reopening the same paper
  shows cached results without re-running. Reset only when (a) find-on-web is run on a
  *different* paper, or (b) the user clicks an explicit **Reset** button in the popup.
- **Files**: `WorkDetail.svelte`, a find-on-web results store.
- **Acceptance**: reopening find-on-web on the same paper shows prior results instantly; a
  Reset button forces a fresh search.

### #5 — Pull-model progress/status
- **Root cause**: `AiModelsPanel.svelte:148` queues a job and just says "watch the Jobs tab";
  no progress. Ollama's `/api/pull` streams progress.
- **Plan**: stream pull progress (backend proxies Ollama `/api/pull` stream, or the job
  reports percent) and show a progress bar/status in the panel.
- **Files**: backend pull endpoint/job, `AiModelsPanel.svelte`.
- **Acceptance**: after "Pull model", a live progress/status indicator appears until complete.

### #19 — Click Ollama model name to copy
- **Root cause**: model names are plain `<span>` (`AiModelsPanel.svelte:359-366`).
- **Plan**: make the name clickable → copy to clipboard with a brief "copied" confirmation
  (reuse the hash copy-to-clipboard pattern already in `WorkDetail`).
- **Files**: `AiModelsPanel.svelte`.
- **Acceptance**: clicking a model name copies it.

### #12 — Batch import Preview stays disabled even with input — DONE
- **Root cause (real)**: Svelte reactivity trap. `disabled={loading || !inputLines().length}`
  (`BatchImport.svelte:154`) calls a function that reads `text` *inside its body*. A template
  expression only tracks variables it references **directly**, so `disabled` never
  re-evaluated when `text` changed — it computed once at mount (empty → disabled) and stuck.
  (Not a "missing hint" — the button was genuinely unusable.)
- **Fix applied**: replaced the `inputLines()` function with a reactive `$: lines = text…`
  declaration that references `text` directly, and used `lines` in both the `disabled` binding
  and `preview()`. Now typing enables Preview.
- **Files**: `frontend/src/components/BatchImport.svelte`.
- **Follow-up (optional)**: still worth adding an explicit hint ("Paste one citation per line")
  for empty state.

---

## Round 10 — Admin + OCR visibility

### #24 — Tabbed admin
- **Root cause**: `AdminPage.svelte` is one long page with sections (users, agents, folders,
  hosts/policy, groups).
- **Plan**: reorganize into tabs: **Users**, **Groups**, **Find-on-web** (hosts + policy),
  **Folders** (server import folders), **Agents**. Sub-tab component reused from the main tab
  pattern.
- **Files**: `frontend/src/pages/AdminPage.svelte` (+ maybe split into sub-components).
- **Acceptance**: admin is tabbed with the five sections.

### #22 — OCR visibility + manual trigger
- **Root cause**: OCRmyPDF *is* wired and auto-runs when `needs_ocr(text_layer_quality)` and
  the `ocrmypdf` CLI is present (`extraction.py:209`), but there's **no UI feedback**; a
  scanned PDF staying textless means either the CLI isn't in the image or the quality wasn't
  classified as poor/none.
- **Plan**:
  1. Verify `ocrmypdf` is installed in the backend image; if not, add it (document the dep).
  2. **Surface OCR status** per file (ran / skipped-had-text / unavailable / failed) in the
     file list and extraction result.
  3. Add a **"Force OCR"** manual action (re-run extraction with OCR forced regardless of
     `needs_ocr`).
- **Files**: `backend/app/services/ocr.py`, `backend/app/services/extraction.py`,
  extraction/works endpoint, `WorkDetail.svelte`.
- **Acceptance**: the UI shows whether OCR ran; a scanned PDF can be force-OCR'd; if the CLI
  is missing, the UI says so instead of silently doing nothing.

---

## Cross-cutting: process + testing

- **Git**: commit to `main` (no branch); explicit `git add` of specific files (not `-A`);
  leave `NOTES.md` untracked; commit messages end with
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Security invariants** (unchanged): keep `httpx2==2.4.0`; shadow-library denylist +
  internal-IP SSRF guard; agent rejects server-supplied paths; visibility clamps on all
  work-returning endpoints; single immutable owner; bcrypt.
- **Runtime DDL (#21)**: Postgres-only, idempotent, best-effort, never blocks a request;
  SQLite tests must still pass by degrading to the doc-level path.
- **Tests**: SQLite for logic; Postgres-only markers for ANN/pgvector; Python-BM25 tests are
  DB-agnostic. Add tests per item's acceptance criteria. Mock external daemons (Ollama) so CI
  is deterministic (#11).
- **UI copy**: user-facing text uses "paper(s)", not "work".

## Suggested execution order

Round 0 → Round 1 → Round 2 in sequence (they build on each other). Rounds 3, 5, 7, 8, 9, 10
can proceed in parallel after Round 1. Round 4 after Rounds 1+3. Round 6 after Round 1.
Land each round as its own commit (or a small series) with tests green before moving on.

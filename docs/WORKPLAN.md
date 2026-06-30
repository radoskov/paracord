# PaRacORD — Work Plan (2026-06-29)

This is the **execution-ordered** plan for finishing the app. It supersedes the loose "Next
recommended items" list that previously lived in `PROGRESS.md`. It reconciles three inputs:

1. `SPECIFICATION.md` (§20 milestones M0–M8) — the destination.
2. `docs/AUDIT.md` (2026-06-25 base + 2026-06-26 addendum) — findings, re-validated below.
3. The actual code at `HEAD` (validated 2026-06-29) — what is really done.

**Governing principle (per maintainer):** drive *steady progress toward a fully functional app*.
Front-load the work that unblocks whole feature areas; **defer minor polish, micro-optimizations,
and fine-tuning to the end** (Stage 7) so engineering time is not lost tinkering with non-blocking
details. Each stage below lists a concrete *Definition of Done*; several map onto the skipped
acceptance contracts already in `backend/tests/future/` — enabling those tests is the completion
signal.

---

## Audit re-validation snapshot (2026-06-29)

Verified against the current tree (not the audit's original commit). See the table at the bottom
of `docs/AUDIT.md` for the same data in audit-ID order.

**Resolved since the audit was written:** C1, C2 (migration parity), C3 (core FKs), C4 (audit
JSONB), C5 (docker dev/prod targets), H1 (`httpx2==2.4.0`), H4 (agent stub auth → 501/410), H5
(prod build), P1/item4 (`arxiv_base_id` + unique indexes), P1/item5 (DOI normalization + SQL
pushdown), P2/item6 (nav shell + Admin UI), P2/item9 (scope summaries), P2/item10-partial (PDF
upload + identifier import frontend + backend).

**Done so far:**
- **A1** ✅ managed-path extraction fix · **A3** ✅ `make ready`/`ci` mirror CI → Stage 1
- **B1** ✅ GROBID options config-driven + PDF coordinate extraction → Stage 2
- **PDF.js reader** ✅ and **Cytoscape graph** ✅ components → Stage 3
- **Frontend IA & UX overhaul** ✅ — tabbed shell, master–detail Library with work editing +
  metadata review + attach/open PDFs, explicit shelves/racks managers, RIS/CSL import,
  attach-file-to-work backend, tooltips/disabled-reasons/help → Stage 4

- **Agent manifest/teleport** ✅ — secure agent-push: manifest + hash-verified teleport into the
  managed library; raw-path helper removed → Stage 5

**Still open and scheduled below:**
- **H2** (AI read-path writes), embedding/topic/summary **provider interface**. → **Stage 6 (next)**
- **H3** fuzzy-title perf, **C3/C4** remaining edges, **H7** pgvector, export polish, auth
  hardening, security-doc truthfulness, backups, prod smoke, plus Stage-4 refinements
  (per-field `user_confirmed`, applied-tags listing, import-queue panel). → Stage 7 (deferred)

---

## Stage 1 — Correctness & CI integrity  *(small, do immediately)*

Cheap, high-leverage fixes that protect everything built afterward. A shipped feature (upload)
is currently broken by A1; fix it before building more on top.

1. **A1 — Managed-path extraction fix. ✅ DONE (2026-06-29).** Added the shared resolver
   `app/services/file_paths.py::resolve_backend_readable_pdf_path(db, *, file, settings)` —
   resolves `server_path` (validated against the server-folder source root) and `managed_path`
   (validated against `managed_library_root`), picking the primary available location and raising
   `FileLocationError` (a `ValueError` subclass with a `kind` flag → 403/404 at the API layer).
   `extract_and_store()` (previously `server_path`-only, with no root check) and
   `files.py::stream_file` both route through it. Regression test
   `test_extraction.py::test_extract_and_store_reads_managed_path`; full backend suite green
   (175 passed, 7 skipped).

2. **A3 — `make ready`/`ci` mirror CI.** Make readiness fail on frontend or migration regressions:
   ```makefile
   check:         lint test test-migrations
   frontend-check: frontend-install frontend-test frontend-build
   ready:         fix precommit check frontend-check
   ci:            lint test test-migrations frontend-check check-secrets
   ```
   *DoD:* `make ready` fails if the frontend build or migration parity fails; runbook documents
   exactly what `ready` covers.

---

## Stage 2 — Extraction depth  *(unblocks the reader, graph, and annotations)*

The single most leverage-rich backend item: real PDF coordinates are the prerequisite for the
PDF.js reader, anchored highlights, and citation→mention jumps.

3. **B1 — GROBID settings + coordinate extraction. ✅ DONE (2026-06-29).** GROBID options are
   now config-driven (`grobid_consolidate_header/_citations`, `grobid_include_raw_citations`,
   `grobid_segment_sentences`, `grobid_coordinate_elements`), read from the `processing.grobid:`
   YAML block; `GrobidClient` builds the form data (incl. repeated `teiCoordinates` fields) from
   settings — the hardcoded flags and the TODO are gone. `tei_parser` parses the `coords`
   attribute into `pdf_coordinates` (a list of `{page,x,y,w,h}` boxes, multi-box for line wraps),
   which replaced the four scalar `pdf_*` columns on `CitationMention` (migration
   `0013_citation_pdf_coordinates`, §9.3). The citation-context API exposes `pdf_coordinates`
   plus convenience `pdf_x/y/w/h` from the primary box. The acceptance test
   `test_future_grobid_coordinates_acceptance.py` was rewritten to be deterministic (fixture-driven
   through the real `extract_and_store` + HTTP read) and is now enabled. Backend suite: 179 passed,
   6 skipped; migration parity green on Postgres.

---

## Stage 3 — The real reader & interactive graph  *(biggest user-facing leap)*

`pdfjs-dist` and `cytoscape` are already in `package.json` but unused. This stage turns the
"debug console" into the intended reading application.

4. **PDF.js reader (replaces the `<iframe>`). ✅ DONE (2026-06-29).** `PdfReader.svelte` now
   renders pages to a canvas via `pdfjs-dist` (lazy-loaded chunk + bundled worker): page
   navigation, a thumbnail rail, zoom, and in-app full-text search (`getTextContent`, jump between
   matching pages). Citation contexts with `pdf_coordinates` draw a highlight overlay on their
   page; the References tab has a **Jump to p.N** control that scrolls the marker into view and
   flashes its box. Text selection in the page is captured into the Notes form with the page and a
   bounding-box `coordinates` payload (previously always null). Heavy imports are deferred so jsdom
   tests never load them; new `PdfReader.test.ts`.
   *Deferred to Stage 7:* a full ref→all-mentions back-index list (the marker↔reference jump and
   per-page overlay are in place).

5. **Interactive Cytoscape citation graph. ✅ DONE (2026-06-29).** `CitationGraph.svelte` now
   renders an interactive `cytoscape` canvas (lazy chunk): click-to-open nodes
   (`onOpenWork` → `selectWorkById`), selectable layouts (force/circle/grid/hierarchy), and node
   size ≈ citation degree (centrality proxy). Per maintainer guidance there is a **Graph ↔ List
   render-mode toggle**; the list renderer is the previous edge list and is also the automatic
   fallback where a canvas isn't available (jsdom/headless). `CitationGraph.test.ts` updated.
   *Deferred to Stage 7:* version-collapse and server-side scope limits / progressive rendering for
   very large (>few-thousand-node) graphs.

---

## Stage 4 — Frontend information architecture & UX overhaul  *(make the app usable in-vivo)*

> **Why this is now the priority (UI review, 2026-06-29).** The backend is broad but the
> frontend is still one ~10-section "operator console" that is genuinely confusing to use. Concrete
> problems observed in-vivo and confirmed in code (`LibraryPage.svelte`):
> - **Everything is on one page.** ~10 stacked sections (library, contexts, search, summaries,
>   duplicates, graph, topics, queue, files, reader) + a sidebar (new-work, sources, shelves,
>   racks, tags). Functionally distinct areas should be separate tabs/pages.
> - **A manually-created work is a dead end.** Selecting a library row loads contexts/annotations
>   /summaries but offers **no detail panel, no field editing, no way to attach a file** (upload
>   always mints its own work). Users can create a work and then do nothing with it.
> - **Organize controls feel broken.** Clicking a shelf chip silently sets *two* hidden states
>   (`selectedShelf` **and** the "Add work" target `selectedShelfForWork`), so a single selection
>   enables the adjacent **Archive shelf** *and* **Add work** buttons at once — which reads as
>   "Archive let me add a work." Same for racks. Selection has almost no visual feedback.
> - **No affordances.** No tooltips, no explanation of *why* a button is disabled, no empty-state
>   guidance, no per-area help text, no confirmation on destructive actions.
>
> This stage subsumes the former "Metadata review/edit UI" (P2/item8) and "RIS/CSL import"
> (P2/item10 remainder) — the metadata editing becomes part of the work-detail panel. It is
> frontend-heavy with one small backend addition (F). **No capability may regress** during the
> refactor; the current page is the source pool for components being moved.

> **Status: ✅ implemented (2026-06-29).** All of 6A–6F landed. The monolithic `LibraryPage`
> was decomposed into a tabbed shell (`App.svelte`) over per-area pages
> (`LibraryPage`/`ImportPage`/`ShelvesPage`/`RacksPage`/`TagsPage`/`DuplicatesPage`/
> `InsightsPage`/`AdminPage`); the Library is now a searchable master list + `WorkDetail` panel
> (edit, metadata-conflict review, Enrich, attach/open PDFs, embedded reader); shelves/racks use
> explicit add-pickers (no more overloaded selection); RIS/CSL import and the
> attach-file-to-work endpoint (6F) shipped; tooltips/disabled-reasons/empty-states/confirms are
> in throughout. **Deferred refinements** (Stage 7): per-field `user_confirmed` locking (6B/6F —
> field editing + canonical-select shipped; per-field lock not yet), listing a work's applied
> tags (no backend endpoint yet), and an import-batch/queue status panel (6D).

**6A. Tabbed application shell.** Extend the existing dependency-free hash router (`App.svelte`)
from `#library`/`#admin` to first-class areas, each its own page component under `pages/`:
**Library**, **Shelves**, **Racks**, **Tags**, **Import** (sources/folder · upload · identifier ·
BibTeX/RIS/CSL · batch & queue status), **Duplicates**, **Insights** (graph · topics · semantic
search · scope summaries), **Admin**. A persistent left or top tab bar with clear labels + icons;
the active tab is reflected in the hash (shareable/back-button-able). Reader opens from a work's
detail rather than being a permanent panel.

**6B. Library as master–detail (incl. metadata edit, P2/item8).** Left: the searchable/filterable
work **list** (reuse existing filters; add result count + sort). Right (or a routed
`#library/{id}`): a **work detail** panel with:
  - **editable fields** (title, year, venue, DOI, arXiv id, abstract, reading status) with Save;
  - **per-field provenance/conflict review** comparing assertions by source with "select canonical"
    (`GET /works/{id}/metadata`, `POST /works/{id}/metadata/select`) and a per-work **Enrich**
    button (`POST /works/{id}/enrich`);
  - **per-field `user_confirmed` locking** (§8.12) so enrichment never overwrites a confirmed field
    (today it is per-work all-or-nothing — needs a small backend change, see 6F);
  - **attached files** with "open in Reader", plus the work's contexts / annotations / summaries
    (moved out of the mega-page).
  *DoD:* a user can create a work, edit its fields, resolve a title conflict, attach/open a PDF, and
  enrich — all from one coherent panel; a confirmed field survives re-enrichment.

**6C. Shelves / Racks / Tags as explicit manager views.** Replace the overloaded-selection design:
  - A selected shelf/rack gets a clear **detail header** ("Shelf: Name") with its own grouped
    actions (rename, archive-with-confirm) and its membership list.
  - **Decouple selection from the add-target** — adding uses an explicit, searchable picker
    ("Add a work to this shelf…", "Add a shelf to this rack…") instead of a hidden dropdown that
    a chip-click silently primes. This removes the "archive enabled add" confusion.
  - Obvious selected-state styling and breadcrumbs.

**6D. Import area + queue visibility.** Consolidate all ingestion into one tabbed area and add
**RIS + CSL JSON import** — `POST /imports/ris` and `POST /imports/csl` mirroring the BibTeX path
(dedup by normalized DOI/title, `ImportBatch` + audit event), completing the §8.1 set. Surface
recent `ImportBatch` rows and extraction/enrichment job status so a user can see what's processing.
  *DoD:* RIS and CSL-JSON files round-trip into works with dedup (tested); import results and batch
  status are visible in the UI.

**6E. Affordances & help (cross-cutting, applies to every area).**
  - `title=` tooltips on all action buttons.
  - When an action is disabled, state **why** (inline hint or tooltip: "Select a work first",
    "Pick a shelf to add it to") — never a silently dead button.
  - Empty-state guidance per area ("No works yet — import a PDF, add by arXiv/DOI, or create one").
  - A one-line explanatory blurb at the top of each tab.
  - Confirmation prompts for destructive/irreversible actions (archive, remove, disable).

**6F. Small backend support (dependency for 6B).**
  - Endpoint to **attach an existing file to a work** (or upload-into-an-existing-work) so a
    manually-created work isn't a dead end; and a "files for a work" read.
  - Per-field confirm: extend metadata-select / a `user_confirmed` set to be **per field** rather
    than per work (§8.12), with a migration if a column/representation change is needed.
  *DoD:* the work-detail panel can attach + open a PDF and lock individual fields; covered by tests.

**Stage-4 exit:** the app is navigable by area; a new user can, without guidance, find how to
import/create works, edit them, attach PDFs, organize into shelves/racks, and review duplicates —
with visible help and no dead/unexplained controls. Frontend component tests cover shell routing
and master–detail selection; existing tests stay green; no backend capability regresses.

---

## Stage 4.5 — UX refinements & operational visibility  *(from a 2026-06-29 in-vivo review)*

A second hands-on pass surfaced concrete follow-ups. Tracked here so the polish is scheduled, not
lost. **Batch 1 (✅ done 2026-06-29):**
- ✅ **New-paper dialog** — a modal taking title / DOI / arXiv id / URL (URL parsed to id), not
  just a title.
- ✅ **Reader placement** — opens in a full-width **modal** ("Read") + "New tab ↗" for the raw PDF,
  instead of the cramped side panel.
- ✅ **Frozen top nav** — sticky header; the page scrolls under it.
- ✅ **Cross-tab selection persistence** — shared `lib/selection.ts` store keeps the open paper
  when you leave and return to a tab (Library wired; shelves/racks in batch 2).
- ✅ **Admin re-enable user** — `POST /admin/users/{id}/enable`; disabling is reversible.
- ✅ **Empty Audit-events list (bug)** — client read the paginated `{items}` envelope as an array.
- ✅ **Honest enrichment message** — names the background worker + Jobs tab.

**Batch 2 (✅ done 2026-06-29):**
- ✅ **Jobs tab + queue visibility** — `GET /jobs` exposes RQ queued/started/finished/failed +
  worker count + recent jobs, or `available:false` when Redis/worker is down. The Jobs tab
  auto-refreshes and flags "worker unavailable / no worker connected" — the fix for *"enrichment
  queued but nothing happens"* / *"abstract not extracted"* (both are worker tasks).
- ✅ **Delete paper** — `DELETE /works/{id}` cascades dependent rows; UI confirm; files kept.
- ✅ **Selection persistence for shelves/racks** — `lib/selection.ts` wired into both pages.
- ✅ **Server-folder clarity + agent management UI** — Import explains server-folder is server-side
  (aliases from `storage.server_allowed_roots`) and points PC folders to the agent; Admin → Agents
  lists an agent's manifested files with a Teleport action + agent CLI run instructions.

**Batch 3 (deferred / smaller):** per-field `user_confirmed` locking; list a paper's applied tags;
durable agent SQLite index; "is the worker running?" runbook note.

---

## Stage 5 — Local agent vertical (M5)  *(the distinctive remote-machine feature)*

The agent is the project's differentiator and is still mostly enrollment scaffold. Build it as one
focused vertical (audit "Agent M1").

8. **Agent manifest + teleport. ✅ DONE (2026-06-29).** Implemented as a secure agent-push flow:
   - Agent: `AgentIndex` maps opaque `local_file_id` (content hash) → path within configured
     roots; built by scanning. The raw-path `open_file_for_teleport` helper (the M5 security TODO)
     is **gone** — teleport resolves strictly through the index, so there is no code path that
     opens a server-supplied path.
   - Server: `AgentFile` model + migration `0014`; `POST /agents/manifest` (agent token) ingests
     entries; `POST /imports/teleport` (owner/editor) marks an entry requested;
     `GET /agents/teleports/pending` (agent token) lists them; `POST /agents/teleports/{id}/content`
     (agent token, multipart) verifies the uploaded bytes against the manifest SHA-256, stores
     content-addressed in the managed library, creates Work+FileWorkLink, and enqueues extraction.
   - Audit: `agent.manifest_received`, `teleport.requested/completed/failed`.
   *DoD met:* the rewritten `test_future_agent_teleport_acceptance.py` is enabled (real enroll →
   approve → manifest → request → push → file-present flow + hash-mismatch rejection); the server
   never sees a path and the agent never accepts one; a teleported file is a managed file
   extractable via the Stage 1 resolver. *Deferred (Stage 7):* a durable agent-side SQLite index
   (currently rebuilt per run) and an admin "browse agent files / request teleport" UI.

9. **Agent redesign v2 (SPEC §32). ✅ DONE (2026-06-29).** Reworked the agent into a single,
   persistent, tool-managed deployable and closed both Stage-5 deferrals:
   - **S1 — per-agent privileges** (migration `0015`): `can_index`/`can_extract`/`can_teleport`
     (off by default)/`can_be_requested`/`processing_visibility`/`server_status_visibility`;
     `PATCH /admin/agents/{id}/privileges` (owner, audited) + Admin UI; enforced server-side.
   - **S2 — import actions + teleport request/block** (migration `0016`): `index_only` /
     `index_and_extract` (PDF discarded after extraction, reference + preview kept) / `teleport`;
     `virtual_path`, `processing_state`, `teleport_blocked`, `preview_text`; reject / reject-forever
     / unblock; a `processing_visibility`-gated file-status endpoint; removed-source flagging.
   - **S3 — durable agent state**: tool-managed `agent.yaml`, a SQLite `state.sqlite3` mapping
     opaque `local_file_id` → real path (local-only) + per-file state/blocks, secrets via OS keyring
     or `0600` file. (This is the Stage-5 "durable agent-side SQLite index" deferral, now closed.)
   - **S4 — CLI**: enroll/set-token/set-server/add-folder/add-file/remove/list/status/sync/refresh/
     teleport/`request`/`start` (monitor + periodic sync, per-item action applied).
   - **S5 — local web GUI**: `paracord-agent web up`/`down`/`status`, a token-gated, loopback-only
     Starlette page covering all agent management (the in-vivo "how do I manage the agent" gap).
   *DoD met:* backend suite green (privilege + import-action + teleport-request coverage), 22 agent
   tests green, migration parity green; the agent is installable and the web GUI starts, gates by
   token, and stops cleanly.

---

## Stage 6 — AI pipeline hardening  *(provider architecture; keep lexical baselines)*

Move the lightweight baselines behind provider interfaces so a real local model can drop in without
a rewrite. **Keep the hash-BOW / TF-IDF / extractive providers as the default + test providers.**

9. **H2 — Embeddings off the read path. ✅ DONE (2026-06-30).** `semantic_search` is read-only
   (ranks stored vectors + embeds the query in memory; no writes). Embeddings are built on import
   via a background `embed_work_job` (enqueued on work-create + after enrichment) and on demand via
   `POST /search/reindex`; `index_one_work` replaces per-`(entity,model)`, and the bulk indexer uses
   a savepoint + `IntegrityError` guard so concurrent indexing is race-safe. An embedding-provider
   interface (`get_embedding_provider`) selects `hash_bow` (default) / `sentence_transformers` /
   `ollama`, each storing its own `model_name`. *DoD met:* `test_semantic_search_is_read_only_*`
   asserts no writes; the API path is read-only; provider is swappable.

10. **Summaries & topics provider interface + semantic dual-mode. ✅ DONE (2026-06-30).**
    `POST /search/semantic` takes `mode=embedding|lexical`. Summaries gained a `local_llm` tier
    (Ollama opt-in, graceful extractive fallback recording the requested model + `source_sections`).
    `model_topics` gained an `embedding`/`bertopic` backend with representative works / coherence /
    outliers / hierarchy, echoing the requested `embedding_model`. *DoD met:*
    `test_future_local_llm_acceptance.py` and `test_future_topic_modeling_acceptance.py` are enabled;
    the lexical/TF-IDF/extractive baselines remain the defaults with no new hard dependency.

---

## Stage 7 — Deferred polish & hardening  *(largely DONE 2026-06-30)*

- **H3 — fuzzy-title dedup. ✅ DONE.** Normalized-title **blocking** (compare only works sharing the
  first title token) bounds the former all-pairs scan; the ratio uses `rapidfuzz` when installed and
  falls back to stdlib `difflib`. A full-library scan can run in the background worker
  (`POST /duplicates/scan {background:true}` → `scan_duplicates_job`).
- **Auth hardening. ✅ DONE.** Failed-login throttling (429 + `Retry-After`) and in-app
  change-password with other-session revocation (`POST /auth/change-password`).
- **Security-doc truthfulness. ✅ DONE.** SSRF hardening (percent-encoded identifiers, same-host
  redirect enforcement); removed the dead `guest_access_enabled` flag; `SECURITY.md` reconciled
  (token hashes, no reversibly-encrypted fields, SSRF documented); `PARACORD_SECRET_KEY` is a real
  reserved setting.
- **Export polish. ✅ (mostly).** Preview + copy-to-clipboard + download in `ExportDialog`;
  selection/search export scope (`work_ids`) wired into the library multi-select. *Remaining:* full
  CSL **style** rendering via citeproc (CSL-JSON interchange already ships) and a graph-scope export.
- **View audit events. ✅ DONE.** `paper.viewed` on `GET /works/{id}`, `file.downloaded` on the
  (now-authenticated) stream endpoint (§7.6).
- **Ops. ✅ (core).** `make prod-smoke` (build prod stack + assert `/api/v1/health`); `make backup`
  / `make restore` + `docs/runbooks/backup_restore.md` (§8.16).

**Genuinely-remaining tail** (now planned in **`docs/WORKPLAN_NEXT.md`**, 2026-06-30):
- **C3/C4 remainder. ✅ DONE (2026-06-30, migration `0017`).** Added the weak FKs
  (`locations.agent_id` → agents; `references.*` and `citation_mentions.*` → works/references/
  raw_tei_documents with CASCADE / SET NULL) and converted the remaining document JSON columns
  (`sources.config`, `import_batches.settings/stats`, `duplicate_candidates.signals`,
  `annotations.coordinates`) to JSONB on Postgres. The parity test now also asserts every model FK
  exists in the migrated schema.
- **Headline next: runtime, GUI-managed AI providers + model download** (Stage 8 of
  `WORKPLAN_NEXT.md`) — move provider selection out of static config into an owner-editable web UI
  with in-GUI model pulls.
- **H7 pgvector**, **CSL citeproc styles**, a **Postgres FK-cascade/timestamptz/JSONB integration
  suite**, the **graph-scope export**, and an **ML extraction path** — Stage 9 of `WORKPLAN_NEXT.md`.

---

## Sequencing rationale

```
Stage 1 ✅ ──► Stage 2 ✅ GROBID coords ──► Stage 3 ✅ reader + graph components
                                                  │
Stage 4 ✅ frontend IA & UX overhaul  ◄───────────┘   (tabbed shell, master–detail
          (metadata-edit UI + RIS/CSL folded in)        library w/ editing, organize
                                                         fix, affordances/help)
Stage 5 ✅ agent manifest + teleport vertical (secure agent-push)
Stage 6  AI provider hardening (NEXT; independent; after 1)
Stage 7  deferred polish (last)
```

Stages 1–5 are done — the single-machine loop, the reader/graph, the full UI, and the
remote-workstation agent teleport all work. **Stage 6 (AI provider hardening) is next**: move
embeddings off the read path and put summaries/topics/search behind provider interfaces (keeping
the lexical baselines as defaults). Everything in Stage 7 is intentionally last.

---

# Round: UI / agent / reader improvements (2026-06-30)

In-vivo review round. Four phases, each implemented + tested + committed by a dedicated agent. Run
in a coordinated **sequence** (see "Execution & collision-safety") so they never collide on the
shared dev stack or the git index. Effort: **S** ≈ hours · **M** ≈ half-day · **L** ≈ multi-session.

## Resolved decisions (from discussion)
- **Reader tabs for Readers:** browse-all, hide actions — Reader sees Library, Shelves, Racks, Tags,
  Insights, Profile; **hide Import, Jobs, Duplicates** (editor+); Admin/Events stay owner-only.
- **PDF reader:** one fully-capable PDF.js reader (add a **text layer**); **no** separate
  native-viewer toggle (the existing "New tab ↗" button is the native/distraction-free escape hatch).
- **In-reader search behavior (confirmed):** **in-app search = whole document** (scans every page via
  `getTextContent()`, jumps to + highlights matches); **browser Ctrl+F = visible/rendered pages only**
  (page view = current page; smooth-scroll = pages rendered into view). This split is intended.
- **Agent metadata:** sync extracted **title + authors** from server back to the agent now (enables
  agent-side search/sort by title/authors).
- **Agent teleport:** agent-initiated, **push directly when the owner has granted `can_teleport`**.
- **change-password copy:** behavior is correct (it does revoke other sessions); fix the wording only.

## Judgment calls (will do unless told otherwise)
- Reference shorthand uses the `marker_text` of a linked `CitationMention` (numeric "[69]" or
  author-year, whichever GROBID captured) — no new column/migration needed.
- The agent "monitored/once" last-used preference persists in the agent web GUI's `localStorage`.

## Phase 1 — Access & profile polish  *(S)*
Files: `frontend/src/App.svelte`, `frontend/src/pages/ProfilePage.svelte`, `frontend/src/App.test.ts`.
- [x] 1. **Header grouping** — username + Profile link in one rounded-rect chip (a user-menu unit).
- [x] 2. **Profile role badge → top-right** of the Account card.
- [x] 3. **Roles & access: show only the user's own role** (don't advertise the privilege ladder).
  Self-contained descriptions: Reader = "Browse, search and read papers; cannot modify the library.";
  Editor = "Browse, search and read papers; import, edit, enrich and delete papers."; Owner = that +
  "manage users, agents, AI settings and the audit log."
- [x] 4. **Change-password copy fix** — "Signs you out everywhere else (other browsers/devices). This
  tab stays signed in." (behavior unchanged).
- [x] 5. **Role-gated tabs** — `roles: ['owner','editor']` on Import, Jobs, Duplicates; update
  `App.test.ts`.

## Phase 2 — Library: references/citations, import refresh, Read robustness  *(M)*
Files: `frontend/src/components/WorkDetail.svelte`, `frontend/src/pages/LibraryPage.svelte`,
`frontend/src/api/client.ts`, `backend/app/api/v1/endpoints/works.py`, files read schema.
- [ ] 6. **Reference ↔ citation cross-link** — derived shorthand/label per reference (from a linked
  `CitationMention.marker_text`, one join, no migration); show as a leading column/badge.
- [ ] 7. **Group entries in rounded-rect cards** — References and In-text-citations lists.
- [ ] 8. **Refresh library list on import** — `onImported` callback WorkDetail → LibraryPage reloads
  the works list.
- [ ] 9. **Read-button robustness** — `content_available` in the file read schema (false for
  `extracted_discarded` / missing location); clear label + disabled Read w/ tooltip; surface failure
  reason.
- [ ] 10. **Search by hash + full hash (server GUI)** — full hash visible + copy-on-click; library
  search matches a file `sha256` → owning paper.

## Phase 3 — Agent GUI overhaul  *(L)*  — internal order: **#11 before #15**
Files: `agent/paperracks_agent/{web.py,client.py,state.py,agent_ops.py,config.py}`,
`backend/app/api/v1/endpoints/agents.py`, `backend/app/schemas/agent.py`.
- [ ] 11. **Server→agent metadata sync** — server returns extracted title + authors in the agent
  file-status response; agent stores them in `state.sqlite3` (new columns).
- [ ] 12. **Agent-initiated teleport** — backend endpoint accepting an agent-offered teleport when
  `can_teleport` is granted; **Request teleport** button per file in the agent GUI.
- [ ] 13. **Local Read** — agent route `GET /api/files/{local_file_id}/view` (resolves `real_path`
  locally); **Read** button opens it in a new tab.
- [ ] 14. **Full hash + copy-on-click** in the agent table.
- [ ] 15. **Search / sort / filter** — search over filename + hash + title + authors; sort by any
  column (incl. title); filter by action and state.
- [ ] 16. **Add-folder/file dialog** — monitored/once as a radio/toggle remembering the last choice.
- [ ] 17. **Server status light** — green = reachable + approved; yellow = reachable but error
  (pending/revoked/unregistered/other); red = unreachable.

## Phase 4 — PDF reader overhaul  *(L)*  — internal order: **#18 before #19–#21**
Files: `frontend/src/components/PdfReader.svelte`, `frontend/src/components/WorkDetail.svelte`,
`frontend/src/api/client.ts`, `backend/app/api/v1/endpoints/works.py`,
`backend/app/models/annotation.py` (+ DELETE endpoint).
- [ ] 18. **PDF.js text layer** (foundation) — selectable text layer over each page. Unblocks 19–21.
- [ ] 19. **Working search** — highlight matches in the text layer; next/prev across all pages;
  Ctrl+F on rendered pages.
- [ ] 20. **Annotations that work** — selection → highlight/note/copy with accurate coords; persist +
  render highlight boxes; **delete** (new backend DELETE + client + per-note button); click a note
  jumps to its page/anchor.
- [ ] 21. **Citation ↔ reference navigation** — citation-overlay click switches tab **and scrolls to +
  flashes** the matching entry (both directions).
- [ ] 22. **View ergonomics** — paged ↔ smooth-scroll toggle (remembered) + drag-to-pan the zoomed
  page.

## Dependencies
- Phase 3: #11 before #15. Phase 4: #18 before #19–#21.
- **Phase 2 and Phase 4 edit the same files** (`WorkDetail.svelte`, `works.py`, `client.ts`) → must
  not run concurrently. Phases otherwise independent.

## Execution & collision-safety
The dev stack is a single shared working tree, bind-mounted into Docker with `uvicorn --reload` +
Vite HMR, and a single git index. Concurrent agents would collide at **build/test time** (a
whole-project build compiles another agent's half-finished edits) and on **commits**. So the four
phase agents run **sequentially** (P1 → P2 → P3 → P4): each implements, tests (frontend build +
vitest; backend pytest where relevant), and commits to `main` (no branch), then the next starts.
Within a phase, the internal order above is honored by that phase's agent.

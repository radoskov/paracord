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

**Batch 2 (planned, next):**
- **Jobs tab + queue visibility** — a backend endpoint exposing RQ queued/started/finished/failed
  (and "queue/worker unavailable"), and a Jobs tab. This is the fix for *"Enrichment queued but
  nothing happens"* and *"abstract not extracted"*: both are background-worker tasks — if the
  `worker` service (or Redis/GROBID) isn't running, jobs sit unprocessed with no UI signal today.
- **Delete paper** — `DELETE /works/{id}` (cascade dependent rows) + UI, with confirm.
- **Selection persistence for shelves/racks** — finish wiring `lib/selection.ts` so a half-built
  shelf stays open across tabs (the construct-a-shelf workflow).
- **Server-folder clarity + agent management UI** — explain what a "server-folder alias" is and
  where it's configured (server YAML `storage.server_allowed_roots`); make clear that adding a
  folder *on the user's own PC* is the **agent** path, not server-folder. Add an Agents UI to
  drive the local agent: show enrolled agents, their manifested files, and a "request teleport"
  action (the Stage 5 backend already supports this) + guidance on running the agent with its
  enrollment/bearer token.
- **Operational doc** — a short "is the background worker running?" runbook note; surface worker
  health in the Jobs tab.

**Batch 3 (deferred / smaller):** per-field `user_confirmed` locking; list a paper's applied tags;
durable agent SQLite index.

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

---

## Stage 6 — AI pipeline hardening  *(provider architecture; keep lexical baselines)*

Move the lightweight baselines behind provider interfaces so a real local model can drop in without
a rewrite. **Keep the hash-BOW / TF-IDF / extractive providers as the default + test providers.**

9. **H2 — Embeddings off the read path.** Generate embeddings on import / background RQ job, not
   inside `POST /search/semantic`; use upsert / `ON CONFLICT DO NOTHING` on `(entity_type,
   entity_id, model_name)`. Introduce an embedding-provider interface (`hash_bow` default;
   `sentence-transformers`/`ollama` opt-in).
   *DoD:* a normal search performs no writes; concurrent searches don't race; provider is swappable.

10. **Summaries & topics provider interface + semantic dual-mode.** Per maintainer note, offer the
    user a **choice of semantic-search modes** (lexical vs. embedding) rather than silently picking
    one. Add provider seams for local-LLM summaries (Ollama, opt-in) and a BERTopic option for
    topics, keeping the current deterministic baselines.
    *DoD:* enable `test_future_local_llm_acceptance.py` and `test_future_topic_modeling_acceptance.py`
    behind opt-in config; baselines remain the default with no new hard dependency.

---

## Stage 7 — Deferred polish & hardening  *(do last; non-blocking)*

Explicitly postponed so they don't consume time mid-build. Pull one forward only if it becomes a
user-visible problem (e.g. import latency for H3).

- **H3** — fuzzy-title dedup: `rapidfuzz` + normalized-title blocking/trigram index; move
  full-library scans to RQ. *(only when import latency is actually felt)*
- **C3/C4 remainder** — weak FKs (`Location.agent_id`, `Reference`, `CitationMention`), extend
  `JSONB` variant to remaining JSON columns, then assert autogenerate-clean in the parity test.
- **H7** — pgvector column + index + `CREATE EXTENSION vector` (ships with the real embedding model
  from Stage 6, not before).
- **Export polish** — CSL styles (citeproc), preview, copy-to-clipboard, search-result/graph/
  selection scopes, live always-current shelf/rack bibliography (§8.17.3).
- **Auth hardening (deferred M0)** — login rate limiting / lockout; in-app change-password with
  session revocation.
- **Security-doc truthfulness** — implement at-rest field encryption (`PARACORD_SECRET_KEY`) *or*
  correct `SECURITY.md` (M2/B8); remove/enforce `guest_access_enabled` (M3); SSRF hardening:
  URL-encode identifiers, forbid cross-host redirects (M5); reword egress copy (L).
- **Ops** — production smoke target (`prod-smoke`, B10); backup/restore (§8.16); emit and surface
  read/view audit events `file.viewed`/`downloaded`/`paper.viewed` (§7.6); Postgres-backed
  integration suite for FK-cascade/timestamptz/JSONB-query behavior.

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

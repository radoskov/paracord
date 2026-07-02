# Progress Report

> **Read `docs/AUDIT.md` first.** A full functional + implementation audit (2026-06-25) maps what
> the app actually does for a user vs. `SPECIFICATION.md`, lists correctness/infra/security
> findings with severities, and gives a prioritized **"Path to a fully functional app"** backlog.
> Two things every contributor must know: (1) models and migrations are **separate** schema
> definitions and have drifted — change a model → write + verify the migration on Postgres; and
> (2) "semantic search" and "topic modeling" are honest **lexical/TF-IDF approximations**, not
> embedding/BERTopic implementations.

## Full audit + consolidated decisions + auto-fixes (2026-07-02)

A six-pass audit (security, efficiency, stability, tech-stack suitability, plus verification of
every open item in the old followup/needs-discussion docs) produced **`docs/DECISIONS.md`** —
now the single decision list; `FOLLOWUP.md` and `docs/NEEDS_DISCUSSION.md` are superseded by it.
~30 unambiguous fixes were applied and committed in this pass (agent file perms + GUI XSS,
import-batch IDOR, RQ 900 s job timeout, extraction-failure rollback discipline, transactional
`make restore`, batched hot-path queries, HTTP-client + embedding-provider caching
(NEEDS_DISCUSSION 3a), default-shelf hooks for the last creation paths (2c), derived-OCR-copy
cleanup, 4 dead deps removed, and more — full table in DECISIONS §A). Verified with
`make test-full` (660 backend + 32 agent green) and `make frontend-check` (75 green + build).
`httpx2` was verified online as the legitimate Pydantic-maintained httpx fork. 38 items await
owner decisions in DECISIONS §B, each with a recommendation.

## Stage 6 + 7 complete (2026-06-30)

The `docs/WORKPLAN.md` stages are implemented through Stage 7. **Stage 6 (AI provider hardening):**
embeddings are built off the search read path (import / RQ / `POST /search/reindex`); search is
read-only with an `embedding`/`lexical` mode; embedding/summary/topic **provider seams**
(`hash_bow` + extractive + TF-IDF defaults; `sentence_transformers`/`ollama`/`local_llm`/`bertopic`
opt-in, degrading gracefully). Both AI future acceptance tests are enabled. **Stage 7:** login
throttling, in-app change-password + session revocation, SSRF-hardened enrichment, removed the dead
guest flag, `SECURITY.md` reconciled, selection-scope export + preview/copy, `paper.viewed`/
`file.downloaded` audit events, fuzzy-dedup blocking (+ rapidfuzz/RQ), `make prod-smoke` +
`make backup`/`restore`. Full backend suite green (233) + migration parity green. The remaining tail
(pgvector/H7, CSL citeproc styles, the C3/C4 FK+JSONB migration, a Postgres integration suite) is
non-blocking and tracked in WORKPLAN Stage 7.

## Current status

**Milestones 0–7 have an implemented vertical for every acceptance contract (all
`test_future_milestones.py` tests enabled and green). Much of M3–M7 is backend-complete but
under-exposed in the single-page UI, and several "AI" features are deliberate lightweight
stand-ins — see `docs/AUDIT.md` for the gap analysis and what to build next.**

What works today (real, tested in-container on Python 3.12):

- Containerized build/test/run stack (`docker compose`), auth (bcrypt), revocable sessions,
  owner/editor/reader role authorization, owner-only admin user management, audit logging,
  server-console bootstrap/password-reset, and Alembic migrations for the auth tables.
- Initial M1 backend path: configured server-folder sources, folder PDF scanning, SHA-256
  file registration, File/Location/Work links, import batches, basic work/shelf/rack/tag
  endpoints, and focused service tests.
- Initial M1 frontend path: Dockerized Vite/Svelte service, login, library table, reading
  queue, server-folder import controls, shelf/rack/tag controls, and file preview panel.
- Works can now be filtered by shelf, rack, tag, reading status, and basic metadata in the
  backend and from the frontend toolbar.
- Authenticated PDF streaming exists for configured server-folder file locations, including
  root-escape protection; the frontend file panel can open streamed PDFs in a browser tab.
- Shelves and racks can be archived, work/shelf memberships can be removed, and tag links
  can be removed via backend endpoints and frontend controls.
- **M1 validated end-to-end** (real HTTP API + Postgres + frontend build): login → create
  server-folder source → import PDFs (hash/dedup/PyMuPDF preview) → list/search works →
  add to shelf/rack → stream PDF, with the editor/reader role gate enforced over HTTP and
  re-import dedup confirmed. The Svelte frontend compiles (`vite build`).
- **M2 GROBID extraction, validated end-to-end on real arXiv papers:** real TEI parser
  (`services/tei_parser.py`), provenance-aware persistence (`services/extraction.py`) that
  records MetadataAssertions + References and only promotes canonical title/abstract/DOI
  when the work is not user-confirmed, migration `0004`, a synchronous GROBID client, an
  RQ queue (`app/workers/queue.py`), a `worker` compose service, enqueue-on-import plus a
  `POST /files/{id}/extract` trigger. Verified live: imported the Transformer and ResNet
  PDFs from arXiv → the worker ran GROBID via the `extraction` profile → 90 references and
  abstracts/titles persisted asynchronously. Uses the lightweight `lfoppiano/grobid:0.8.0`
  CRF image (~0.5 GB) rather than the ~12 GB deep-learning image.
- **M2 metadata enrichment (arXiv + Crossref), validated live end-to-end:** identifier-based
  connectors (`services/metadata_enrichment.py`) that record provenance-aware
  MetadataAssertions and promote trusted external fields over GROBID when the work is not
  user-confirmed; arXiv-id-from-filename detection at import; an automatic chain
  (import → GROBID extract → arXiv/Crossref enrich) plus a `POST /works/{id}/enrich`
  trigger; and a review/conflict surface (`GET /works/{id}/metadata`,
  `POST /works/{id}/metadata/select`). Verified live: importing `1706.03762.pdf`
  auto-corrected GROBID's mis-detected title ("Provided proper attribution…") to
  "Attention Is All You Need" via arXiv, with both values kept as assertions and the
  conflict flagged.
- **M2 enrichment connectors extended (OpenAlex + Semantic Scholar):** two more identifier-based
  connectors in `services/metadata_enrichment.py` — OpenAlex by DOI (rebuilding its
  inverted-index abstract) and Semantic Scholar by arXiv id/DOI — wired into `enrich_work`
  behind `enrichment_openalex` / `enrichment_semantic_scholar` settings (both opt-in/off by
  default). They record provenance assertions and promote like the existing sources, and stay
  within the egress policy (only the DOI/arXiv identifier leaves the machine). Parsers are
  unit-tested against fixtures.
- **M2 raw TEI + citation mention persistence:** raw TEI blobs are stored in
  `raw_tei_documents`; TEI body `ref type="bibr"` markers are parsed into
  `CitationMention` rows with section label and before/current/after sentence contexts,
  linked back to the extracted `Reference` and raw TEI source.
- **M2 citation context API:** `GET /works/{work_id}/citation-contexts` returns persisted
  in-text citation contexts with reference metadata.
- **M2 citation context frontend surface:** selecting a work in the Svelte library loads and
  displays extracted citation contexts with marker, section, sentence context, and reference
  metadata.
- **M4 duplicate/version scanner foundation:** `duplicate_candidates` now exists with an
  Alembic migration and ORM model; `services/duplicate_detection.py` generates idempotent
  review candidates for same DOI, same arXiv base ID/version mismatch, fuzzy normalized-title
  matches, exact file hash, and matching text fingerprints.
- **M4 duplicate review API foundation:** `/api/v1/duplicates` lists candidates, triggers a
  scan across all or selected work/file identities, and updates candidate review status
  (`open`/`accepted`/`rejected`/`ignored`) with resolver metadata.
- **M4 duplicate review frontend surface:** the Svelte workspace can list duplicate
  candidates, filter by review status, run a scan, inspect signals, and mark a candidate
  accepted/rejected/ignored.
- **M4 duplicate review backend actions:** review decisions can now merge work candidates
  without deleting source works, link a source work as a `WorkVersion`, mark a file candidate
  as a duplicate copy, keep candidates separate, or ignore them. Resolutions write audit events.
- **M4 duplicate review hardening:** when no explicit target is given, the surviving canonical
  work is chosen by heuristic (user-confirmed → latest arXiv version → metadata completeness)
  instead of arbitrary id order; candidate API responses carry human-readable entity labels, a
  summary, and a suggested target; and actions are refused on already-resolved candidates, with
  an extra guard preventing the same file from being split twice. The Svelte review panel shows
  the labels/summary and uses the suggested target for merge/version.
- **M4 duplicate review frontend actions:** the review panel now calls explicit merge,
  link-as-version, mark-duplicate-file, keep-separate, ignore, and reopen flows instead of only
  toggling generic status.
- **M4 multiwork candidate detection:** files with repeated abstract/reference markers or
  long proceedings-like previews are queued as `multiwork_file` candidates for review.
- **M4 multiwork split backend action:** `split_file` accepts user-provided segment ranges and
  creates `FileSegment`, `Work`, and `FileWorkLink` rows with `file_contains_multiple_works`
  warning state.
- **M4 multiwork split frontend controls:** `multiwork_file` candidates can submit line-based
  `Title | start page | end page` segment ranges to the backend split action.
- **M3 reader/reference integration started:** the frontend now has an embedded reader surface
  that loads authenticated PDF blobs and shows extracted citation contexts in a References tab.
- **M3 annotation storage started:** `annotations` has an Alembic migration and
  work-scoped create/list endpoints; the forward-looking annotation acceptance test is enabled.
- **M3 reader annotation UI started:** the embedded reader Notes tab lists annotations and can
  create note/highlight/page-anchor/citation-note records for the selected work/file.
- **M3 export (multi-format):** `/api/v1/exports` resolves work/shelf/rack scopes and renders
  BibTeX, BibLaTeX, RIS, CSL JSON, Markdown, HTML, and plain text. Authors are pulled from the
  best metadata assertion, citation keys follow the `authorYEAR` convention, each format
  returns its correct filename/content-type, and a `paper.exported` audit event is recorded
  (SPEC §7.6/§8.13). The Svelte library exposes a working export control (format picker +
  download) for the selected shelf or rack. Covered by `test_export_formats.py` and
  `ExportDialog.test.ts`.
- **M3 BibTeX import:** `POST /api/v1/imports/bibtex` ingests pasted/uploaded BibTeX
  (`services/bibtex.py`, a dependency-free balanced-brace parser) into Works, recording authors
  as a `bibtex`-sourced MetadataAssertion and an `ImportBatch` + `import.bibtex` audit event.
  Entries are de-duplicated against the library by normalized DOI and title, so re-importing the
  same file is a no-op. Imported works stay `user_confirmed=False` so enrichment can still fill
  gaps. The Svelte library has a paste-BibTeX import box. Covered by `test_bibtex_import.py` and
  the now-enabled forward-looking `test_import_bibtex_creates_works`.
- **M5 agent enrollment (owner-gated):** owner mints a single-use, expiring enrollment token
  (`POST /api/v1/admin/agents/enroll-token`); the agent presents it unauthenticated
  (`POST /api/v1/agents/enroll-request` → 202, pending); the owner approves
  (`POST /api/v1/admin/agents/{id}/approve`) which mints the agent's scoped access token, returned
  once. New `agents` / `agent_enrollment_tokens` tables (migration `0009_agents`), all tokens
  stored hashed, every step audit-logged (`services/agents.py`). **Manifest ingestion + teleport
  are now implemented** (Stage 5, see the 2026-06-29 entry above). Covered by `test_agents.py` and
  the now-enabled forward-looking `test_agent_enrollment_requires_owner_approval`.
- **M7 topic modeling (lightweight, no ML dep):** `POST /api/v1/ai/topics` clusters a
  library/shelf/rack scope's works into keyword-labelled topics (`services/topic_modeling.py`,
  TF-IDF + a small deterministic k-means, fully local) and persists `TopicAssignment` rows
  stamped with a `topic_model_id` (re-running replaces them). Returns topics with keyword labels
  + work counts. The Svelte library has a "Model topics" panel for the current scope. Covered by
  `test_topic_modeling.py` and the now-enabled forward-looking `test_topic_model_on_shelf_suggests_tags`.
- **M6 scoped citation graph:** `POST /api/v1/graphs/citation` builds a node/edge graph for a
  library/shelf/rack scope (`services/citation_graph.py`). Edges come from extracted
  `Reference` rows resolved to local works by a persisted `resolved_work_id` or an exact
  DOI/arXiv-base match; `node_mode=local_only` keeps in-scope edges while `include_external`
  also surfaces cited works not yet in the library, with a summary (node/edge/external/
  unresolved counts). The Svelte library has a working (lightweight) graph panel — summary +
  edge list, scoped to the selected shelf/rack or whole library. Covered by
  `test_citation_graph.py`, `CitationGraph.test.ts`, and the now-enabled forward-looking
  `test_shelf_citation_graph_is_scoped`.
- **M7 local summaries (tiers 0 & 1, no LLM):** `POST /api/v1/works/{id}/summaries` +
  `GET` (`services/summarization.py`). Tier 0 (`abstract`) stores the abstract verbatim; Tier 1
  (`extractive`) runs a dependency-free frequency-based extractive summarizer over the abstract
  plus GROBID body text (`tei_parser.extract_body_text`). Summaries are stored with provenance
  (`model_name` + `prompt_version`) and are idempotent per (work, type). The Svelte library has
  an Abstract/Extractive summary panel for the selected work. Covered by `test_summarization.py`
  and the now-enabled forward-looking `test_local_summary_records_provenance`. Tier 2
  (local-LLM abstractive via Ollama) is deliberately not implemented.
- **M7 semantic search:** `POST /api/v1/search/semantic` ranks works by cosine similarity to a
  free-text query (`services/semantic_search.py`). The default embedder is a deterministic,
  dependency-free feature-hashing bag-of-words model (`services/embeddings.py`) — fully local
  (no egress) and stable across processes. Embeddings (title + abstract) are cached in the new
  `embeddings` table (JSON vectors + Python cosine, so the same path works on SQLite and
  Postgres) and computed lazily on first search. Migration `0008_embeddings`. The Svelte
  library has a semantic search box that opens the matched work. Covered by
  `test_semantic_search.py` and the enabled forward-looking `test_semantic_search_returns_neighbours`.

- **Frontend navigation shell + Admin UI + import controls (P2/item6, P2/item10):** the SPA now
  has hash-based routing (`#library` / `#admin`) with a nav bar; an Admin page for user management,
  agent enrollment/approval, and the audit-event log; and PDF-upload + arXiv/DOI identifier-import
  controls in the Library Sources panel.

What still does NOT exist yet:

- Rich citation-graph rendering (Cytoscape interactive canvas) — the scoped graph API and a
  lightweight summary/edge-list panel exist, but the full interactive graph view and the
  PDF.js reader/reference-panel integration are still pending. Reference resolution is
  identifier-only so far (no fuzzy-title edge resolution, and `resolved_work_id` is not yet
  persisted by a background pass).
- Crossref/arXiv title-based (fuzzy) lookup and arXiv/DOI link ingestion — only
  exact-identifier enrichment is implemented so far (arXiv, Crossref, OpenAlex, Semantic
  Scholar).
- Annotation search/export, PDF.js-specific rendering/anchors,
  hardened duplicate/version UX, interactive citation graph, Tier-2 (local-LLM) summaries.
  Semantic search and topic modeling use a local hashing/TF-IDF approach; a real embedding model
  (sentence-transformers / Ollama / BERTopic) and a pgvector index are future opt-in upgrades.
  Agent manifest ingestion and teleport remain stubs.

Component note: **Redis has a live consumer** — the `worker` service runs the RQ
`paracord` queue and processes both GROBID extraction and enrichment jobs.

### Testing

The suite has three layers (run with `make test`):

- **Service/unit tests** — `test_extraction.py`, `test_enrichment.py`, `test_duplicate_detection.py`,
  `test_m1_core_library.py`, `test_auth_service.py`, etc. (SQLite, direct calls).
- **High-level API/flow + security tests** — `test_api_flows.py` (import → organize → search →
  read; metadata review; citation contexts), `test_api_security.py` (RBAC matrix, no-guest,
  auth-required, account-enumeration, audit, path-escape), `test_api_smoke.py`. These run the
  real app via `TestClient` against in-memory SQLite (shared harness in `conftest.py`).
- **Acceptance contracts — `test_future_milestones.py`.** These encode the M3–M7 milestone
  contracts; all are now enabled and green (no remaining skips). The file's header documents the
  `ENABLE WHEN` pattern so future milestones can add new skipped acceptance tests the same way.
- **Frontend component tests** — `frontend/src/*.test.ts` (Vitest + jsdom, run with
  `make frontend-test`). These execute the real Svelte mount in a DOM, so they catch
  client-render regressions that a raw-HTML fetch cannot (e.g. `main.test.ts` guards the
  Svelte-5 `mount()` entrypoint; `App.test.ts` checks the sign-in view renders).

Current count: 161 passing + 0 skipped backend, 2 passing agent, 4 passing frontend.
(`test_topics_separate_distinct_groups` occasionally produces `[2,4]` instead of `[3,3]` due to
TF-IDF nondeterminism on small corpora — all other 161 backend tests are deterministic.)

### Start here (next agent)

M1 done; M2 extraction + enrichment pipeline is live and validated. M3 reader/annotations/export
has started. M4 duplicate detection is complete. M6 citation graph and M7 AI features are done.

P0 audit items addressed (2026-06-26):
- **C3 (DONE):** FK declarations added to all ORM models matching their migration constraints.
- **C4 (DONE):** `AuditEvent.details` uses `JSONB` variant on Postgres.
- **H1 (DONE):** `httpx2==2.4.0` pinned in both `requirements.txt` files.
- **H4 (DONE):** Agent manifest/teleport endpoints now require agent-token auth and return 501.
  Dead `/citations/contexts` stub removed from OpenAPI.
- **P1/item4 (DONE):** `works.arxiv_base_id` persisted (migration 0011, backfilled), partial
  unique indexes on `doi` and `arxiv_base_id`; `references.resolution_status` added;
  `_same_arxiv_candidates` SQL-pushdown; `identifiers.py` shared helper.

P0 audit items addressed (continued, 2026-06-26):
- **H5 (DONE):** Multi-stage backend Dockerfile (`development` / `production` gunicorn targets);
  multi-stage frontend Dockerfile (`development` Vite dev / `production` nginx static);
  `docker-compose.prod.yml` compose override; `make prod-build/up/down` targets; `gunicorn>=22.0`
  added to requirements; `PARACORD_ENV` now defaults to `production`; `frontend/nginx.conf` with
  SPA routing + gzip + immutable cache headers.

P2 items addressed (continued, 2026-06-26):
- **item9 (DONE):** `POST /api/v1/ai/summaries` now generates a real extractive summary over
  all abstracts in a library/shelf/rack scope. Returns entity_type, entity_id, text, provenance,
  and work_count. Empty scopes return 400. Six new tests.
- **item10 (DONE, partial):** Single-PDF upload (`POST /imports/upload`) stores content-addressed
  in `managed_library_root`, SHA-256 deduplicates, and enqueues GROBID extraction. Identifier
  import (`POST /imports/identifier`) creates a Work from arXiv id or DOI and immediately
  enriches it; idempotent on re-import. Streaming updated to serve `managed_path` locations.
  Nine new tests. RIS/CSL import deferred to a later session.

P1 items addressed (2026-06-26):
- **item5 (DONE):** DOIs are now stored normalized (bare, lowercase, no `https://doi.org/`
  prefix) at all write sites. `_same_doi_candidates` and `_find_existing` (BibTeX) now use
  `WHERE doi = :bare_doi` SQL pushdown — O(1) lookups instead of O(n) Python loops.
  Migration `0012_normalize_dois` patches any existing rows. Tests updated.

P2 / P0 items addressed (2026-06-29):
- **Agent redesign v2 (SPEC §32, DONE) — single persistent, tool-managed agent:** the agent is now
  one durable deployable rather than per-run scaffold, and both Stage-5 deferrals are closed.
  **Server:** per-agent privileges (migration `0015`: `can_index`/`can_extract`/`can_teleport`
  [off by default]/`can_be_requested`/`processing_visibility`/`server_status_visibility`,
  `PATCH /admin/agents/{id}/privileges` + Admin UI, enforced server-side) and import actions +
  teleport request/block (migration `0016`: `import_action`/`teleport_policy`/`virtual_path`/
  `processing_state`/`teleport_blocked`/`preview_text`). New `index_and_extract` action uploads,
  extracts, then **discards** the PDF, keeping the Work + references + a preview; teleport
  reject/reject-forever/unblock; removed-source flagging. **Agent:** tool-managed `agent.yaml`, a
  durable SQLite `state.sqlite3` mapping opaque `local_file_id` → real path (local-only, the closed
  Stage-5 deferral), secrets via OS keyring or `0600` file; a full CLI (enroll/set-token/add-folder
  /list/status/sync/refresh/teleport/`request`/`start`); and a token-gated, loopback-only Starlette
  **web GUI** (`paracord-agent web up`/`down`/`status`) covering all agent management — the in-vivo
  "how do I run/manage the agent" gap. 22 agent tests + backend privilege/import-action/teleport
  coverage + migration parity green.
- **Stage 5 (DONE) — Agent manifest + teleport (M5):** the remote-workstation feature now works as
  a secure **agent-push** flow. `AgentFile` (migration `0014`) records manifest entries; an agent
  posts its manifest (`POST /agents/manifest`), a user requests a teleport (`POST /imports/teleport`),
  the agent polls `GET /agents/teleports/pending` and pushes the bytes to
  `POST /agents/teleports/{local_file_id}/content`, where the server **verifies the SHA-256** before
  storing the file content-addressed in the managed library (then creates a Work + enqueues
  extraction). The agent resolves files only through an opaque-`local_file_id` `AgentIndex`; the
  raw-path teleport helper is removed, so neither side ever handles a server-supplied path. Audit
  events at each step. Acceptance test enabled. Deferred to Stage 7: durable agent SQLite index +
  an admin teleport-browser UI.
- **Stage 4 (DONE) — Frontend IA & UX overhaul:** the single ~10-section page was replaced with a
  hash-routed **tabbed shell** (`App.svelte`) over per-area pages — Library, Import, Shelves, Racks,
  Tags, Duplicates, Insights, Admin. The **Library** is now a searchable master list + a
  `WorkDetail` panel (edit fields + Save, metadata-conflict review with canonical select, per-work
  Enrich, attach/open PDFs via the new `/works/{id}/files` endpoints, embedded PDF.js reader, tag
  apply). **Shelves/Racks** are explicit master–detail managers with add-pickers scoped to the open
  item (fixing the overloaded-selection confusion). **Import** consolidates folder/upload/identifier
  /BibTeX/**RIS**/**CSL-JSON**. Cross-cutting affordances (tooltips, disabled-reason hints,
  empty-state help, per-tab blurbs, destructive-action confirms) throughout. Deferred to Stage 7:
  per-field `user_confirmed` locking, applied-tags listing, import-queue panel.
- **Stage 3 (DONE) — PDF.js reader + interactive Cytoscape graph:** `PdfReader.svelte` replaces the
  iframe with a `pdfjs-dist` canvas reader (page nav, thumbnail rail, zoom, in-app text search,
  citation-coordinate highlight overlay, References→page jump, and text-selection→annotation with a
  coordinate payload). `CitationGraph.svelte` replaces the text edge-list with an interactive
  `cytoscape` canvas (click-to-open works, force/circle/grid/hierarchy layouts, degree-based node
  sizing) and a Graph/List render-mode toggle (list doubles as the headless fallback). Heavy libs
  are lazy-loaded chunks; frontend tests (10) + `vite build` green. Deferred to Stage 7:
  ref→all-mentions back-index, graph version-collapse, large-graph progressive rendering.
- **A3 (DONE):** `make check` now includes `test-migrations`; `make ready` and `make ci` include
  `frontend-check` — so a green `ready` mirrors CI (backend+agent tests, migration parity, frontend
  build/test). WORKPLAN Stage 1, item 2.
- **B1 / Stage 2 (DONE):** GROBID extraction options are config-driven (`processing.grobid:` YAML);
  `GrobidClient` sends `teiCoordinates`; `tei_parser` parses PDF `coords` into
  `CitationMention.pdf_coordinates` (JSONB list of `{page,x,y,w,h}` boxes, replacing the four
  scalar `pdf_*` columns — migration `0013`, SPEC §9.3); the citation-context API now returns
  `pdf_coordinates` + `pdf_x/y/w/h`. Deterministic coordinate acceptance test enabled. This
  unblocks the PDF.js reader anchors (Stage 3).
- **A1 (DONE, HIGH):** managed-path extraction fix. New shared resolver
  `services/file_paths.py::resolve_backend_readable_pdf_path` handles both `server_path` and
  `managed_path` (with root-escape validation); `extract_and_store()` and `files.py::stream_file`
  both use it. Uploaded PDFs are now extractable (previously failed with "No server-path location").
  Regression test added. Completes WORKPLAN Stage 1, item 1.
- **P2/item6 (DONE):** Navigation shell + Admin UI. `App.svelte` now has hash-based routing
  (`#library` / `#admin`) and a nav bar; new `pages/AdminPage.svelte` covers user management
  (create / role-change / disable), agent management (issue enrollment token, approve, reveal
  bearer token once), and the last-50 audit-event list. The token is lifted to `App.svelte` so the
  Admin page shares the authenticated client. (commit `94151b4`)
- **P2/item10 frontend (DONE):** `LibraryPage.svelte` Sources section now has a PDF file-upload
  control (`uploadPdf`) and an arXiv/DOI identifier-import control (`importByIdentifier`) wired to
  the import endpoints. (commit `94151b4`)
- **C5 (DONE):** the production-build work (H5) had made `make build` build production images and
  left misleading `Dockerfile` comments; `docker-compose.yml` now pins `target: development` for
  `api`/`worker`/`frontend` and the dev/prod split is correct again. (commit `c274605`)
- **Test battery + tooling (DONE):** added additional algorithm/library/security contract tests,
  a more robust (deterministic) topic-modeling test, four *skipped* forward-looking acceptance
  contracts under `backend/tests/future/` (GROBID coordinates, agent teleport, local LLM, topic
  modeling), ruff coverage extended to `frontend/`+`config/`, and `INSTALL.md`. (commits `accd526`,
  `517cdb1`)

### >> The ordered plan now lives in `docs/WORKPLAN.md` <<

`docs/WORKPLAN.md` (2026-06-29) is the authoritative, execution-ordered plan to a fully functional
app. It re-validates every open audit finding against the current code and groups the remaining
work into 7 stages, front-loading whole-area unblockers and **deferring minor polish/optimizations
to the last stage**. Summary of the next stages:

1. **Stage 1 — correctness/CI — DONE:** **A1** managed-path extraction fix + **A3** `ready`/`ci`
   mirror CI.
2. **Stage 2 — GROBID settings + coordinate extraction (B1) — DONE.**
3. **Stage 3 — PDF.js reader + interactive Cytoscape graph — DONE.**
4. **Stage 4 — Frontend IA & UX overhaul — DONE.**
5. **Stage 5 — Agent manifest + teleport vertical (M5) — DONE.**
6. **Next: Stage 6 — AI pipeline hardening.** Move embedding creation off the `POST /search/semantic`
   read path to import/background with upsert (H2); put embeddings/summaries/topics behind provider
   interfaces (keep the hash-BOW / TF-IDF / extractive baselines as defaults; add opt-in
   sentence-transformers/Ollama/BERTopic seams) and offer a lexical-vs-embedding semantic mode.
   See `docs/WORKPLAN.md` Stage 6; acceptance scaffolds `test_future_local_llm_acceptance.py` and
   `test_future_topic_modeling_acceptance.py`.
4. **Stage 4 — metadata review/edit UI (P2/item8) + RIS/CSL import (P2/item10 remainder).**
5. **Stage 5 — agent manifest/teleport vertical (M5).**
6. **Stage 6 — AI provider hardening (H2 off read path; embedding/topic/summary provider seams).**
7. **Stage 7 — deferred polish:** H3 fuzzy perf, remaining FK/JSONB, pgvector (H7), export polish,
   M0 auth hardening, security-doc truthfulness, backups, prod smoke.

H6 (`.env` prefix) is an operator action: regenerate a local `.env` from `.env.example`
(`PARACORD_*`) — no code change. The leftover M0 auth items remain deliberately deferred.

## Completed

- Product requirements consolidated into a full implementation specification.
- Server/agent architecture selected.
- No-guest access-control requirement captured.
- Server-local credential recovery requirement captured.
- Teleport workflow captured.
- Work/version/file/file-segment model captured.
- Citation context and local citation graph requirements captured.
- Citation export requirements captured.
- Local AI summary and topic modeling requirements captured.
- Initial backend, agent, frontend, documentation, and operations folder structure created.
- Backend settings now load supported values from server YAML with environment overrides.
- Backend password hashing and verification helpers are implemented with bcrypt.
- Server-console owner bootstrap and password reset scripts now touch the database and write audit events.
- Initial Alembic migration creates `users` and `audit_events`.
- Second Alembic migration creates revocable `user_sessions`.
- `make migrate` applies backend migrations.
- Backend unit tests cover settings loading and security helpers.
- Server-console admin script tests cover first-owner creation, duplicate-owner refusal, and password reset audit logging.
- Minimal login/logout endpoints create and revoke server-side bearer sessions.
- Non-health, non-login API routers now require bearer-token authentication.
- Password reset now revokes active sessions for the target account.
- Auth service tests cover credential validation, token hashing, session revocation, and audit persistence.
- API dependency tests cover valid, missing, and invalid bearer tokens.
- Secrets-handling policy documented and enforced via a secret scanner, pre-commit hook, and CI workflow; hardcoded Postgres dev password removed from compose in favor of `.env`.
- Role-based authorization: `require_roles`/`require_owner` dependencies and owner-only admin endpoints for user management (list/create/role-change/disable) and audit-event access, with `user.created`/`user.role_changed`/`user.disabled` audit events and last-owner protection.
- Login account-enumeration mitigation (constant-time dummy verification on the no-user path) and a startup assertion that no guest role is configured.
- Containerized dev/eval stack (Python 3.12): `backend/Dockerfile` (api server) and `agent/Dockerfile` (client), `docker compose` services for postgres/redis/api/agent with healthchecks and GROBID/Ollama profiles, in-container test/lint, and a CI workflow.
- M1 backend persistence/import slice: `sources`, `import_batches`, M1 file/work/organization
  fields, `shelf_works`, `rack_shelves`, and `tag_links` models plus Alembic migration.
- Configured server-folder sources can be created by alias only; folder import scans a configured
  root, hashes PDFs, creates File/Location/Work/FileWorkLink rows, extracts a PyMuPDF first-page
  text preview when available, deduplicates by SHA-256, and audit-logs import activity.
- Basic backend endpoints exist for sources, folder imports, file metadata, manual work
  create/edit/list/search, shelves, racks, membership, and tags.
- Compose-managed frontend service (`frontend/Dockerfile`) keeps Node dependencies inside
  Docker, with `make frontend-dev` and `make frontend-build` targets.
- M1 frontend workspace renders login, library search/status filters, reading queue,
  server-folder source/import controls, manual work creation, shelves/racks/tags, and a
  file list with first-page preview text.
- Work search now supports shelf/rack/tag filters, and the frontend toolbar exposes them.
- `GET /api/v1/files/{file_id}/stream` streams PDFs from configured server-folder sources
  only, and rejects file locations outside the configured source root.
- M1 CRUD gaps narrowed: archive shelves/racks, remove work-from-shelf and shelf-from-rack
  memberships, and remove tag links.
- Raw TEI storage and citation mention persistence: migration `0005`, `RawTeiDocument`,
  parser support for body bibliography refs, and idempotent persistence of references and
  mentions from GROBID TEI.
- Work-scoped citation context API returns persisted `CitationMention` rows joined to their
  extracted references.
- The frontend library workspace displays citation contexts for the selected work.
- Duplicate candidate storage and scanner foundation: migration `0006`, `DuplicateCandidate`,
  and idempotent candidate generation for DOI/arXiv/fuzzy-title/exact-file/text-fingerprint
  signals.
- Duplicate review API foundation: list/scan/status endpoints under `/api/v1/duplicates`.
- Initial duplicate review frontend panel with scan, status filter, signal display, and
  accept/reject/ignore controls.
- Backend duplicate review actions for merge-work, link-as-version, duplicate-file,
  keep-separate, and ignore decisions.
- Frontend duplicate review actions wired to the backend action API.
- Conservative multiwork-file candidate detection in the duplicate scanner.
- Backend `split_file` action creates segments, works, and contains-links from reviewed ranges.
- Frontend split controls submit segment ranges for `multiwork_file` candidates.
- Embedded reader surface with References tab backed by citation contexts.
- Backend annotation storage and work-scoped create/list API.
- Reader Notes tab can list and create annotations for the selected work/file.
- BibTeX export for work/shelf/rack scopes.

## In progress

- M1 backend API/frontend implementation.
- Local agent protocol stubs.
- LaTeX implementation manual draft.
- Agent task partitioning.
- M0 developer skeleton hardening.

## Not started

- Login rate limiting / failed-login lockout (role-based authorization is now implemented).
- In-app password-change endpoint (server-console reset exists; web change-password + its session revocation still pending).
- Embedded PDF.js reader integration (a lightweight citation-context panel exists; the full reader/reference-tab does not).
- Agent registration and token rotation implementation.
- Export format expansion/audit; annotation search/export; PDF.js-specific reader controls/anchors;
  duplicate UX hardening.
- Citation graph materialization implementation.
- Export renderer.
- BERTopic and embedding pipeline.
- Local LLM summarization pipeline.
- Audit-log admin views (and read/export audit *events* — see tech debt).
- End-to-end tests.

## Tech debt and cleanups

> The authoritative, severity-ranked list (with the prioritized fix order) now lives in
> **`docs/AUDIT.md`**. The items below are kept as quick pointers; AUDIT.md supersedes them.

**Top-priority from the 2026-06-25 audit (see AUDIT.md for detail):**
- ~~`summaries`/`topic_assignments` model tables had no migration (prod-breaking).~~ **Fixed** —
  migration `0010_summaries_topics`, verified on Postgres (AUDIT C1).
- ~~**No migration/Postgres test** — drift is invisible (this is why C1 shipped).~~ **Done** —
  `backend/tests/test_migration_parity.py` runs `alembic upgrade head` on a throwaway Postgres and
  asserts model↔schema table/column parity (`make test-migrations`; CI Postgres service; self-skips
  without PG). Follow-up: assert autogenerate-clean after C3/C4 (AUDIT C2).
- ~~**FK + JSONB drift** — FKs live in migrations but not models; `JSONB` in migrations vs generic
  `JSON` in models. Makes autogenerate dirty and leaves cascades untested (AUDIT C3/C4).~~ **Fixed**
  — ForeignKey declared in all 14 affected model columns; `AuditEvent.details` uses JSONB variant.
- ~~**`httpx2`** is an unpinned niche fork on the only egress path.~~ **Fixed** — pinned to
  `httpx2==2.4.0` in both `requirements.txt` files. (`httpx2` is the Pydantic-maintained
  security-patch fork; reverting to mainline `httpx` would be wrong — AUDIT H1 misstated the fix.)
- **Perf**: dedup scan / BibTeX import / semantic-index are full-table Python loops on the request
  thread — push to indexed SQL + RQ (AUDIT H2/H3). `arxiv_base_id` SQL pushdown is now in place
  for `_same_arxiv_candidates`; DOI/BibTeX dedup and the semantic index still need RQ offload.
- **No production build** (dev `--reload`/Vite-dev image is the only stack) (AUDIT H5).

- Remove or fully wire the dead `guest_access_enabled` setting (`backend/app/core/config.py`). A startup `assert_no_guest_roles` check now enforces that no guest role is present in `security.allowed_roles`.
- ~~Migrate deprecated `datetime.utcnow()` to timezone-aware `datetime.now(UTC)` across `services/auth.py`, `models/*`, `services/users.py`, and `scripts/reset_admin_password.py`, together with switching the model `DateTime` columns to `DateTime(timezone=True)` (plus a migration).~~ **Done.** All write/default sites use `datetime.now(UTC)`; all model `DateTime` columns are `timezone=True`; migration `6a310e33c3d6` converts the existing Postgres columns to `timestamptz` (interpreting stored values as UTC, no-op on SQLite). `auth.py` normalizes session timestamps via `_as_utc()` so the comparison is robust even where a backend round-trips naive datetimes (SQLite, or a not-yet-migrated column).
- Add symlink-escape and `../` traversal test cases to `agent/tests/test_security.py` (the primitive is correct but currently untested).
- Remove or wire the unused agent config flags `follow_symlinks` / `teleport_enabled`.
- Note in `docs/architecture/api_surface.md` and `data_model.md` that they reflect current stubs and defer to `SPECIFICATION.md` §10 / §9.
- Relabel the `SPECIFICATION.md` front-matter Contents as a thematic overview (its numbers do not match the section numbers).

### Data-model divergences from SPECIFICATION.md §9.3 (fix before they are built on)

These cost a migration + re-extraction if M4 (duplicates) / M6 (citation graph) / M7 (topics)
are built on the current shapes, so address the first two before the M4 review workflows harden:

- ~~**Split `works.arxiv_id` into `arxiv_base_id`** (strip the `vN` suffix) and add a **UNIQUE**
  constraint on `doi` and the arXiv base id.~~ **Done** — `arxiv_base_id` column added (migration
  0011, backfilled), partial unique indexes on both fields. `arxiv_id` kept for provenance; base id
  is the dedup/graph key.
- ~~**`Reference` is missing `resolution_status`**.~~ **Done** — `resolution_status` column added
  (migration 0011, default `unresolved`); `build_citation_graph` now persists the result.
  `resolution_confidence` and `parsed_authors`/`parsed_venue` are still not modeled.
- **`CitationMention` is missing `extraction_confidence`** and stores coordinates as four float
  columns instead of §9.3's `pdf_coordinates` jsonb (the PDF.js reader contract for M3).
- **Topic modeling is collapsed** into a single `topic_assignments` table instead of §9.3's
  `topic_models` / `topics` / `work_topics` (loses model version/params/keywords needed by
  §8.15 model-freezing). M7.
- UUIDv7/ULID sortable PKs (§9.2) vs the current random UUID4 — minor, but migration cost grows.

### Behavioral / security gaps found in the alignment audit

- **`user_confirmed` is a global enrichment lock.** `create_work`/`update_work` set
  `user_confirmed=True` on any manual edit, and promotion keys off `not user_confirmed`, so a
  single edit permanently freezes *all* fields against future enrichment. §8.12 wants
  per-field user locks ("user edits highest priority, conflicts surfaced as warnings"). Move to
  per-field locking before users edit metadata heavily.
- **Read/export audit events are not emitted (§7.6).** `record_event` fires only for auth and
  service mutations; `file.viewed` / `file.downloaded` / `paper.exported` are never written on
  the stream/works/exports endpoints. Add them.
- **No SSRF guard in the enrichment HTTP clients.** Only fixed arXiv/Crossref hosts are hit
  today, but §7.7 requires the future `/sources/url` importer to block private IP ranges — it
  must not be built on the current unguarded `httpx2` clients.
- **Duplicate citation-contexts surface.** `endpoints/citations.py` `/contexts` is still a stub
  while `works.py` `/works/{id}/citation-contexts` is the real one; remove/redirect the stub so
  a dead endpoint does not ship in the OpenAPI schema.

## Next milestone: M0 developer skeleton

Acceptance criteria:

1. `docker compose up -d --build` starts PostgreSQL, Redis, the api server, and the agent client (GROBID/Ollama are opt-in profiles).
2. Backend serves `GET /api/v1/health`.
3. Server can create the first admin account through a server-console command.
4. The project can run tests with `make test` (in the api container, Python 3.12).
5. LaTeX docs compile with `docs/compile_docs.sh` on a machine with TeX installed.

Progress notes:

- `GET /api/v1/health` exists and has a test.
- Server-console admin scripts are DB-backed for users/audit events and password reset revokes active sessions.
- Alembic is initialized for the first security tables and sessions; broader domain models still need migrations.
- Build/test now run in containers (Python 3.12). Validated end to end: `docker compose up -d --build` brings the api healthy after migrations, the full suite passes via `docker compose run --rm api pytest` (23 passed), and a live smoke test (bootstrap owner → login → owner `GET /admin/users` 200 → editor `GET /admin/users` 403 → bad login 401 → audit events) succeeds against real Postgres.
- Replaced unmaintained `passlib` with the `bcrypt` library directly (passlib was incompatible with modern bcrypt); fixed Alembic revision ids that exceeded the 32-char `alembic_version` column.

## Next milestone: M1 core library, organization, and files

See `ROADMAP.md` / `SPECIFICATION.md` §20 for the full plan. The local agent and teleport
moved to M5; M1 now delivers the single-machine value loop via server-folder import.

Acceptance criteria:

1. Admin user can log in.
2. A server-folder source can be added and scanned (single-machine mode, no agent required).
3. A folder of PDFs imports as file/work records with a PyMuPDF first-page preview.
4. Works can be created/edited and added to multiple shelves; shelves to multiple racks.
5. Works, shelves, and racks can be tagged.
6. Basic metadata search and filters work; library, shelf/rack, file, and reading-queue views render.
7. No arbitrary path endpoint exists.
8. Import activity is audit logged.

# Changelog

All notable changes to PaRacORD should be documented in this file.

The format follows Keep a Changelog style conventions, but the project is currently pre-release.

## [Unreleased]

### Added

- **"Duplicate PDF" awareness when attaching a shared PDF.** Attaching a PDF that (by content hash)
  already belongs to another paper now shows a **duplicate PDF** badge in the Files section — click it
  to find the other paper(s) via a Library hash search. The duplicate scan also gains a **`shared_file`**
  detector so two papers sharing one PDF are flagged in the Duplicates tab even when their title/DOI
  differ. `WorkFileRead` gains `also_in_count`. (Attaching an already-extracted deduped PDF no longer
  fires a misleading no-op re-extraction; proper per-paper extraction of a shared PDF is a documented
  follow-up.) Batch 11.

### Fixed

- **"Ignore" on the Duplicates tab is now transient.** Ignoring a candidate no longer stamps a
  permanent flag — it drops from the current results but **reappears on the next scan** (with an audit
  event). **"Keep separate"** stays permanent and reviewable (filter relabeled "Kept separate";
  reopenable). Batch 11.

### Added (batch 10)

- **External citing papers — see who cites a paper, and show them in the reference graph.** The paper
  view gains a **Citing papers** panel: a "Fetch citing papers" button retrieves the papers that cite
  this one from **OpenAlex** (falling back to **Semantic Scholar**) — Crossref only exposes the count,
  not the list — up to 100 (title, authors, year, venue, DOI), shown with "as of" + provenance, each
  with an **Import** action to add it to the library. Citing papers are stored **permanently and
  deduplicated** as metadata-only "external papers" (a paper that cites several of your works is
  stored once and referenced), so they never re-fetch unless you refresh. The Reference Graph gains a
  **"Show citing papers"** toggle adding them as incoming `citing` nodes with edges into the base
  paper. New `external_papers` + `external_citation_links` tables;
  `GET/POST /works/{id}/citing-papers[/fetch]`; `reference-graph?include_citing=true`. Batch 10, issue 8.

- **Venue & Author sub-tabs in the Citation Summary.** The Citation Summary tab now has
  **Overview / Venues / Authors** sub-tabs. Venues shows where the scope's papers are typically
  published (paper count, share, year range) and Authors shows the most frequent authors — each with
  basic dedup (venues grouped case/punctuation-insensitively; authors by last name + first initial, so
  "Vaswani, A." and "Ashish Vaswani" count once), surfacing merged spellings/forms. New
  `GET /citations/venue-author-summary` over the same scope + access rules as the summary. Batch 10,
  issue 7. (Live external venue-metadata enrichment is deferred — venue is free text with no reliable
  identifier to look up.)
- **Popups and tabs focus their main input.** Opening "Put into a shelf", "Move file to another
  paper", or "Merge…" now places the cursor in the popup's main field; switching to the Search or Tags
  tab focuses its main input. The **Move file** picker is pre-filled with the current paper's title so
  likely destinations show immediately. New reusable `focusOnMount` Svelte action. Batch 10, issue 6.
- **Multi-PDF import with an extraction preview.** The Import tab's PDF card now accepts several PDFs
  at once — each becomes its own paper. **Preview & choose** extracts every PDF *before* creating any
  record, shows the parsed title/authors/year/DOI and any collisions (same PDF, same DOI, same title),
  and lets you pick which papers to create. **Import directly** creates them immediately, skipping any
  PDF whose content or DOI already exists, or whose extraction failed, with a per-file note. A single
  bad PDF never fails the batch. New `POST /imports/upload-multi`, `GET /imports/staging/{id}`,
  `POST /imports/staging/{id}/commit`, backed by `import_staging_batches`/`import_staging_items` and a
  record-free `extract_staging_item_job` worker (commit applies the stored TEI without re-running
  GROBID). Batch 10, issue 1.
- **New Library columns: Files, Topics, Badges, Tags.** The Library table gains four opt-in columns
  (hidden by default; enable them in the Columns picker): file count, per-paper topics, applied tags
  (colour-tinted), and status **badges** (extracted / extraction failed / not extracted / poor text /
  no text layer / OCR / conflicts). `WorkRead` now carries `file_count`, `tags`, and `badges`,
  computed with four batched queries per page (no N+1). Batch 10, issue 5.
- **Inspect both papers of a duplicate pair in the paper view.** On the Duplicates page the base and
  merge-from labels are now clickable and open the full paper view (WorkDetail modal), so you can
  compare the two papers before choosing which survives the merge (issue 2). The merge-preview line
  also no longer sticks on "Loading preview…": the request is time-bounded, and a failed/timed-out
  preview shows an explicit "Preview unavailable — open the papers below to compare" note.
- **Editable authors in the paper Details panel.** The Details panel now shows an **Authors** field
  (seeded from the paper's `authors` metadata). Editing and saving persists it via a new
  `POST /works/{id}/metadata/set` endpoint, which records a `source="user"` canonical assertion and
  locks the field so enrichment/extraction can't overwrite the manual value. Authors has no dedicated
  Work column, so it lives purely as the canonical assertion (same read path the UI already used).
  Batch 10, issue 4.
- **"Set metadata from best source" gains an "all fields" option.** The Library bulk action can now
  promote the best available assertion for *every* promotable field (title/abstract/year/venue/doi)
  in one go, not just a single chosen field. `field_name: "all"` on
  `POST /works/bulk-apply-metadata` loops the per-field promotion; locked/user-confirmed fields are
  still skipped per-paper so corrected values are never clobbered (AGENTS rule 5). Batch 10, issue 3.
- **Move a PDF between papers, and merge two arbitrary papers.** The paper detail gains a "Move…"
  action on each attached file (re-points its link to another paper via
  `POST /works/{id}/files/{file_id}/move`) and a "Merge…" action that folds any other paper into
  this one (`POST /works/{id}/merge` + `/merge-preview`, reusing the duplicate-resolution
  `merge_works`; reversible via the existing `/unmerge`). New `WorkPicker` typeahead component. Lets
  you consolidate a stub + a full record without deleting and re-uploading the PDF (issue 4).
- **Content-aware agent reconcile.** New `POST /agents/files/known-hashes` reports which of the
  agent's file hashes still exist as library content; `reconcile` uses it so deleting a *duplicate*
  paper record (whose PDF lives on under the surviving canonical paper) no longer proposes
  un-indexing the still-present local file — only files whose content is genuinely gone are flagged
  (issue 1).
- **"Both" (hybrid) mode in the Library search.** The Library search-mode dropdown gains a **both**
  option that runs the existing unified hybrid engine (BM25F+ lexical fused with dense semantic via
  Reciprocal Rank Fusion) instead of metadata-only or semantic-only. `SavedFilter.search_mode` now
  accepts `hybrid` (`frontend/src/pages/LibraryPage.svelte`, `client.ts`,
  `backend/app/schemas/saved_filter.py`).
- **Live status indicator on the Jobs nav tab.** A lightweight 20s poll drives a semaphore dot next
  to the "Jobs" tab — red/yellow/green (mirroring the Jobs page) plus **blue** when jobs are running
  or queued, with a `[N]` queued-count badge. Shared derivation in `frontend/src/lib/jobsHealth.ts`.
- **Keyword extraction overhaul (YAKE + RAKE).** `keyword_extraction` now fuses a YAKE statistical
  keyphrase scorer (new light guarded dependency) with the RAKE scorer via Reciprocal Rank Fusion,
  then filters over-long/content-word-free/mostly-stopword phrases, trims boundary stop words, boosts
  phrases echoed in the title/abstract/section headings, and de-duplicates near-identical phrasings.
  An optional `corpus_idf` rerank + `build_corpus_idf` helper enable a future library-wide TF-IDF
  pass. Degrades to RAKE-only if YAKE is unavailable.

### Changed / Fixed

- **Find-on-web no longer reports a failed download as "downloaded".** The batch counter advanced for
  every processed candidate, so an allow-list failure (`status:"error"`) still showed "1/1
  downloaded". It now counts only `attached`/`deduped` and, when some items failed, shows
  `<ok>/<total> downloaded (N failed)` in red (issue 3).
- **Jobs tab no longer freezes or gets stuck on "Loading…".** An absent `counts`/`jobs` in the
  payload could throw during render (freezing the mounted tab); a failed first load left a permanent
  "Loading…"; a stalled poll never resolved. The payload is now normalised, the last-good status is
  kept on a refresh error (tab stays interactive), a Retry placeholder shows only on a failed first
  load, overlapping polls are guarded, and `getJobs` has a 15s timeout. A positive filter count with
  no jobs in the recent window is now explained (issue 2).
- **Jobs nav semaphore is easier to read.** Slightly larger dot with a soft glow; the green (idle)
  and blue (running/queued) states lightened and the blue leaned toward cyan so they no longer look
  alike at a glance (issue 2).
- **Agent "Scan & push" no longer creates duplicate papers for files already in the library.**
  `agent_files.ingest_manifest` now looks up an existing `File` by SHA-256 before minting a
  filename-titled `index_only` stub Work, linking to that file's existing (properly-titled) Work
  instead — closing the one ingestion path that skipped the hash-dedup every other path used.
- **DOI-collision errors now name the offending DOI and the paper that holds it.** Extraction and
  enrichment keep failing closed on a `uq_works_doi` collision (which prevents duplicate
  accumulation) but emit a clear message via the new shared `app.services.doi_conflict` module.
  Four endpoints that previously returned a raw 500 on a colliding DOI — `PATCH /works/{id}`,
  metadata select/bulk-apply/delete, and find-on-web apply-metadata — now return a clean 409.
- **Library filter panel.** The "Reset" button moved out of the collapsible "More filters" to sit
  beside "Save current filter" (it resets everything, so it shouldn't be hidden); the
  search/filter/action controls were compacted (scoped sizing, not a global button resize) to
  reclaim vertical space for the paper list.
- **Test-suite warnings silenced.** `authorization` header params converted to `Annotated`;
  `HTTP_413_REQUEST_ENTITY_TOO_LARGE` → `HTTP_413_CONTENT_TOO_LARGE`; the FastAPI-internal pydantic
  `UnsupportedFieldAttributeWarning` narrowly ignored in `pyproject.toml`. Safety suite now runs
  warning-free.

- **Visualization axis help + reference-graph help modal.** The temporal map's "About this view"
  popup now explains each X/Y axis option (what it is and how to read it), and the per-paper
  reference graph gains a "ⓘ Help" button documenting its layout, every Y-axis option, and the
  "Local reference-to-reference edges" toggle. Help text lives as testable data in
  `frontend/src/lib/viz/vizHelp.ts` and `frontend/src/lib/viz/referenceGraph.ts`.
- **Duplicates & multi-work files split into two sub-tabs.** `DuplicatesPage` now separates the
  largely-correct duplicate/version candidates from the noisy `multiwork_file` candidates into
  their own sub-tabs (with counts), so multi-work false positives no longer bury the duplicates.

- **P2/item6 — Navigation shell + Admin UI:** `frontend/src/App.svelte` now does hash-based
  routing (`#library` / `#admin`) with a nav bar and lifts the auth token so both pages share one
  authenticated client. New `frontend/src/pages/AdminPage.svelte` provides user management (create /
  role-change / disable), agent management (issue enrollment token, approve, reveal the bearer
  token once), and a last-50 audit-event list. `client.ts` gains the matching admin/agent/audit
  methods plus `uploadPdf` and `importByIdentifier`. (AUDIT P2/item6)
- **P2/item10 frontend — Upload + identifier import controls:** `LibraryPage.svelte` Sources panel
  now has a PDF file-upload control and an arXiv/DOI identifier-import field wired to
  `POST /imports/upload` and `POST /imports/identifier`. (AUDIT P2/item10)
- **Forward-looking acceptance contracts:** four *skipped* acceptance tests under
  `backend/tests/future/` (GROBID coordinates, agent teleport, local-LLM summaries, topic
  modeling) define the completion signal for upcoming `docs/WORKPLAN.md` stages; plus additional
  algorithm/library/security contract tests and a deterministic topic-modeling test.
- **`docs/WORKPLAN.md`:** authoritative, execution-ordered plan (2026-06-29) to a fully functional
  app — re-validates every open audit finding against the current code and groups remaining work
  into 7 stages, deferring minor polish to the last stage.

### Added

- **WORKPLAN_NEXT Stage 8 — runtime, GUI-managed AI providers.** The heavier semantic engines are
  now configured from the web UI instead of a config file. `ai_config` single-row table (migration
  `0018`) overlays the static `Settings` defaults (empty row == today's lexical baselines);
  `get_embedding_provider(db=…)`, summaries, and topics read the effective config. Owner API
  (`/admin/ai-config`, `/admin/ai/providers`, `/admin/ai/models[/pull]`, `/admin/ai/reindex[/status]`)
  + an Admin **"AI & Models"** panel: pick embedding/summary/topic providers + models (unavailable
  ones disabled with how-to-enable hints), set the Ollama URL, **pull/delete models** (Ollama pull /
  sentence-transformers download as RQ jobs), and **reindex** with an indexed/total readout.
  Changing the embedding model auto-queues a reindex. `docs/runbooks/ai_providers.md` documents it.
- **WORKPLAN_NEXT Stage 9 — roadmap tail.**
  - **pgvector ANN (H7), gated:** migration `0019` adds the `vector` extension + an unconstrained
    `embeddings.vector_pg` column (Postgres-only); when `pgvector_enabled` (default off) the index
    path dual-writes it and search ranks via the `<=>` operator, falling back to JSON + Python cosine
    otherwise. No new Python dependency.
  - **Postgres integration suite** (PG-gated): FK CASCADE, `timestamptz`, JSONB `@>`, and the
    pgvector ranking path end-to-end.
  - **Citation styles:** a `styled` export format renders APA / IEEE / Chicago reference lists (via
    the `style` field); surfaced in the export dialog with Preview/Copy/Download.
  - **Library-scope export** + an **Insights/graph-scope export** control.
  - **ML-extraction seam:** `extraction_backend` setting + provider detection for `nougat`/`marker`
    (opt-in; full extractor integration is a documented follow-up).
  - **API happy-path E2E** test (login → create → reindex + search → styled export → audit).

- **C3/C4 — schema hardening (migration `0017`).** Added the previously-weak foreign keys
  (`locations.agent_id` → `agents`; `references.citing_work_id`/`resolved_work_id`/`source_tei_id`
  and `citation_mentions.citing_work_id`/`reference_id`/`resolved_cited_work_id`/`source_tei_id` →
  `works`/`references`/`raw_tei_documents`, with `CASCADE`/`SET NULL`) and converted the remaining
  document JSON columns (`sources.config`, `import_batches.settings`/`stats`,
  `duplicate_candidates.signals`, `annotations.coordinates`) to **JSONB** on Postgres. The migration
  no-ops on non-Postgres dialects; the migration-parity test now also asserts every model foreign key
  is present in the migrated schema.
- **Stage 7 — dedup performance + ops.**
  - **Fuzzy-title dedup (H3):** normalized-title **blocking** (only works sharing the first title
    token are compared) bounds the former all-pairs scan; the similarity ratio uses `rapidfuzz`
    when installed and falls back to stdlib `difflib`. A full-library scan can run in the worker via
    `POST /duplicates/scan {"background": true}` (`scan_duplicates_job`).
  - **Ops:** `make prod-smoke` builds/starts the prod stack and asserts `/api/v1/health`;
    `make backup` / `make restore` dump+restore the database and managed-library volume, documented
    in `docs/runbooks/backup_restore.md`.
  - Optional providers (`rapidfuzz`, `sentence-transformers`, Ollama) documented in
    `backend/requirements.txt` as opt-in — none is installed by default.
- **Stage 7 — export polish + view audit events.**
  - **Selection / search export scope:** `POST /exports` accepts `scope_type: "selection"` (or
    `"search"`) with `work_ids`, exporting an explicit set in caller order. Wired into the library
    multi-select batch bar.
  - **Export dialog: preview + copy-to-clipboard + download.** `ExportDialog` can self-fetch and
    offers Preview / Copy / Download (shelves, racks, and selection use it); all formats
    (BibTeX/BibLaTeX/RIS/CSL-JSON/Markdown/HTML/text) supported.
  - **View audit events (§7.6):** `GET /works/{id}` records `paper.viewed` and `GET
    /files/{id}/stream` records `file.downloaded` (the stream now requires authentication, closing
    an unauthenticated-read gap), both attributed to the acting user.
- **Stage 7 — auth & egress hardening.**
  - **Failed-login throttling** (`login_throttle`): after `login_max_failures` failures for a
    username within `login_lockout_minutes`, `/auth/login` returns **429** with a `Retry-After`
    hint; a successful login clears the counter. In-process sliding window (fits the single-node
    deployment), config-driven via the `security:` block.
  - **In-app change-password** (`POST /auth/change-password`): verifies the current password, sets
    the new hash, and **revokes the user's other sessions** (keeps the current one). Surfaced in the
    web header via a "Password" dialog.
  - **SSRF-hardened enrichment**: identifiers are percent-encoded into the request path and
    redirects that leave the API's own host are refused (blocks pivots to link-local/metadata
    endpoints via a crafted DOI/arXiv id or hostile upstream).
  - **Removed the dead `guest_access_enabled` flag** (no guest access exists; the role set is
    already asserted guest-free at startup). `PARACORD_SECRET_KEY` is now a real setting reserved
    for future field encryption.
  - **SECURITY.md reconciled with reality**: bearer tokens are stored as SHA-256 hashes; there are
    no reversibly-encrypted fields today (at-rest confidentiality relies on volume encryption);
    documented the new SSRF protections.

- **Stage 6 — AI provider hardening (SPEC §20 M7).** Lexical baselines stay the default; heavier
  local providers are opt-in and degrade gracefully (no new hard dependency).
  - **Embeddings off the read path (H2):** `semantic_search` is now **read-only** — it ranks stored
    vectors and embeds the query in memory, performing no writes. Embeddings are built on import via
    a background `embed_work_job` (enqueued on work create + after enrichment) and on demand via
    `POST /search/reindex` (owner/editor). An **embedding-provider interface**
    (`get_embedding_provider`) selects `hash_bow` (default), `sentence_transformers`, or `ollama`
    from config, each storing its own `model_name` so vectors never cross providers. Per-insert
    upsert (savepoint + `IntegrityError`) makes concurrent indexing race-safe.
  - **Dual-mode search:** `POST /search/semantic` accepts `mode=embedding` (default) or `lexical`
    (term-overlap ranking, needs no embeddings).
  - **Summary provider seam + `local_llm`:** summaries support `abstract` / `extractive` (default) /
    `local_llm` (Ollama, opt-in via `summary_llm_enabled`). When the LLM is disabled/unreachable it
    degrades to extractive while still recording the requested model, prompt version, and the
    `source_sections` that fed it. Enabled `test_future_local_llm_acceptance`.
  - **Embedding/BERTopic topic backend:** `model_topics(backend="embedding"|"bertopic", …)` returns
    the deterministic clusters enriched with `representative_work_ids`, `coherence_score`,
    `outlier_work_ids`, and an optional `hierarchy`, echoing the requested `embedding_model` for
    provenance. `POST /ai/topics` exposes `backend`/`embedding_model`. Enabled
    `test_future_topic_modeling_acceptance`. The TF-IDF baseline remains the default.

### Changed / Fixed

- **Library UI pass** (`LibraryPage.svelte`, `PaperTable.svelte`, `WorkDetail.svelte`): the paper
  list and the detail pane now **scroll independently** (each column owns its scroll within a
  viewport-height layout) instead of the whole page scrolling as one. Added **multi-select**
  (checkbox column + select-all) with a batch bar — **delete**, **re-extract** (queues GROBID for
  every attached file), and **set reading status** across the selection. Added extraction/metadata
  **filters** (has-PDF, has-references, and missing-field chips: title/abstract/year/venue/doi/
  arxiv_id) backed by new `list_works` query params, and a **semantic-search** mode in the library
  search box (ranks by local embedding similarity, intersected with the active filters). Each
  attached file now shows its **content hash** (`#…`, click to copy) and an extraction-state badge.
- **Cross-reference identifier surfaced in both GUIs**: a file's SHA-256 content hash *is* the
  agent's `local_file_id`, so the server paper detail and the agent's indexed-files list now both
  show it — you can match a server paper to a file on a workstation. (No new identifier was
  invented; the existing content hash is now displayed.)
- **Re-run extraction from the server UI**: per-file **Re-extract** button in the paper detail (and
  batch re-extract in the library) calling the existing `POST /files/{id}/extract`. The worker now
  records a durable `file.status` of **`extracted`** / **`extract_failed`**, so the UI shows whether
  a successful extraction ever ran.
- **Jobs page controls**: the count tiles are now **filter buttons** (click *failed* to see only
  failed jobs; an **all** tile clears the filter) and a **Clean** button clears finished/failed job
  history via the new owner/editor `POST /jobs/clear` (running jobs are never touched).
- **Admin page no longer loads empty on hard refresh**: the page now (re)loads its data whenever the
  authenticated client is created — including the null-token → authed transition on refresh — rather
  than once before the token was read (which left it blank until the tab was re-clicked).
- **Agent web GUI usability pass** (`agent/paperracks_agent/web.py`): the page is now a
  frozen-header **tabbed** layout (Connection / Folders &amp; files / Indexed / Requests) so a long
  file list scrolls on its own instead of stretching the page. Added a **file/folder picker** that
  browses the agent's own filesystem (`GET /api/browse`, loopback + token-gated only) — kind is
  inferred from the selection, no paste required (paste-a-path still works). Managed items can be
  **edited in place** (action / teleport policy / monitored↔once) and **paused/resumed**
  (`enabled` flag, skipped on scan) via `POST /api/items/update`; folders show live **stats**
  (PDFs + subfolders found, or *missing*). Indexed files gain a **forget** action
  (`POST /api/forget`, removes the index row, leaves the on-disk file). Every action now surfaces a
  **toast** on success/error and an unhandled-handler error returns JSON (`exception_handlers`)
  instead of a silent HTML 500 — fixing the "buttons do nothing / no feedback" reports.
- **Owner can remove and rename agents** (`DELETE`/`PATCH /admin/agents/{id}`,
  `agents.delete_agent`/`rename_agent`, both audited as `agent.deleted`/`agent.renamed`): removal
  revokes the token and deletes the agent's manifest rows while leaving already-teleported/extracted
  library files intact (`file_id` is `SET NULL`). Admin UI gains Rename/Remove controls; previously
  agents could only be approved and have privileges toggled.
- **Clear error when GROBID is unreachable** (`grobid_client.GrobidUnavailableError`): a connection
  failure during extraction now reports *"GROBID is unreachable at &lt;url&gt; … start it with
  `make up-extraction`"* instead of a raw `httpx2.ConnectError: Temporary failure in name
  resolution`. The extraction profile is not part of `make up` by design.
- **§32 agent redesign (complete)** — local web GUI (S5):
  - **`paracord-agent web up`/`down`/`status`**: a minimal, self-served Starlette page bound to
    `127.0.0.1` (default port `8765`, configurable) and gated by a one-time access token printed in
    the `web up` URL — there is no off-host surface. `web up` spawns a detached process and records
    its pid/port/token in a runtime file; `web down` stops it; `web status` reports state and clears
    a stale runtime file.
  - The page + JSON API wrap the same config/state/agent_ops the CLI uses: connection (set/change
    server, enroll, save token), managed folders/files (add/remove with action + teleport policy),
    sync/refresh, per-file processing status, and teleport-request approve / reject / reject-forever
    / unblock, plus per-file re-extract. `starlette`/`uvicorn` added to the agent's deps.
- **§32 agent redesign** — agent core (S3 + S4):
  - **Persistent config + state + secrets**: tool-managed `agent.yaml` (server URL, agent id,
    managed folders/files with per-item mode/action/teleport-policy, defaults, refresh interval,
    web port), a SQLite `state.sqlite3` mapping opaque `local_file_id` → real on-disk path
    (local-only) + per-file state/blocks, and secrets via the OS **keyring if available, else a
    `0600` file** (`pip install -e agent[keyring]` enables keyring).
  - **CLI** (`paracord-agent`): `enroll`, `set-token`, `set-server`, `add-folder`/`add-file`
    (mode/action/policy) / `remove` / `list`, `sync`, `status`, `refresh`, `teleport <id>`,
    `request --list/--approve/--reject [--forever]/--unblock`, and `start` (monitor + periodic
    sync). `agent_ops` applies the per-file action on sync (index_only → manifest only;
    index_and_extract → upload-for-extraction; teleport → push), reports removed sources, and
    auto-fulfils `allow`-policy requests / auto-rejects blocked ones.
  - **Server**: `GET /agents/me` (identity + privileges) and
    `POST /agents/files/source-removed` for the agent's status + removal reporting.
- **§32 agent redesign** — server-side foundations:
  - **S1 — per-agent privileges** (migration `0015`): `can_index`/`can_extract`/`can_be_requested`
    /`processing_visibility`/`server_status_visibility` (default on) + `can_teleport` (default off,
    opt-in). `PATCH /admin/agents/{id}/privileges` (owner, audited); enforced on manifest /
    teleport-content / teleport-request; Admin UI privilege checkboxes.
  - **S2 — import actions + teleport request/block** (migration `0016`): `agent_files` gains
    `import_action`, `teleport_policy`, `virtual_path`, `processing_state`, `teleport_blocked`,
    `preview_text`. New `index_and_extract` flow (`POST /agents/files/{id}/extract`): upload →
    extract → the worker **discards the PDF** afterwards, keeping the Work, references and a
    preview. Teleport reject / reject-forever (block) / unblock endpoints; blocked files refuse new
    requests; an agent file-status endpoint (gated by `processing_visibility`); manifest carries
    per-item action/policy/virtual-path; deleted source files are kept and marked `source_removed`.
- **Agent packaging + `serve` daemon:** the agent is now an installable package
  (`pip install -e agent` provides the `paracord-agent` command) — fixes the setuptools
  flat-layout error from the `systemd/` folder by pinning package discovery to
  `paperracks_agent`. New `serve` command runs continuously (sync manifest + auto-fulfil
  teleports requested from the server UI) and a YAML config loader (`filesystem.allowed_roots`
  lists folders to index; token via `--token`/`$PARACORD_AGENT_TOKEN`/`token_file`). The agent has
  no separate GUI by design — it's managed from the server's Admin → Agents UI plus this
  CLI/daemon. Agent README rewritten with real install/run steps.
- **Stage 4.5 (batch 2) — operational visibility & management:** a **Jobs** tab backed by
  `GET /jobs` (RQ queue counts, worker count, recent jobs with errors; `available:false` when
  Redis/worker is down) — the fix for "enrichment queued but nothing happens" and "abstract not
  extracted" (both are background-worker tasks). **Delete paper** (`DELETE /works/{id}`, cascades
  dependent rows, keeps the content-addressed files) with a confirm in the detail panel.
  **Shelves/racks selection persistence** across tab switches. **Agent management**: Admin → Agents
  lists an approved agent's manifested files (`GET /admin/agents/{id}/files`) with a **Teleport**
  action and concrete agent CLI run instructions; the Import tab now explains that "Server folder"
  is server-side (aliases from `storage.server_allowed_roots`) while files on your own PC go
  through the **agent**. Also fixed smart-quote attributes left by a manual rename.
- **Stage 4.5 (batch 1) — UX refinements:** new-paper **dialog** taking title / DOI / arXiv id /
  URL (not just title); the PDF reader now opens in a full-width **modal** ("Read") with a "New
  tab ↗" option instead of the cramped side panel; the top navigation bar is **sticky**; a shared
  selection store keeps the **open paper selected across tab switches**; owner can **re-enable** a
  disabled user (`POST /admin/users/{id}/enable`). Fixed the **empty Audit-events list** (the
  client read the paginated `{items}` envelope as a bare array). Honest enrichment message naming
  the background worker. (Batch 2 — Jobs/queue tab, delete-paper, agent-management UI,
  server-folder clarity — is scheduled in `docs/WORKPLAN.md` Stage 4.5.)
- **Stage 5 — Local-agent manifest + teleport (M5):** the remote-workstation feature now works as
  a secure **agent-push** vertical. New `AgentFile` model + migration `0014_agent_files`.
  `POST /agents/manifest` (agent token) ingests opaque file identity (`local_file_id`, sha256,
  size, display label — never a server-usable path). `POST /imports/teleport` (owner/editor) marks
  an entry requested; `GET /agents/teleports/pending` (agent token) lists them; and
  `POST /agents/teleports/{local_file_id}/content` (agent token, multipart) **verifies the uploaded
  bytes against the manifest SHA-256**, stores the file content-addressed in the managed library,
  creates a Work + FileWorkLink, and enqueues extraction (hash mismatch → `teleport.failed` + 400).
  Audit events `agent.manifest_received` and `teleport.requested/completed/failed`. Agent side: an
  `AgentIndex` resolves files strictly by opaque `local_file_id`, and the raw-path
  `open_file_for_teleport` helper was **removed** — the server never sees a path and the agent
  never accepts one. CLI gains `sync` and `teleport` commands. The future acceptance test is
  rewritten to the real flow and enabled. (AUDIT B5 / H4; ROADMAP M5; WORKPLAN Stage 5)
- **Stage 4 — Frontend information architecture & UX overhaul:** the single ~10-section operator
  page is replaced by a hash-routed **tabbed shell** (`App.svelte`: Library / Import / Shelves /
  Racks / Tags / Duplicates / Insights / Admin), each tab a focused page with a one-line hint.
  The **Library** becomes a searchable master list + a `WorkDetail` panel — edit fields + Save,
  metadata-conflict review with canonical "Use this", per-work **Enrich**, **attach/open PDFs**
  (new `GET`/`POST /works/{id}/files` + `attach_uploaded_pdf_to_work`), an embedded PDF.js reader,
  and tag apply. **Shelves/Racks** become explicit master–detail managers with add-pickers scoped
  to the open item — fixing the prior overloaded-selection bug where clicking a chip silently
  primed the add-target so "Archive" appeared to enable "Add". **Import** consolidates folder /
  upload / identifier / BibTeX / **RIS** / **CSL-JSON** (`services/bibliography_import.py`,
  `POST /imports/ris` + `/imports/csl`). Cross-cutting affordances: tooltips, disabled-reason
  hints, empty-state guidance, per-tab blurbs, and confirmation on destructive actions. Login moved
  into the shell. Frontend: 11 component tests (added a shell-routing test); backend 190 passed.
  Deferred to Stage 7: per-field `user_confirmed` locking, applied-tags listing, import-queue
  panel. (AUDIT B6 / P2/item8 / P2/item10; WORKPLAN Stage 4)
- **Stage 3 — PDF.js reader + interactive citation graph (frontend):**
  `PdfReader.svelte` replaces the `<iframe>` with a `pdfjs-dist` canvas reader: page navigation,
  thumbnail rail, zoom, in-app full-text search, a citation highlight overlay driven by the
  `pdf_coordinates` from Stage 2, a References→page **Jump** control, and text-selection capture
  that prefills the Notes form with a coordinate payload (annotation `coordinates` were always
  null before). `CitationGraph.svelte` replaces the text edge-list with an interactive `cytoscape`
  canvas — click a node to open the work, selectable layouts (force/circle/grid/hierarchy), node
  size by citation degree — plus a **Graph ↔ List** render-mode toggle (the list is also the
  automatic fallback when no canvas is available). `cytoscape`/`pdfjs-dist` are lazy-loaded chunks,
  so the initial bundle is unaffected. `CitationContext` gains `pdf_coordinates`/`pdf_x..h`. New
  `PdfReader.test.ts`; `CitationGraph.test.ts` updated; `src/vite-env.d.ts` added. (WORKPLAN
  Stage 3; AUDIT B6 reader/graph)
- **B1 / Stage 2 — GROBID settings + PDF coordinate extraction:** GROBID extraction options
  (consolidation, raw citations, sentence segmentation, and which TEI elements get coordinates)
  are now driven from the `processing.grobid:` YAML block / settings instead of hardcoded flags;
  `GrobidClient` emits repeated `teiCoordinates` form fields. `tei_parser` parses the `coords`
  attribute on `<ref type="bibr">` markers into `CitationMention.pdf_coordinates` — a JSONB list
  of `{page,x,y,w,h}` boxes (multi-box for line-wrapped mentions) that **replaces** the four
  scalar `pdf_x/pdf_y/pdf_width/pdf_height` columns (migration `0013_citation_pdf_coordinates`,
  SPEC §9.3). `GET /works/{id}/citation-contexts` now returns `pdf_coordinates` plus convenience
  `pdf_x/pdf_y/pdf_w/pdf_h` for the primary box. The coordinate acceptance test
  (`tests/future/test_future_grobid_coordinates_acceptance.py`) was rewritten to be deterministic
  and is now enabled. (AUDIT B1; WORKPLAN Stage 2)
- **A3 — `make ready`/`ci` mirror CI:** `check` now also runs `test-migrations`; `ready` and `ci`
  now run `frontend-check` (install + Vitest + build). A green `make ready` implies a green CI.
  (AUDIT A3; WORKPLAN Stage 1)
- **A1 — Managed-path extraction fix (HIGH):** uploaded managed-library PDFs are now extractable.
  Added a shared resolver `app/services/file_paths.py::resolve_backend_readable_pdf_path` that
  resolves both `server_path` (validated against the server-folder source root) and `managed_path`
  (validated against `managed_library_root`) locations. `extract_and_store()` previously resolved
  `server_path` only — so uploaded PDFs (`managed_path`) failed extraction with "No server-path
  location available." Both `extraction.py` and `files.py::stream_file` now route through the
  resolver, which also adds server-root validation to extraction (it had none). The resolver
  raises `FileLocationError` (a `ValueError` subclass with a `kind` flag) so the streaming endpoint
  still returns 403 on root escapes and 404 when absent. New regression test
  `test_extract_and_store_reads_managed_path`. (AUDIT A1; WORKPLAN Stage 1)
- **C5 — Docker dev/prod split repaired:** the H5 production-build work had made `make build`
  produce production images and left misleading `Dockerfile` comments. `docker-compose.yml` now
  pins `target: development` for `api`/`worker`/`frontend`; development is the default build again.
  (AUDIT C5)
- **Tooling:** ruff checks extended to `frontend/` and `config/`; added `INSTALL.md` and
  `docs/testing/` references (`ADDITIONAL_TEST_BATTERY.md`, `OPTIONAL_MAKEFILE_TARGETS.md`,
  `TEST_DESIGN_REVIEW.md`).

- **P1/item5 — DOI SQL pushdown:** DOIs are now stored normalized (bare, lowercase, no URL
  prefix) at every write site (`bibtex.py`, `metadata_enrichment.py`, works create/
  metadata-select endpoints, identifier import).  `_same_doi_candidates` (duplicate scanner)
  and `_find_existing` (BibTeX import) now use `WHERE doi = :bare_doi` SQL equality instead
  of O(n) Python loops.  Migration `0012_normalize_dois` fixes any existing rows (Postgres:
  single UPDATE with `regexp_replace`; SQLite: row-by-row).  (AUDIT H3 partial)
- **P2/item9 — Scope-level extractive summaries:** `POST /api/v1/ai/summaries` now generates
  (and idempotently replaces) an extractive summary over all abstracts in a library/shelf/rack
  scope, replacing the previous `{"status":"todo"}` stub.  Returns `entity_type`, `entity_id`,
  `text`, model provenance, and `work_count`.  Empty scopes (no abstracts) return 400.  Stored
  in the existing `summaries` table; library scope uses the nil UUID as entity_id sentinel.
- **P2/item10 (partial) — Import expansion:** `POST /api/v1/imports/upload` accepts a
  multipart PDF, writes content-addressed to `managed_library_root` (SHA-256 dedup), creates
  `File/Location(managed_path)/Work/FileWorkLink`, and enqueues GROBID extraction.  Magic-byte
  check rejects non-PDFs; 200 MB hard limit.  `POST /api/v1/imports/identifier` creates or
  re-enriches a work from an arXiv id or DOI (idempotent on re-import).
  `GET /api/v1/files/{id}/stream` updated to also serve `managed_path` locations with
  path-escape validation.  RIS/CSL JSON import deferred.
- **P0/H5 — Multi-stage production builds:** `backend/Dockerfile` now has `development`
  (uvicorn `--reload`, bind-mount overlay) and `production` (gunicorn + UvicornWorker,
  runtime-only dependencies, no source bind-mount) stages. `frontend/Dockerfile` adds a
  `builder` stage (Vite build) and a `production` stage (nginx:1.27-alpine serving the
  pre-compiled Svelte SPA with gzip + immutable cache headers for hashed assets and SPA
  `try_files` routing). `docker-compose.prod.yml` is a compose override that selects
  `production` targets for `api`, `worker`, and `frontend` and removes all source-code
  bind-mounts. `make prod-build`, `make prod-up`, `make prod-down` targets added. Config
  default changed to `environment: production`. (AUDIT H5)
- **P0/C3 — FK declarations in ORM models:** added `ForeignKey(…, ondelete=…)` to all
  model columns whose migrations already declare a FK constraint (`Location.file_id /
  source_id`, `WorkVersion.work_id`, `FileSegment.file_id`, `FileWorkLink.file_id /
  work_id / version_id / segment_id`, `ShelfWork.shelf_id / work_id`, `RackShelf.rack_id /
  shelf_id`, `TagLink.tag_id`, `ImportBatch.source_id`). Autogenerate is now clean for
  these tables and cascade/SET-NULL behavior is declared at the ORM layer. (AUDIT C3)
- **P0/C4 — JSONB for audit events:** `AuditEvent.details` now uses
  `JSON().with_variant(JSONB(), "postgresql")` so the Postgres column type matches the
  `postgresql.JSONB` declared in migration `0001` and GIN/`@>` queries work. (AUDIT C4)
- **P0/H1 — Pin `httpx2==2.4.0`:** both `backend/requirements.txt` and
  `agent/requirements.txt` now pin the Pydantic-maintained fork at `2.4.0` for a
  reproducible build. (AUDIT H1)
- **P0/H4 — Agent stub security:** `POST /agents/manifest` and `POST /agents/teleport/{id}`
  now require a valid approved-agent bearer token (`require_agent_token` dep in
  `app/api/deps.py`) and return **501 Not Implemented** instead of an unauthenticated
  500/200. The deprecated `POST /agents/register` stub now returns 410 Gone. Removed the
  dead `GET /citations/contexts` stub (`{"status":"todo"}`) from the OpenAPI surface.
  (AUDIT H4)
- **P1/item4 — `works.arxiv_base_id` + schema identifiers:** new `Work.arxiv_base_id`
  column (String 64, indexed) stores the version-less arXiv base id so version-collapsing,
  duplicate detection, and graph edge resolution can key on the stable id without runtime
  string manipulation. Partial unique indexes on `arxiv_base_id WHERE NOT NULL` and
  `doi WHERE NOT NULL` enforce the identifier uniqueness SPEC §9.2 requires. Migration
  `0011_schema_identifiers` adds the column, backfills existing rows from `arxiv_id`
  (Postgres only — tests build from metadata), and creates the partial unique indexes.
  `storage.py` and `bibtex.py` now populate `arxiv_base_id` at creation time.
  Shared helper `app/services/identifiers.py` provides the `arxiv_base_id()` normalizer.
  (AUDIT P1 item 4 / PROGRESS data-model divergences §2)
- **P1/item4 — `references.resolution_status`:** new `Reference.resolution_status` column
  (`unresolved | local_match | external`) as required by SPEC §12.5 for citation-graph
  edge classification. Migration `0011` adds the column with server default `unresolved`.
  `build_citation_graph` now persists the resolution result on each `Reference` row so
  subsequent graph builds reuse the persisted status. (AUDIT P1 item 4)
- **Dedup SQL pushdown:** `_same_arxiv_candidates` now filters by the indexed
  `arxiv_base_id` column via SQL when the column is populated, eliminating the O(n) Python
  scan over all arXiv works for per-work duplicate detection. (AUDIT H3 partial)

- Added a Postgres migration↔model parity test (`backend/tests/test_migration_parity.py`, AUDIT
  C2): it creates a throwaway database, runs `alembic upgrade head`, and asserts every model table
  and column exists in the migrated schema — the guard that would have caught the missing-migration
  bug above. It self-skips when no Postgres is reachable (so the SQLite-only run and current CI
  stay green), runs via `make test-migrations`, and the CI `backend` job now has a Postgres service
  + `DATABASE_URL` so it runs there. Also set `path_separator = os` in `alembic.ini` to clear an
  alembic deprecation warning.
- Added `docs/AUDIT.md` — a full functional + implementation audit (2026-06-25) covering
  spec-fidelity per capability, correctness/infra/security/data-model findings with severities, and
  a prioritized "Path to a fully functional app" backlog. Refreshed `docs/architecture/api_surface.md`
  and `data_model.md` (they were pre-M2 stubs) to match the real routes/tables, and added a top-of-file
  audit pointer + honest framing (semantic search / topics are lexical/TF-IDF stand-ins) to
  `PROGRESS.md`.

- Added M7 lightweight topic modeling (no ML dependency): `POST /api/v1/ai/topics` clusters a
  library/shelf/rack scope's works into keyword-labelled topics (`services/topic_modeling.py` —
  TF-IDF + a small deterministic k-means, fully local/no-egress, deterministic for a given input
  order) and persists `TopicAssignment` rows stamped with a `topic_model_id` (re-running a scope
  replaces them). Returns each topic's keyword label and work count. The default tier
  deliberately avoids BERTopic/sentence-transformers (a real embedding/BERTopic backend can
  replace `model_topics` later). The Svelte library gained a "Model topics" panel for the current
  scope. Covered by `test_topic_modeling.py` and the enabled forward-looking
  `test_topic_model_on_shelf_suggests_tags`. With this, all `test_future_milestones.py`
  acceptance contracts are enabled (no skipped tests remain).
- Added M5 local-agent enrollment (owner-gated, SPEC §11.2): an owner mints a single-use,
  expiring enrollment token (`POST /api/v1/admin/agents/enroll-token`); the agent presents it
  unauthenticated (`POST /api/v1/agents/enroll-request`, returns 202 with a pending agent); an
  owner approves it (`POST /api/v1/admin/agents/{id}/approve`), which mints the agent's scoped
  access token (returned once). New `agents` and `agent_enrollment_tokens` tables (migration
  `0009_agents`); all tokens are stored hashed (sha256) and every step writes an audit event
  (`agent.enroll_token_issued` / `agent.enroll_requested` / `agent.approved`). The `/agents`
  router is no longer behind the user-session dependency since agents authenticate with their
  own token; the legacy `/agents/register` stub now points at the new flow. Covered by
  `test_agents.py` and the enabled forward-looking `test_agent_enrollment_requires_owner_approval`.
- Added M3 BibTeX import: `POST /api/v1/imports/bibtex` ingests pasted/uploaded BibTeX into
  works (`services/bibtex.py` — a small dependency-free balanced-brace parser handling
  `{…}`/`"…"`/bare values, nested braces, and `@comment`/`@string`/`@preamble`). Authors are
  recorded as a `bibtex`-sourced MetadataAssertion, venue/year/DOI/arXiv (from
  `archiveprefix`+`eprint`) are mapped onto the work, and an `ImportBatch` + `import.bibtex`
  audit event capture the run. Entries are de-duplicated against the library by normalized DOI
  and title (re-import is a no-op); imported works are left `user_confirmed=False` so enrichment
  can still fill gaps. The Svelte library gained a paste-BibTeX import box. Covered by
  `test_bibtex_import.py` and the enabled forward-looking `test_import_bibtex_creates_works`.
- Added M7 semantic search: `POST /api/v1/search/semantic` ranks works by cosine similarity to
  a free-text query (`services/semantic_search.py`). The default embedder is a deterministic,
  dependency-free feature-hashing bag-of-words model (`services/embeddings.py`) — fully local
  (no network/egress) and stable across processes; a real local model
  (sentence-transformers / Ollama) can later be plugged in behind the same interface.
  Embeddings of each work's title + abstract are cached in a new `embeddings` table (vectors
  stored as JSON and ranked with Python cosine, so the same code path works on SQLite and
  Postgres — a pgvector index is a future scaling step) and computed lazily on the first search.
  Migration `0008_embeddings`. The Svelte library gained a semantic search box that opens the
  matched work. Covered by `test_semantic_search.py` and the enabled forward-looking
  `test_semantic_search_returns_neighbours`.
- Added M7 local paper summaries (tiers 0 and 1, no LLM, no network):
  `POST /api/v1/works/{id}/summaries` and `GET` (`services/summarization.py`). Tier 0
  (`abstract`) stores the work's abstract verbatim; Tier 1 (`extractive`) runs a dependency-free
  frequency-based extractive summarizer over the abstract plus extracted GROBID body text
  (`tei_parser.extract_body_text`). Summaries are stored with provenance (`model_name` +
  `prompt_version`) and replace any prior summary of the same type (idempotent re-runs). The
  Svelte library gained an Abstract/Extractive summary panel for the selected work. Covered by
  `test_summarization.py` and the enabled forward-looking `test_local_summary_records_provenance`.
  Tier 2 (local-LLM abstractive via Ollama) is intentionally left for later.
- Added the M6 scoped citation graph: `POST /api/v1/graphs/citation` builds a node/edge graph
  for a library/shelf/rack scope (`services/citation_graph.py`). Edges are derived from
  extracted `Reference` rows resolved to local works (persisted `resolved_work_id`, else an
  exact DOI/arXiv-base match); `node_mode=local_only` keeps in-scope edges while
  `include_external` also surfaces cited works not yet in the library, plus a summary
  (node/edge/external/unresolved counts). Self-citations are dropped and repeated citations
  raise the edge weight. The Svelte library gained a lightweight graph panel (summary + edge
  list, scoped to the selected shelf/rack or whole library), replacing the placeholder. The
  previously-stub `GET /graph` endpoint is now `POST /graphs/citation`. Covered by
  `test_citation_graph.py`, `CitationGraph.test.ts`, and the enabled forward-looking
  `test_shelf_citation_graph_is_scoped`.
- Added OpenAlex and Semantic Scholar metadata-enrichment connectors (identifier-based, like
  the existing arXiv/Crossref ones): OpenAlex is queried by DOI (reconstructing its
  inverted-index abstract) and Semantic Scholar by arXiv id or DOI. Both are wired into
  `enrich_work` behind new `enrichment_openalex` / `enrichment_semantic_scholar` settings (and
  `metadata_enrichment.sources.*` config keys, now read by the loader), default **off**. They
  record provenance assertions and promote trusted fields exactly like the existing sources,
  send only the bibliographic identifier (no titles/abstracts) so the data-egress policy is
  preserved, and are covered by parser + `enrich_work` tests.
- Hardened the M4 duplicate/version review: duplicate-candidate API responses now include
  human-readable entity labels, a summary string, and a `suggested_target_work_id`. When a
  merge/link action is applied without an explicit target, the surviving canonical work is now
  chosen by heuristic (user-confirmed → latest arXiv version → metadata completeness) instead
  of arbitrary id order. Actions are refused on already-resolved candidates, with an extra
  guard that prevents the same file from being split twice (which would create duplicate
  works). The Svelte review panel surfaces the labels/summary and uses the suggested target.
  Covered by new cases in `test_duplicates_api.py`.
- Expanded citation export to all planned formats: `/api/v1/exports` now renders BibTeX,
  BibLaTeX, RIS, CSL JSON, Markdown, HTML, and plain text (previously only BibTeX/text).
  Exports include authors (resolved from the best metadata assertion), use `authorYEAR`
  citation keys, and return a per-format filename + content type. A `paper.exported` audit
  event is now recorded for every export (SPEC §7.6/§8.13). The Svelte library gained a
  working export control (format picker + file download) for the selected shelf or rack,
  replacing the placeholder `ExportDialog`. Covered by `test_export_formats.py` (8 cases) and
  `ExportDialog.test.ts`.
- Added frontend component tests (Vitest + jsdom + Testing Library, `vitest.config.ts`,
  `make frontend-test`, and a CI `frontend` job): `main.test.ts` executes the entrypoint in
  a DOM and asserts the app mounts into `#app` (regression guard for the Svelte-5 mount bug),
  and `App.test.ts` checks the sign-in view renders. These run the real Svelte mount, so they
  catch client-render failures a raw-HTML fetch cannot.
- Expanded the test suite with high-level coverage: a shared `conftest.py` harness
  (FastAPI `TestClient` over in-memory SQLite) and three layers — service/unit tests,
  user-oriented API flow tests (`test_api_flows.py`: import → organize → search → read,
  metadata review, citation contexts), a security suite (`test_api_security.py`: RBAC matrix,
  no-guest, auth-required, account-enumeration, audit, PDF path-escape), and skipped
  forward-looking tests for M3+ (`test_future_milestones.py`, each with an `ENABLE WHEN`
  note to turn on as its milestone lands). ~75 passing + 8 skipped backend.
- Added the M4 duplicate/version review queue foundation: `duplicate_candidates` model and
  migration `0006_dupe_candidates`, plus a DB-backed scanner for same-DOI, same-arXiv-base,
  fuzzy-title, text-fingerprint, and exact-file candidates with idempotent candidate upserts.
- Added duplicate review API endpoints under `/api/v1/duplicates` to list candidates, trigger
  scans, and mark candidates `accepted`, `rejected`, `ignored`, or back to `open`.
- Added an initial Svelte duplicate-review panel: list/open-status filter, scan trigger, signal
  display, and accept/reject/ignore status controls backed by `/api/v1/duplicates`.
- Added backend duplicate-review actions: merge work candidates without deleting source works,
  link a candidate as a `WorkVersion`, mark file candidates as duplicate copies, keep separate,
  and ignore, with audit events and focused tests.
- Updated the Svelte duplicate-review panel to call explicit backend actions (merge, link
  version, mark duplicate, keep separate, ignore) and reopen resolved candidates.
- Added initial multiwork-file candidate detection: long/proceedings-like files or previews with
  repeated abstract/reference markers now enter the duplicate review queue as `multiwork_file`.
- Added the backend `split_file` review action for `multiwork_file` candidates: supplied
  segments create `FileSegment`, `Work`, and `FileWorkLink` rows with multiwork warning state.
- Added frontend split-file controls for `multiwork_file` candidates using line-based
  `Title | start page | end page` segment entry.
- Added an embedded reader surface that loads authenticated PDF streams as object URLs and
  includes a References tab backed by extracted citation contexts.
- Added separate reader annotation storage: `annotations` model/migration and
  `GET`/`POST /api/v1/works/{work_id}/annotations`; enabled the M3 annotation acceptance test.
- Added reader annotation UI: the embedded reader now has a Notes tab that lists annotations
  and creates note/highlight/page-anchor/citation-note rows through the work annotation API.
- Added initial bibliography export: `/api/v1/exports` resolves work/shelf/rack scopes and
  renders BibTeX (plus plain-text fallback); enabled the shelf BibTeX acceptance test.
- Added raw TEI storage and citation mention persistence for M2 extraction: migration
  `0005_raw_tei_mentions`, `RawTeiDocument`, source-TEI links on references/mentions, TEI
  body `ref type="bibr"` parsing with sentence contexts, and idempotent persistence of
  `CitationMention` rows from GROBID TEI.
- Added `GET /api/v1/works/{work_id}/citation-contexts` to expose persisted citation
  mentions with their extracted reference metadata.
- Added an initial frontend citation-context panel for the selected work in the Svelte
  library workspace.
- Added external metadata enrichment (arXiv + Crossref): identifier-based connectors in
  `services/metadata_enrichment.py` that record provenance-aware `MetadataAssertion`s and
  promote trusted external fields over GROBID when the work is not user-confirmed;
  arXiv-id-from-filename detection at import; an automatic import → extract → enrich chain
  plus a `POST /works/{id}/enrich` trigger and an `enrich_work_job` worker job; a
  review/conflict surface (`GET /works/{id}/metadata`, `POST /works/{id}/metadata/select`);
  and enrichment config loading. Validated live: arXiv auto-corrected GROBID's mis-detected
  title for 1706.03762 to "Attention Is All You Need", with the conflict surfaced.
- Wired the GROBID extraction pipeline into the running system: an RQ queue
  (`app/workers/queue.py`, best-effort enqueue), a `worker` compose service running
  `rq worker`, enqueue-on-import, and a `POST /files/{id}/extract` trigger. The
  `extraction` profile now uses the lightweight `lfoppiano/grobid:0.8.0` CRF image
  (~0.5 GB) instead of the ~12 GB deep-learning image. Validated end-to-end on real arXiv
  PDFs (Transformer + ResNet): HTTP import → worker → live GROBID → 90 references and
  abstracts persisted asynchronously.
- Started the M2 extraction layer: a real GROBID TEI parser (`services/tei_parser.py` —
  title/abstract/DOI/authors/references via lxml), a provenance-aware persistence service
  (`services/extraction.py`) that records `MetadataAssertion`s and `Reference`s and only
  promotes canonical title/abstract/DOI when the work is not user-confirmed, Alembic
  migration `0004` for `references`/`citation_mentions`/`metadata_assertions`, a synchronous
  GROBID client method, and the wired `extract_pdf_job`. Covered by a TEI fixture and tests,
  and validated against real Postgres (import → extract → assertions/references).
- Started the M1 core-library backend slice: added `sources`, `import_batches`,
  `shelf_works`, `rack_shelves`, and `tag_links` models plus an Alembic migration for the
  core file/work/source/organization tables.
- Added alias-only configured server-folder sources and folder imports. Imports scan a
  configured root, SHA-256 hash PDFs, create File/Location/Work/FileWorkLink rows, extract a
  PyMuPDF first-page text preview when available, deduplicate by file hash, and audit-log
  source creation/import completion.
- Added basic backend endpoints for sources, folder import batches, file metadata, manual
  work create/edit/search, shelves, racks, memberships, and tags.
- Added focused M1 service tests for configured-root alias handling, server-folder import
  persistence, deduplication, and audit logging.
- Added a Compose-managed frontend service (`frontend/Dockerfile`) with Docker-contained
  Node dependencies, `make frontend-dev`, `make frontend-build`, and runbook notes.
- Added the initial M1 Svelte workspace: login, library table, reading queue, source import
  controls, manual work creation, shelf/rack/tag controls, and file preview list.
- Added backend read endpoints for file listing, shelf works, and rack shelves to support
  the M1 frontend views.
- Added work search filters for shelf, rack, and tag membership and exposed them in the
  frontend library toolbar.
- Added authenticated PDF streaming for configured server-folder file locations, with
  root-escape protection and a frontend file-panel action that opens the streamed PDF.
- Added archive/unlink operations for shelves, racks, shelf-work memberships,
  rack-shelf memberships, and tag links, with matching frontend controls and tests.
- Added a containerized development & evaluation stack: `backend/Dockerfile` (api server) and `agent/Dockerfile` (client), `docker compose` services for `postgres`/`redis`/`api`/`agent` (with healthchecks, a smart entrypoint that runs migrations only for the server, and opt-in `extraction`/`ai` profiles for GROBID/Ollama), `backend/requirements-dev.txt`, a `ci` GitHub Actions workflow (lint + test on Python 3.12), `make` targets (`build`/`up`/`down`/`test`/`lint`), and `docs/runbooks/dev_containers.md`. The full test suite (23 tests) and a live auth/role smoke test now pass in-container against real Postgres.
- Added role-based authorization (`require_roles` / `require_owner` dependencies) and owner-only admin endpoints under `/api/v1/admin`: list/create users, change a user's role, disable a user (with last-active-owner protection), and paginated audit-event access. New `user.created` (admin API), `user.role_changed`, and `user.disabled` audit events.
- Added an account-enumeration mitigation to login (constant-time bcrypt verification on the unknown/disabled-user path) and a startup assertion that no guest role is present in `security.allowed_roles`.
- Added a reusable FastAPI current-user dependency for bearer-token authentication.
- Protected all non-health, non-login API routers with the authentication dependency.
- Added API dependency tests for valid, missing, and invalid bearer tokens.
- Added revocable server-side bearer sessions for login/logout.
- Added persisted audit events for login success, login failure, and logout.
- Added password-reset session revocation for server-console credential recovery.
- Added authentication service tests for credential validation, token hashing, revocation, and audit persistence.
- Added Alembic configuration and the initial `users`/`audit_events` migration.
- Added a `user_sessions` migration for revocable tokens.
- Added `make migrate` for applying backend database migrations.
- Added server-console admin script tests using an isolated SQLite database.
- Added backend YAML settings loading with environment-variable override precedence.
- Added bcrypt password hashing and verification helpers.
- Added DB-backed server-console owner bootstrap and password-reset script skeletons with audit events.
- Added backend tests for settings loading and security helper behavior.

### Changed

- Pinned frontend dependencies in `frontend/package.json` (svelte `^5.56.4`, vite `^8.1.0`,
  `@sveltejs/vite-plugin-svelte` `^7.1.2`, typescript `^6.0.3`, pdfjs-dist `^6.0.227`,
  cytoscape `^3.34.0`) instead of `latest`, so a future major bump can't silently reintroduce
  a framework mismatch (the cause of the blank-page bug).
- Restructured the Makefile and runbooks for clearer test/lint/format workflows: tests run per component (`test-api` in the api container, `test-agent` in the agent container, `test` runs both) rather than forcing agent code into the server image; lint/format are host-local (`lint`/`fix`) since Ruff is pure static analysis; added `up-extraction`/`up-ai` profile targets for GROBID/Ollama; fixed `make db-shell` to expand `$POSTGRES_USER`/`$POSTGRES_DB` inside the Postgres container; added `agent/pyproject.toml` so the agent's pytest is properly configured. Updated `README.md`, `docs/runbooks/development_setup.md`, and `docs/runbooks/dev_containers.md` to match.
- Bumped the target runtime to Python 3.12 (`pyproject.toml`, Dockerfiles, CI) and the Postgres image to `pgvector/pgvector:pg17`. `make test` now runs in the api container by default (`make test-local` runs on the host).
- Reconciled `SPECIFICATION.md` with the implemented scaffold: roles are `owner | editor | reader` (was `owner | member`), the repository-layout and per-agent work split now defer to `WORK_SPLIT.md` (A–J) and the actual `backend/ frontend/ agent/` layout, config examples use port 8000 and bcrypt, and the milestone plan was re-ordered to front-load the single-machine loop (the local agent moved to M5).
- Rewrote `ROADMAP.md` as a condensed mirror of the canonical `SPECIFICATION.md` §20 milestones and updated `PROGRESS.md`'s next-milestone section accordingly.
- Integrated supporting open-source tools into the spec: PyMuPDF (fast preview), YAKE/KeyBERT (keywords), OCRmyPDF/Tesseract (OCR fallback), anystyle/refextract (reference fallback), biblio-glutton (local consolidation), Nougat/Marker (optional ML extraction), and Zotero translation-server (URL metadata).
- Added usability features to the spec: reading queue, related-papers suggestions, live shelf/rack bibliography, and annotation/note full-text search.
- Made topic modeling and body summaries tiered (lightweight default, heavier opt-in): BERTopic is now optional and off by default with lightweight keyword extraction as the default; paper body summaries are Tier 0 abstract → Tier 1 extractive Method/Experiment/Results (sumy/TextRank, no LLM) → Tier 2 opt-in local-LLM abstractive (Ollama). Reflected in `config/server.example.yaml`.

- Made all timestamps timezone-aware end to end. Write/default sites use `datetime.now(UTC)`
  (replacing deprecated `datetime.utcnow()`), every model `DateTime` column is now
  `timezone=True`, and migration `6a310e33c3d6` converts the existing Postgres columns to
  `timestamptz` (interpreting stored naive values as UTC; introspects `information_schema`, so
  it covers every column and is a deterministic, perfectly reversible no-op on SQLite). The
  hand-written migration replaced an autogenerated draft that had bundled in destructive,
  unrelated changes (dropping every foreign key, a JSONB→JSON downgrade, table creates).
- Switched the outbound HTTP clients (`services/grobid_client.py`,
  `services/metadata_enrichment.py`) from `httpx` to its successor `httpx2`, updated in
  `backend/requirements.txt` and `agent/requirements.txt`.

### Fixed

- Fixed a prod-breaking schema gap: the `summaries` and `topic_assignments` model tables had no
  Alembic migration, so a fully migrated Postgres lacked them and the M7 summary/topic endpoints
  would raise `UndefinedTable` in production (tests missed it because they build the schema from
  `Base.metadata` on SQLite). Added migration `0010_summaries_topics`, verified on Postgres. Found
  during the full-project audit (`docs/AUDIT.md` C1).
- Fixed `auth.get_active_session` raising `TypeError: can't compare offset-naive and
  offset-aware datetimes`: session `expires_at` values are normalized with a new `_as_utc()`
  helper before comparison against `datetime.now(UTC)`, so the check is robust on backends that
  round-trip naive datetimes (SQLite, or a not-yet-migrated Postgres column). This had broken
  every authenticated request and ~15 tests after the timezone migration.
- Agent tests now actually run in Docker. They were silently skipped (the `agent/` tree isn't in the api container), and the explicit-path Make target failed outright; agent tests now run in the agent container, so `make test` exercises both backend (48) and agent (2) suites.
- Made the `citation`/`metadata`/`ai` models use the generic `sqlalchemy.Uuid` instead of `postgresql.UUID`, matching the rest of the models so their tables can be created under SQLite (tests) as well as Postgres.
- Fixed invalid `docker-compose.yml` YAML (the `${VAR:?…}` default messages contained an unquoted colon — "mapping value is not allowed in this context"); the guarded values are now quoted.
- Replaced unmaintained `passlib` with the maintained `bcrypt` library in `core/security.py` — `passlib` 1.7.x raised `AttributeError: module 'bcrypt' has no attribute '__about__'` against modern bcrypt, breaking all password hashing. Added an explicit 72-byte length guard.
- Fixed Alembic revision ids that exceeded the 32-char `alembic_version` column (migrations failed with `value too long for type character varying(32)` on a real Postgres).
- Made `test_config` hermetic (it now clears ambient settings env vars, so it passes inside the api container where `DATABASE_URL` is set).
- Registered the `app.models.ai` models (`Summary`, `TopicAssignment`) in `models/__init__.py` so the `summaries`/`topic_assignments` tables are no longer silently omitted from `Base.metadata` (Alembic autogenerate and `create_all`).
- Fixed `make test` collection: switched pytest to `--import-mode=importlib` and added the repo root to `pythonpath` so the two `test_security.py` modules (backend + agent) coexist and `scripts` is importable; added `scripts/__init__.py`.
- Made the `docker-compose.yml` Postgres credentials fail fast with a clear message (`${VAR:?…}`) when `.env` is missing, instead of silently breaking `make dev-up`.
- Refreshed `FILE_TREE.md` (secrets-policy files, CI workflow, new scripts) and annotated the not-yet-created owned paths in `WORK_SPLIT.md`.
- Improved `scripts/check_secrets.py`: in source files only quoted string literals are flagged (unquoted code references like `password=payload.password` no longer false-positive), config-style files still flag unquoted values, and prefixed key names (`DB_PASSWORD`, `access_token`, …) are now detected.

### Security

- Added a "Data egress and privacy" section to `SECURITY.md` and `SPECIFICATION.md` (§7.8): only opt-in, audit-logged bibliographic identifiers ever leave the machine; no PDF contents, collection structure, filesystem paths, or bulk exports are transmitted.
- Kept credential recovery as a server-console operation only.
- Owner bootstrap now refuses to create a second owner account.
- Added an authoritative secrets-and-credential-handling policy (`docs/runbooks/secrets_management.md`) and wired it into `SECURITY.md`, `AGENTS.md`, `HINTS_FOR_AGENTS.md`, `CONTRIBUTING.md`, the README, and the LaTeX security chapter.
- Added `scripts/check_secrets.py`, a dependency-free secret scanner, with `make check-secrets`, a pre-commit configuration, a plain git-hook installer (`scripts/install_git_hooks.sh`), and a `secret-scan` GitHub Actions workflow.
- Hardened `.gitignore` to exclude key material (`*.pem`, `*.key`, `secrets/`, token files) and any `.env.*` except `.env.example`.
- Removed the hardcoded Postgres dev password from `docker-compose.yml`; credentials now come from `.env`.

## [0.0.0] - 2026-06-23

### Added

- Created initial repository scaffold.
- Added FastAPI backend directory layout.
- Added local workstation agent directory layout.
- Added web frontend directory layout.
- Added Docker Compose development skeleton.
- Added configuration examples for server and agent.
- Added project progress report, agent guide, work split, and implementation hints.
- Added LaTeX documentation source tree and compile script.
- Added server-local credential recovery design and placeholder script.
- Added GROBID, PostgreSQL, Redis, pgvector, PDF.js, BERTopic, and local LLM integration placeholders.

### Security

- No guest role is defined.
- All filesystem access is routed through configured roots, managed library storage, or local agent file IDs.
- Credential recovery is specified as a server-console operation only.

### Known incomplete areas

- Database migrations are placeholders.
- API endpoints are skeletal.
- Frontend components are placeholders.
- GROBID parsing, citation graph construction, export rendering, topic modeling, and summarization are not implemented yet.

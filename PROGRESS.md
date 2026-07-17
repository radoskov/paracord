# Progress Report

> **Planning & audit docs (read these first):** `docs/AUDIT.md` — current & deferred technical
> issues (open list + resolved archive); `docs/WORKPLAN.md` — the forward-looking backlog, with the
> open product/architecture choices awaiting the owner's call collected at its end; `docs/WORKPLAN_ARCHIVE.md`
> — completed workplan history. (The former `DISCUSSIONS.md` and `ARCHIVED_AUDIT_LOG.md` were
> consolidated into these on 2026-07-08; their originals live in the gitignored
> `documentation_archive.zip`.) One rule every contributor must know: models and
> migrations are **separate** schema definitions — change a model → write + verify the migration
> on Postgres (parity + autogenerate-clean tests enforce this).

## Degraded badge + scope-summary timeout fix + import preview links (2026-07-17)

- **Degraded extraction is now visible AND summarizable** (follow-up to the GROBID fallback):
  the fallback additionally extracts the PDF's plain text with PyMuPDF and injects it as a
  single "Full text" body section (XML-escaped, control-chars stripped), so AI summaries,
  chunking and semantic search process the whole document — verified live on open_ease.pdf
  (chunks now carry "Full text" sections). Every degraded TEI carries a marker; migration
  **0077** adds `files.extraction_degraded` (set/cleared by `store_parsed_extraction`), shown as
  a warning badge in the Files panel and a `degraded` token in the library Badges column.
  Applied to the live DB; open_ease re-extracted (badge visible, body chunked).
- **Whole-library scope-summary crash root-caused** ("Work-horse terminated unexpectedly;
  waitpid returned None" at 7/85 after EXACTLY 16 min): the queue's **900s default job timeout**
  fired mid-LLM-call, the degrade-to-extractive `except Exception` swallowed
  `JobTimeoutException`, the loop kept running, and RQ's monitor hard-killed the horse — no
  traceback, and the end-of-job-commit design lost the 7 finished per-paper summaries to the
  rollback. Fixes: scope jobs get `job_timeout=6h` (coalesced → no stacking); a shared
  `is_abort_exception` guard makes ALL degrade-and-continue handlers re-raise user cancels and
  RQ timeouts (clean failure with the real reason); and the map loop commits after every paper
  when running as a job, so per-paper progress survives any crash and is reused on re-run.
- **Import preview UX**: collision warnings link each matching paper's title (opens it in the
  Library via `pendingLibraryOpen` to verify before choosing create/attach/skip), and every
  staged row gets a "preview ↗" that streams the uploaded PDF into a new tab
  (`StagingItemRead.file_id`; staged files are loose → see-able). Verified live end-to-end.

## GROBID full-text crash fallback + Files-panel button wrap (2026-07-17)

- **`open_ease.pdf` extraction fixed**: GROBID's body formatter crashes on this PDF with an
  internal 500 (`TEIFormatter.toTEITextPiece: fromIndex(9) > toIndex(8)`) no matter the request
  parameters, and a PyMuPDF re-save doesn't help — but its header and references parsers handle
  the same file fine. `GrobidClient` now degrades on a full-text 5xx: it calls
  `processHeaderDocument` + `processReferences` and splices the `listBibl` into the header TEI,
  so the standard `parse_tei` path still yields title/authors/abstract/DOI **and the full
  bibliography** (40 references for this paper); only body sections and their citation contexts
  are lost. Header-also-fails re-raises the original error. Verified live: the real PDF imports,
  the worker logs the "degrading to header+references" warning, and extract → enrich → chunk →
  embed all complete. 4 client tests added.
- **Files panel button overflow fixed**: `.file-actions` was `flex-shrink: 0` with no wrap, so
  the seven per-file buttons (Read … Remove) pushed out of the page in a narrow paper-view
  column; they now wrap into extra rows (verified at 1100px viewport).

## PDF-import collision resolution: append-to-existing + DOI editing (2026-07-17)

- **"Append PDF" action**: the Preview & choose table now resolves collisions per file — an
  action dropdown replaces the Create checkbox when a staged PDF matches existing papers:
  Skip / Create new paper / Attach PDF to \<matching paper\>. Same-DOI (and same-PDF) collisions
  refuse Create-new (attach or skip only, per the owner's rule); title-only matches allow BOTH
  (workshop vs. journal version). Backend: `append` decision on the staging commit — the file is
  linked to the chosen paper (ACL-checked) and the stored preview TEI is applied only when that
  paper has no extraction yet, so a PDF-less record gains full extraction while an already-
  extracted paper just gains an alternate file. Verified live end-to-end: knowrow.pdf attached
  through the real UI to a PDF-less same-DOI record; 100 references landed, no worker errors.
- **No more post-append DOI crashes**: `store_parsed_extraction` never promotes a DOI another
  live paper owns (previously a unique-violation 500 on re-extraction or append-to-title-match);
  it lands as a reviewable metadata assertion instead.
- **Book-vs-chapter same-DOI batch fixed**: the commit pre-checks accepted items' DOIs and
  refuses with a message naming the actual owner ("same DOI as 'X' — choose Attach PDF to it")
  or the sibling file in the batch ("same DOI as 'book.pdf' in this batch — edit/clear one DOI"),
  replacing the old "could not create paper (possible duplicate DOI)". The preview warns both
  rows about the intra-batch clash, and each row's DOI is editable/clearable inline
  (`PATCH /imports/staging/{batch}/items/{item}`); the override supersedes GROBID's original at
  commit, so a cleared DOI stays cleared.

## Title taming + column width ratios + error envelope (2026-07-17)

- **ALL-CAPS titles tamed at display time** (`lib/titleCase.ts`, used by PaperTable cells + the
  WorkDetail heading): triggers only on >10-char titles with >95% uppercase letters, then title-
  cases with small words lowercased, digit tokens/Roman numerals kept, and short words missing
  from an embedded common-word list kept caps as likely acronyms (DNA, SVM, LSTM stay; DEEP,
  DATA get cased). Display-only — stored titles/search/dedupe/exports untouched; the edit form
  shows the raw title. 11 unit tests.
- **Columns dialog: per-column width ratios + divider toggle.** Width is a ratio (relative
  weight): the table divides its actual width by the sum of visible ratios (`columnWidthPercents`
  → `<colgroup>` percentages under `table-layout: fixed`), so layouts adapt to the list width and
  the chosen column set without re-tuning. Number input sits between the column name and the
  order arrows; "Divider lines between rows" checkbox toggles the row borders (header underline
  stays). Persisted per user via the existing preferences flow (localStorage + backend blob —
  `LibraryColumnPrefs.widths/dividers`). Headers ellipsize and the status select shrinks into
  narrowed columns. Verified live incl. persistence across reload.
- **Request-id + descriptive error envelope** (user request, follow-up to the NUL-byte 500):
  every request/response carries `X-Request-ID`; `sqlalchemy.DataError` → 400 with the DB's own
  reason; any other unhandled exception → 500 naming the exception class + message, both with
  the request id embedded (and the full traceback logged under the same id). Implemented as a
  middleware wrapped BY CORSMiddleware — an `@app.exception_handler(Exception)` lands in
  Starlette's outermost ServerErrorMiddleware whose response bypasses CORS, which is precisely
  why the browser only said "NetworkError". Trade-off note: exception text is exposed to
  clients by design (self-hosted LAN tool; diagnosability over secrecy — owner decision).

## NUL-byte PDF import 500 fixed (2026-07-17)

- **"NetworkError" on importing `95-ont-ijcai95-ont-method.pdf` root-caused**: NOT the embedded
  file the PDF carries (that's inert) — the 1995-era custom font encoding makes PyMuPDF extract
  the first page with raw control codes including **NUL (0x00)**, and the `files.preview_text`
  INSERT died with Postgres `DataError: text fields cannot contain NUL` → 500 → the browser
  reports "NetworkError". Fix: `sanitize_extracted_text` (storage service) strips C0 controls
  (keeping `\t \n \r`) at the preview extractor, so every ingest path (Import tab, paper-detail
  attach, folder scan, from-path/from-URL) is covered at the single source. The chunking
  service's identical private regex (it hit the same DataError earlier via GROBID labels) now
  imports the shared `CONTROL_CHARS`. Verified live: the real file imports, GROBID extracts
  title + year 1995, and enrich/chunk/embed jobs all complete; 3 regression tests added (SQLite
  accepts NULs, so they assert on the stored value).

## Profile layout + attach-from-URL/server-path (2026-07-17)

- **Profile page reorganized**: two independent flex columns inside the grid (no cross-column
  row-height gaps) — main column: Appearance (most-used, now first) + Reference-graph weights;
  side column: Account, Password, Roles & access. Width raised 52rem → 72rem so the theme grid
  actually uses the horizontal space; single column under 860px. Bonus fix: `.theme-option`
  inherited the global button's inverse ink, leaving theme names near-invisible — now
  `--ink-strong`.
- **Attach from URL (Files panel)**: "From URL…" opens a Proceed/Cancel modal; the URL is sent
  as a synthetic `manual_url` item through the EXISTING `find-on-web/download` endpoint, so the
  entire download policy applies unchanged (shadow-library denylist, SSRF guard, allow-list mode
  gate, `needs_confirmation` handshake — the modal shows the warning with "Download anyway").
  Success reports attached vs deduped and refreshes the paper (doi/arxiv backfill + queued
  extraction). Verified live: fetched the real arXiv 1706.03762 PDF (2.2 MB) end-to-end.
- **Attach from server path**: new `POST /works/{id}/files/from-path`. Path must resolve
  (symlinks followed) to a file INSIDE a merged allowed import root (server.yaml + Admin rows) —
  same containment rule as the server-folder import; then the bytes pass the exact browser-upload
  validation (size cap, %PDF, openability) and the shared attach/dedupe/extraction tail (factored
  into `_attach_pdf_bytes_to_work`). 10 endpoint tests incl. symlink-escape + `..`-traversal
  refusals; audited as `work.file_attached_from_path`. Verified live against the dev stack.
- **Tests**: backend 1237, frontend 305 (5 new modal tests), safety 161, e2e 37/37 with a new
  Journey 35 (both modals' offline-deterministic refusal paths). Journey 13's go-to-page flake
  root-caused: `fill` + synthetic `change` dispatch raced the pager re-render (`value={page}`
  reset the input between the calls) — now types + Enter like the input's title says.

## Skipped-test audit + journey 19 fix + e2e flake root-causes (2026-07-17)

- **Journey 19 (arXiv import) fixed**: it failed because the Import page gained method tabs (the
  identifier form only renders on the "Identifier" tab) and the button became "Import directly".
  The spec now clicks the tab first. Verified live against arXiv (`E2E_ONLINE=1`), passing. It
  stays gated behind `E2E_ONLINE` so offline CI remains deterministic.
- **Future placeholder tests graduated**: `frontend/src/future/{FutureUserFlow,ExtFutureWorkflow}.test.ts`
  and `e2e/tests/future/90-…` were deleted — every acceptance target they encoded has shipped and
  is enforced by real tests (teleport: `test_agent_teleport_acceptance.py`; summary provenance/
  fallback: `WorkDetail.summary.test.ts` + `InsightsPage.summary.test.ts` + `test_summarization.py`;
  missing-references dashboard: e2e Journey 24b; the full literature-review flow: Journeys 03–33).
  Mapping recorded in `docs/testing/EXT_TEST_BATTERY.md`; follows the policy in
  `TEST_DESIGN_REVIEW.md` and the backend `tests/future` precedent. Frontend unit suite is now
  0-skips (300/300), e2e is 0-skips online (36/36).
- **Safety-suite skip resolved**: `frontend/nginx.conf` is now mounted read-only into the api
  service, so the CSP/security-header test runs in `make test-safety` (161 passed, 0 skipped)
  instead of skipping outside CI.
- **e2e flake root cause — shared-account state races**: journeys 13+23 (papers_per_page) and
  29–32 (server theme) mutate the same `e2e_admin` preferences from parallel workers; one test's
  write/cleanup landed mid-assertion in another ("page 3 of 3" vanished, Save disabled with "No
  changes to save", `/auth/me` theme came back null). Each family now lives in ONE spec file
  under `test.describe.configure({ mode: 'serial' })` (`13-pagination.spec.ts`,
  `29-themes.spec.ts`). Two consecutive online runs after: 36/36, zero flaky.
- **Build-output warnings**: dead `.range` CSS dropped from `VisualizationsPage.svelte` (markup
  went away in UX batch 3 — the build warned "Unused CSS selector" every run) and an explicit
  default `frontend/svelte.config.js` added (silences "no Svelte config found" on every
  vite/vitest run).
- **Operational**: e2e must not run concurrently with `make ready-full`/`frontend-*` — the
  container npm build rewrites bind-mounted files (`themes.generated.ts` via `prebuild`) and
  invalidates the dev server's optimize-dep cache, which breaks app mount mid-suite (this
  produced misleading sign-in/global-setup failures today, twice).

## Full-battery clean run + journey-17 flake root-caused (2026-07-16)

- **Full battery green**: `make ready-full` (1227 + 73 + 4 backend, 300 frontend), `make test-safety`
  (160 passed; 1 by-design skip — nginx.conf lives only in the frontend image), `make e2e` (35 passed;
  3 by-design skips — Journey 19 needs `E2E_ONLINE=1`, plus the two `describe.skip` future specs).
  Output is warning-free end to end.
- **Journey-17 flake root cause**: `AdminPage`'s `$: if (client && client !== loadedFor)` fired
  `refresh()` the instant the authed client existed, but `currentUser` is seeded by an async `/me`
  *after* client creation — when `refresh()` won that race, every `get(canManageUsers)`-gated load
  (app config seeding the Settings form, groups, themes, …) was skipped for good; the existing
  `loadAppConfig` retry never ran at all. Fix: the trigger now also waits for `$currentUser`
  (unit tests already seed it before mounting). Two `make e2e` runs after the fix: zero flakes.
- **Build-output noise silenced**: `build.chunkSizeWarningLimit: 1300` in `vite.config.ts` (the
  over-500 kB chunks are echarts ~1.13 MB and the pdf.js worker ~1.25 MB — already lazily-imported
  vendor chunks that can't be usefully subdivided) and `frontend/.npmrc` with
  `update-notifier=false` (npm is pinned by the image; the self-update nag is non-actionable).
- **Operational reminder confirmed twice this session**: any `make frontend-*` / `ready-full` run
  (compose `run` on the shared bind mount) invalidates the live dev server's optimize-dep cache —
  clear `frontend/node_modules/.vite` + `docker compose restart frontend` before `make e2e`, or the
  reader journeys (8/10) fail on stale pre-bundles.

## Codebase documentation sweep + CI Redis-test fix (2026-07-16)

- **Doc/comment sweep**: a whole-codebase documentation pass (behavior-preserving) — module/class/
  function docstrings across `backend/app`, a leading purpose/props/lifecycle comment on every
  Svelte component + JSDoc on TS libs, and comments on non-obvious logic. Committed as
  `backend: …` and `frontend: …`. Verified: `compileall` clean, `make frontend-check` (build +
  300 tests) green, `make test` (927 passed) green.
- **docs/reference re-verified**: full accuracy pass over `00–11` against current code; corrected
  count/schema/behavior drift (reader teardown + zen, summary tiers, graph edges, migration/model
  counts). `06_agent_protocol` + `08_security` were already accurate.
- **CI Redis-test fix**: `POST /works/{id}/summaries` (detailed local_llm) returns 202 only when
  `enqueue_work_summary` yields a job id; with no Redis it returns None → inline 201. So the test
  passed locally (dev Redis) but failed on GitHub's Redis-less `backend` job (201≠202). Fix:
  (1) the endpoint-contract test now stubs the enqueue (deterministic 202, matches
  `test_batch_job_endpoints`); (2) new `test_detailed_summary_enqueues_a_real_rq_job` exercises the
  REAL enqueue + fetches the job back from Redis, self-skipping when Redis is unreachable via the
  new `requires_redis` fixture (same self-skip convention as `test_migration_parity`).

## Reader "Read works once then dead" — real root cause (2026-07-16)

The recurring bug (Read opens the reader once, then does nothing until you switch papers and back)
was finally reproduced and fixed. Root cause: `PdfReader.onDestroy` threw `pdfDoc.destroy is not a
function` inside Svelte 5's `destroy_effect` **only after a background job-poll refresh
(`refreshOpenWork → loadDetail`) had reassigned the reader's props mid-view**. A throw in effect
teardown corrupts the effect tree, so the parent's `{#key readerUrl}` block can never re-mount — a
paper switch "fixes" it because `{#key selected.id}` rebuilds the whole `WorkDetail` tree. A job-less
e2e paper never triggers the poll, which is why every earlier render-gate fix (and Journey 33)
passed while the bug survived manually.

- **Fix**: `PdfReader.onDestroy` (and the mid-life destroy in `loadPdf`) now guard every pdf.js
  teardown call (`renderTask.cancel` / `currentTextLayer.cancel` / `pdfDoc.destroy`) so one bad
  object can never abort teardown and escape into the effect tree.
- **Regression test**: Journey 34 (`e2e/tests/34-reader-reopen-after-refresh.spec.ts`) enqueues a
  real background job, holds the reader open across two 4s poll ticks so a refresh fires mid-view,
  then closes + re-opens — asserting the reader returns AND no page error escaped teardown. This is
  the only test that reproduces it.
- **Zen References fix**: clicking an in-text citation in zen switches to the References tab and now
  **stays in zen** (per user preference — leaving zen was the wrong call). Two real fixes make that
  usable: (1) a `← Back to paper` button in the References pane (`.ctx-nav`) — the only route back to
  the pages while zen hides the top tab nav; (2) the zen `display:none` rule was scoped from
  `.reader.zen header` to `.reader.zen > header`, so it no longer blanks the `<header>` inside each
  References/Notes article — that descendant match was the actual "black"/empty References pane.
- **Known separate bug (not fixed)**: extraction jobs intermittently fail with
  `StaleDataError: UPDATE ... works expected to update 1 row(s); 0 were matched` at
  `store_parsed_extraction`'s flush. `Work` has no optimistic-lock column, so the row is being
  deleted during the minutes-long extraction window — needs its own investigation (concurrent
  deleter: dedup/merge, recovery-sweep double-enqueue, or a citing/summarize job).

## UX batch 5 polish: summaries, reference graph, PDF reader (2026-07-16)

Follow-up polish after testing batch 5 (migrations 0075/0076 already on the live DB):

- **Summaries**: the detailed "section" level groups by MAIN sections (numbered subsections fold in
  by `N.M` numbering — a 13-main/20-sub paper summarizes 13). Funding/Acknowledgements/Conflicts/
  Author-contributions/Data-availability/Ethics/Declarations/ORCID/Supplementary are excluded from
  ALL summaries (short included — `_work_source` builds the body from the section-filtered
  `iter_work_sections`). A large scope that degrades to extractive now keeps the requested model on
  the row, so the frontend's (effort, model) read finds it (was a 404 → "finished but empty" window).
- **Reference graph**: reference edges are blue, citing gold, ref↔ref green; the ref↔ref toggle is
  client-side (always-fetched, no refetch/reset) and renders.
- **Insights scope summary**: the Regenerate button always rebuilds the scope synthesis; "Reuse"
  keeps existing per-paper summaries (only new/changed papers are summarized), "Regenerate all"
  redoes them; a per-paper summary made while abstract-only is refreshed once the paper gains a PDF.
- **PDF reader** (`PdfReader.svelte`): ref-link/annotation overlays rescale on zoom; page buttons
  work in scroll mode; zoom horizontal centering keeps the left edge reachable (`safe center`);
  touchpad pan (Space-hold no longer needs page focus); zen fills the viewport + Esc exits zen via a
  capture-phase listener (no longer closes the whole reader); "Read" opens on the first click; reader
  actions report in an in-reader status line (no more "Annotation deleted" under the paper title);
  highlight/citation/reference jumps scroll to the box position, and captured highlight text is
  de-hyphenated; highlight-note hover tooltip shows the note; Dim/Dark tint the reader chrome too.
- **KaTeX** was added to `optimizeDeps.include` (an on-demand optimize 504 on it blocked the whole
  app mount → e2e login timeout).
- **Notes** (feature): per-paper `Work.notes` (below the abstract) + per-Insights-scope `ScopeNote`
  (a per-scope Notes card + a folded "All scope notes" panel). Library tag filter is scope-aware.

## UX batch 5: summary effort levels, no-PDF honesty, jobs, graphs (2026-07-16)

Workplan `docs/WORKPLAN_2026-07-16_summary-effort-tags-graphs.md` (all 12 design questions resolved
inline). Shipped so far, each with tests + `make ready-full`/`frontend-check` green:

- **Jobs** (`a4c64a9`): every scope summary now enqueues (drops the size gate) so all Insights
  summaries show in the Jobs tab; detailed single-paper summaries report per-section progress and
  honor Stop (`summarize_work_job` wires progress/cancel, `JobCancelled` propagates); the stuck
  "stopping…" badge becomes a terminal "stopped" once the job ends.
- **No-PDF honesty** (`11f9982`): works are classified full-text / abstract-only / title-only.
  Title-only can't be summarized locally (clear 400, no doomed job); abstract-only uses an
  abstract-framed prompt; scope summaries fold abstract-only and title-only papers into single
  grouped paragraphs; the footer shows "N with PDFs, M abstract-only, K title-only · model · when".
- **Effort levels + cache matrix** (`4db779b`, `2164df7`, migration `0074`): detailed summaries have
  three effort levels — `detailed_fast` (LLM categorizes sections into ≤4 buckets), `detailed_section`
  (per top-level section, drops a level if <3), `detailed_deep` (per subsection; new leaf-section
  extractor). Each (effort × model) is a separate cached row, keyed `(entity, summary_type, model)`
  with LRU eviction past 5 models; scope summaries cached the same way and returned unless Regenerate.
  UI: Fast/Section/Deep radios (paper view + Insights) + a read-only history popup over cached
  variants. Migration 0074 renamed legacy `*_detailed` rows to `*_detailed_deep` (applied to live DB).
- **Graph quick wins** (`85dbc2f`, `4802c90`): the Insights external-node budget is distributed
  evenly across scope papers (absolute `A=ceil((L/2)/N)` + relative `R_i=E·C_i/S`) instead of a
  global top-N; the reference graph gets 3 colour-coded thicker edge series (reference / citing /
  ref↔ref), the paper title in its header, a scrollable overlap tooltip whose external members can be
  ctrl/middle-clicked to queue into the import box without switching tabs, and the Insights Build
  button shows building/done/failed state.
- **Insights pan/zoom freeze fix** (`095fb3b`): restyle repaints use a merge `setOption` so the force
  sim isn't restarted and roam is preserved; only Build/Refresh does a full rebuild.
- **LaTeX plain/fancy rendering** (`<this commit>`): `lib/renderMath.ts` renders `$…$`/`$$…$$` spans
  with bundled offline KaTeX (non-math text HTML-escaped); a conservative heuristic wraps only
  egregious non-delimited math (backslash-commands, braced `D^{-1/2}`) and leaves `N_i`/`O(n)` alone.
  Summary prompts now ask the model to delimit maths. WorkDetail + Insights scope summary get a
  fancy/plain toggle (fancy default). `katex` + `@types/katex` added; CSS imported globally.

- **Insights citing papers + typed edges + external styling** (`<graph commit>`): the citation graph
  now includes citing papers (from fetched `ExternalCitationLink`/`ExternalPaper`) as nodes with
  edges pointing INTO the scope (new `GraphEdge.relation` reference/citing, coloured distinctly);
  references and citing papers get **separate caps** (`max_external` / `max_external_citing`), each
  distributed across the scope papers so one half never starves the other. External nodes conform to
  the colour scheme where they have the value (year), keeping the diamond shape; a "Citing papers"
  toggle + coverage note ("none fetched yet").
- **Per-shelf/rack tags** (`<tag commits>`, migration `0075`): new `tag_shelves`/`tag_racks` tables
  (zero rows = global). `PUT /tags/{id}/scope` sets a tag's scope; `GET /tags/assignable?work_id=…`
  returns global + shelf/rack-scoped tags for a paper (rack via any-shelf-in-rack); `GET /tags?
  shelf_id/rack_id` filters. Tag tab gets a per-tag scope editor + a shelf/rack filter; the paper
  view's add-tag dropdown is filtered to the assignable set. Migration 0075 applied to the live DB.

### Follow-ups shipped after the main batch (2026-07-16)

- **Library-view scope-aware tag filter** (`<commit>`): narrowing the shelf/rack filter now shows
  only tags offered there (+ globals) and clears a now-invalid tag selection. Closes §3 Q7.
- **Per-paper Notes** (`<commit>`, migration `0076`): `Work.notes` (Text) editable via
  `PATCH /works/{id}`; a Notes textarea below the abstract in the paper view, saved with the paper.
- **Per-scope Insights Notes** (same migration): new `ScopeNote` model keyed (scope_type, scope_id)
  + `GET/PUT /ai/scope-notes` + `GET /ai/scope-notes` (list). Insights gains a per-scope Notes card
  (loads on scope change, Save button) and a folded "All scope notes" panel headed by each scope's
  type/label. Migrations 0075 + 0076 applied to the live DB.

## UX batch 4c: citation-count sizing, detailed-summary job, section names (2026-07-16)

- `f6a150c` — citation graph gains a **"citation count"** node-size option (external/global count
  on local nodes; backfilled for out-of-scope resolved targets). Help popup + hint updated.
- `002e258` — detailed per-paper summary now **runs as a background job** (paper view polls the
  Jobs list, then reloads) — its section-by-section LLM passes no longer block the request; short/
  extractive stay inline. Each section paragraph is **headed by its GROBID section name**
  ("Methods: …") and the chunk prompt no longer opens with "This section" (starts with content).
  `enqueue_work_summary(work_id, detail)` keys short vs detailed as distinct jobs.

## UX batch 4b: short/detailed summaries, download messaging, job progress (2026-07-16)

- `3e72148` — find-on-web: a policy refusal now carries an actionable hint (names the allowing
  policy / Admin → Find-on-web; never suggests a denylisted host) and lists the tried URLs, shown
  as clickable links in the paper view. Root cause of the owner's Springer failure was the
  `restricted` download policy, not a bug (probed live: works under `careful`). Headless-browser
  discussion in `docs/WORKPLAN_2026-07-16_ai-insights-downloads.md`.
- `66d2f78` (earlier) — browser-compatible fetch headers (publisher CDNs 403 bare bot UAs).
- `7ed10e9` — per-paper **detailed** summaries: whole body (no 12k clip) chunked into section
  paragraphs + a synthesized intro, stored as `{type}_detailed` so short + detailed coexist;
  scope `paper_detail`/`regenerate_papers` passthrough.
- `085caf5` — paper view Summaries panel shows Short + Detailed separately, each with its own
  Regenerate (the top-actions Summarize button moved here); Insights scope-summary dropdown
  (use/create · regen × short/detailed) chooses which per-paper summary feeds the synthesis and
  whether to reuse or regenerate; scope output rendered as formatted sections (bold headings +
  bullet lists) instead of one run-on block.
- `21a5dc2` — long scope-summary jobs report progress (N/M papers) and can be **Stopped**
  cooperatively from the Jobs tab (per-paper summaries already made are kept).

## UX batch 4: scope-summary overhaul, topics UX, insights graphs (2026-07-15)

- `0c32ea6` — **scope summaries are now map-reduce** (`local-llm-v2-map-reduce`): per-paper LLM
  digests generated through `summarize_work` (persisted → also visible per paper; reused on the
  next run; outage circuit-breaker), packed into 11k-char chunks, condensed, then synthesized
  with collection-framed scope prompts (overview + key problems / methods & algorithms /
  datasets / findings — no more "This paper addresses…" for a shelf). `scope_label` + method
  stored/returned. Topics carry their member papers (`work_ids`/`works` with titles) and
  `GET /ai/topics/latest` reconstructs job results from assignments.
- `e0a4497` — Insights: Summarize / Model topics buttons show a busy state and stay disabled for
  the whole run incl. the background job, then auto-load the result (whole-library topics used to
  end with an empty card); "Max topics" control (default 8, was fixed 6); per-topic "Show papers"
  lists with jump-to-paper; graph node click finally wired (`onOpenWork` was never passed);
  topic graph gains size (similarity links / citation count) + color (year) selects (+
  `citation_count` on topic-graph nodes); ⓘ Help popup explains degree/PageRank/betweenness and
  how topic edges are computed (embedding cosine kNN, k=6, ≥0.30).
- `b607d35` — ruff SIM102/SIM105 cleanups in `pdf_link_finder`.
- `33ec89c` — future acceptance contracts graduated: 3 dropped as already enforced (citations in
  the file docstring), cross-scope topic stability implemented as a real test.

## UX batch 3 follow-ups: smarter downloads, Elsevier API, graph zoom/sliders (2026-07-15)

- `6780d1d` — fix round: **zen mode portals to `<body>`** (the paper-view modal's containing
  block was clipping `position:fixed` — the app showed around the backdrop); force-layout
  **Show all fits without re-springing** (zoom/center merge, not a repaint; one-shot auto-fit
  ~1.6 s after rebuilds); **ctrl-click focus on the reference graph** (nodes AND legend entries,
  venue coloring included; the legend toggle the click causes is undone automatically); help
  popups document the new interactions.
- `c1e5baf` — ctrl-click neighborhood focus extended to the Visualizations **co-citation network
  and temporal map** (payload-level node/edge filtering before the pure renderers; legend
  ctrl-click included; focus note + hint line; About texts updated).
- reference docs updated in the same batch: 02 (app_config/users columns, migrations 0070–0073),
  03 (`pdf_link_finder` row + web_find fallback chain incl. the Elsevier gates), 07 (zen mode +
  standard graph interactions); handoff note `2026-07-15-ux-batch3-downloads-graphs.md`.
- `0e139c3` — embedded-JSON PDF sniffing (layer 4 of `pdf_link_finder`): script-blob URL scan +
  ScienceDirect `pdfft?md5&pid` reconstruction from `urlMetadata`; **Elsevier Article Retrieval
  API fetcher** for 10.1016 DOIs (write-only key in Admin → Find-on-web; yaml/env fallback
  `web_find.elsevier_api_key` / `PARACORD_ELSEVIER_API_KEY`; `api.elsevier.com` allowlisted).
  Migration 0072.
- `60cfd6c` — Elsevier API gating (migration 0073): master enable switch (key can stay stored
  while disabled) + per-user allowance (`users.elsevier_api_allowed`, OFF by default, toggled in
  Admin → Users; `POST /admin/users/{id}/elsevier-api`). The key is used only when key ∧ master
  switch ∧ per-user allowance all hold.
- `72515eb` — reference graph: edge-snapped cursor zoom (wheel near a plot edge pins the zoom
  window to that data edge — no more zoom-drag-zoom toward the bottom lanes); Visualizations:
  manual X/Y min/max inputs replaced by native two-handle range sliders (auto-ranging; end-stop =
  auto) on the temporal map + embedding cluster, plus Show all / Reset view buttons.
- Live DB migrated to **0073**; web-find suite 141 passed; frontend build + AdminPage tests green;
  frontend dev server restarted (vite cache cleared).

## UX batch 3: zen reader, graph UX, ref metadata, badges (2026-07-15)

- `5eb79a8` — **landing-page PDF discovery** (item 4, after owner clarification: manual triggers
  + current host policy stay; the *attempt* gets smarter). New `services/pdf_link_finder.py`:
  publisher PDF-URL rewrites (ACM/Springer/Wiley/IEEE/Nature/MDPI/arXiv/ACL/OpenReview/
  bioRxiv/PLOS), `citation_pdf_url`/alternate-link metas, scored "Download PDF" anchors (the
  IEEE `xpl-btn-pdf`-style hints). Wired into `download_and_attach` as bounded fallbacks (≤5
  URLs, one page read, no recursion) — every URL still passes the denylist/SSRF/policy gates and
  unknown hosts are never auto-confirmed. JS-only pages (ScienceDirect) still fall back to
  `manual_upload_needed`. web_find suite 112 passed.

- `112d862` + workplan updates — `docs/WORKPLAN_2026-07-15_ux-batch3.md`, incl. the **automatic
  PDF retrieval proposal + 6 discussion points awaiting the owner** (item 4 not implemented by
  request).
- `e36dce6` — Insights export widgets folded into a `<details>` card at the bottom; reference
  graph "Max external" base = 500 (frontend default + backend Query/service defaults) and the
  user's chosen value persists in the preferences blob (`reference_graph_max_external`).
- `62f5c04` — resolved references display the WORK's canonical title/year (reference-graph nodes
  + the references panel overlay) — importing a cited paper now updates what other papers show.
- `b5ba0ed` — Library badges column gains `likely_refs` ("refs to review") and
  `citers_in_library` ("cited locally") tokens (two grouped queries per page).
- `754985e` — reader zen mode: viewport takeover on a dark backdrop; only paging/zoom/view-mode/
  reading-mode + Exit zen remain; Esc exits; combinable with original/dim/dark.
- `3fdf38d` — graphs: **Show all / Reset view / Refresh** on the citation/topic graph and the
  reference graph; tooltips spell out the encoded channels (size/color/Y); **ctrl-click** a node
  or a category chip on the citation/topic graph shows it + direct neighbors only (focus note +
  cleared by Reset view/rebuild). Deferred to discussion: edge-snap zoom, ctrl-click on the
  reference-graph scatter / Visualizations.
- Fast tier 905 passed (after adding the external-citation tables to the m1 trimmed fixture),
  frontend 294 + build green. Frontend dev server restarted (vite cache cleared); API hot-reloads;
  no migrations this batch.

## UX batch 2: acceptance policy, unified imports, previews, summary columns (2026-07-15)

- `0911bb0` — two-level fuzzy-match acceptance (migration 0070): admin checkbox "Use fuzzy
  auto-accept" + editable threshold (default from yaml `reference_matching.auto_accept_threshold`,
  now 90; floored by yaml-only `min_auto_accept_threshold` 90) and "Use high-confidence
  auto-accept" checkbox (threshold yaml-only `high_confidence_threshold` 100, shown read-only,
  implied/disabled while fuzzy auto-accept is on). New `AcceptPolicy` +
  `effective_accept_policy(db)` replace the bare `fuzzy_as_confirmed` bool at every call site;
  changing any acceptance setting enqueues a library-wide rescan.
- `6b6ac76` — unified external-entry imports: references AND citing papers get "Find & Import"
  (prefills Batch import citations incl. DOI, jumps to Import → Citations) plus "Direct import"
  (identifier present) / "Create paper" (title-only — new `POST /works/from-citing/{id}` creates
  from cached metadata, so DOI-less citing papers are importable at last).
- `3c1920b` — References/Citing panel headers carry "N likely matches / N in library / N external"
  badges so needed input is visible without expanding.
- `87b1811` — Identifier import gained Preview & choose (existing `/citations/external-preview`
  fetch → shared DraftReview → batch commit `engine="identifier"`); direct import unchanged.
- `dfd397b` — DraftReview badges (matched / title only / no match) explain themselves + the commit
  outcome in tooltips.
- `43691e9` — citation summary (migration 0071): per-column item cap is admin-editable
  (`citation_summary_item_cap`, default 100 — was a fixed 15 that hid entries); columns fold into
  ~15-item scrollable windows with a per-column "↑ To top" reset.
- Live DB migrated to 0071; worker + frontend dev server restarted; fast tier 894 passed,
  frontend suite + build green, migration parity green.

## UX batch: import sub-tabs, resilient citation import, jobs history, matching (2026-07-15)

- `9744046` — Import page split into sub-tabs (PDF import / Citations / Identifier / Folder
  import / External data); last selection remembered per browser session; a reference-graph
  "import citation" push auto-opens the Citations sub-tab.
- `65901b7` + `a60c457` — batch-citation preview reworked to two rows per entry (title gets the
  full first row); the review table + commit extracted into shared `DraftReview.svelte`.
- `838e4e2` — batch citation lookup is now chunked (4 lines/request): progress bar, "Cancel
  search", a failed/timed-out chunk degrades to editable title-only rows, and "Commit selected
  now" imports found entries while the search keeps running. `web_find.total_budget` default
  raised 25 → 120 s (now a backstop, not the pacing mechanism).
- `a60c457` — BibTeX import gained "Preview & choose" (`POST /imports/bibtex/preview` → shared
  review table → `batch/commit` with `engine="bibtex"`; arXiv id / work_type / abstract ride
  along; already-in-library entries flagged and default-unchecked).
- `b615420` — jobs: every run gets a unique id (`{key}-{token}`); live-run coalescing moved to a
  Redis `latest-job:{key}` pointer. Re-running a finished job now ADDS a Jobs-tab entry instead of
  overwriting the old one (which made history vanish and counts disagree); "all" count = sum of
  the per-state registry counts.
- `eae2239` — the restart-looping chunk-job failure (SQLAlchemy DataError 9h9h): chunk text/labels
  now sanitized (NUL/control chars stripped; section labels clamped to the column's 255 chars) and
  a chunk failure now sets the paper's processing-error badge. NOTE: the original failing work was
  deleted from the library before diagnosis, so the fix covers the two known DataError vectors of
  that insert rather than a reproduced case.
- `0bcd4ef` — reference matching: new `reference_matching.auto_accept_threshold` (yaml, default
  100 = exact normalized title) auto-CONFIRMS a fuzzy match even without a DOI/arXiv id — fixes
  "stays likely at 100%"; the runtime fuzzy-as-confirmed toggle is unchanged. Confirm/reject in
  the references list now shows inline per-row pending/error feedback (an error used to render
  only at the top of the modal, reading as "nothing happened").
- `0875075` — citation summary: a likely-local fuzzy suggestion counts as held, not "frequently
  cited but missing" (rejected suggestions stay missing).
- `897da47` — Admin Settings / Find-on-web / Backup tabs use a packed two-column (masonry) layout.
- Fast backend tier green (889 passed), frontend 293 + build green. Worker restarted; frontend
  `.vite` cache cleared + dev server restarted after the in-container build.

## UX batch: citing polish, field search, clickable summary, batch jobs (2026-07-15)

- `1601ad7` — external citing papers tinted lighter in the reference graph; paper view loads the
  citing list eagerly so the header count shows without expanding.
- `f97f971` — `keyword:`/`topic:` operators + exact-field search modes (Library: keywords / topic /
  author / venue; Search tab: Keywords / Topics).
- `73c597a` — citation summary: year bars open a papers popup; venue/author rows jump to a
  Library `venue:`/`author:` search.
- `7036831` — Library batch actions "Fetch citing papers" and "Summarize" as background jobs
  (`fetch_citing_work_job`, `summarize_work_job` + 202 endpoints; titles in the Jobs tab).
- Full battery green (backend 1173, safety 160, frontend 288 + build, e2e 33; one unrelated
  one-off flake in test_duplicate_merge passed on rerun of the full tier).

## Legend chips, palette, count-sort 500 (2026-07-15)

- **Count-column sorts 500ed on Postgres** (`4abc63b`): file/reference/local-count sorts order by
  correlated subqueries, but the works query is SELECT DISTINCT — Postgres rejects DISTINCT +
  ORDER BY-subquery (the library showed "NetworkError" until the sort column changed; SQLite
  tests tolerated the SQL). Fixed by selecting the count as a labelled column; regression test.
- **Citation-graph legend chips** (`9019b07`): ECharts' native graph-series legend hover
  highlights the node at the legend item's INDEX (not the category) — replaced with our own chip
  row (hover highlights the group, click hides/shows, shift-click solos). Groups sorted (years
  numeric), palette extends past the fixed 6 theme colors with evenly spaced hues, and the
  shift-click solo gesture now also works on the reference-graph and Visualizations legends via
  shared `lib/viz/legendSolo.ts`.

## Graph legend + paper-view history follow-ups (2026-07-14)

- **Graph legend/colors** (`2f1a993`): legend-hover highlight now shows each group's own color
  (explicit per-category itemStyle — the option-level palette made ECharts repaint highlighted
  nodes in the emphasis default); shift-click a legend entry to solo that color group
  (shift-click again to show all); `color_by=year` added end to end.
- **Paper-view history** (`610bd47`): the Library page records the last 100 viewed papers;
  WorkDetail's "Previous" button walks back (refetching papers that left the filtered list) —
  for returning after hopping through citation/reference graphs.
- Also this session: AdminPage app-config load decoupled + retried (`8953e87`, e2e journey-17
  first-run flake) and the stale cytoscape `optimizeDeps` entries removed (`fba76de`, broke the
  PDF reader after a dep-cache rebuild).

## Insights audit implementation, chunk C (2026-07-14)

- **C1+C2** (`7d25815`): ONE charting stack — Cytoscape removed from `package.json`;
  `CitationGraph.svelte` rewritten as an ECharts force-directed graph (same props/controls;
  layouts reduced to Force/Circle; filters/sizing/theme are client-side option rebuilds).
  Shared `<ChartHost>` owns the ECharts lifecycle (lazy import, init, ResizeObserver,
  resize-on-tab-show, theme repaint, dispose) for CitationGraph, ReferenceGraphModal,
  CitationSummaryPage and VisualizationsPage.
- **C3** (`f5aa83f`): shared `<ScopePicker>` + `lib/scope.ts` (readiness rule + request
  building); Visualizations and Citation summary gain the import-batch and saved-filter scopes
  their backends already accepted (all three tabs now offer all seven). Vocabulary unified
  (same scope labels/hints everywhere; American "Summarize"/"Modeled" spelling).
- **C4**: temporal-map size/color changes restyle the loaded payload client-side
  (`restyleTemporalMap`; nodes now ship `venue` in meta) instead of refetching; the four
  duplicated cosine implementations collapse into `services/vector_math.py`
  (dense/sparse/matrix); Citation summary fetches the venue/author aggregation lazily on first
  sub-tab open; Insights surfaces the topic modeler's coherence score, representative papers
  (lazy title lookup, click-to-open) and no-topic outliers, which were previously dropped.

## Insights audit implementation, chunks A+B (2026-07-13)

Owner decisions on the Insights/Viz/Summaries audit: merge graph engines on ECharts; high
admin-editable node caps + background-job routing per surface; extend the scope resolver to all
seven types; keep the three tabs but unify vocabulary.

- **Chunk A** (`f83e945`): scope_resolution handles all SEVEN scope types;
  `citation_graph._scope_works` is now a dict-shaped shim over it (migrating citation_summary,
  venue_author_summary, visualization in one move); the six copied endpoint SEE-check +
  saved-filter blocks collapse into `api/scope_params.resolve_scope_or_404`.
- **Chunk B** (`f5f27fb`): per-surface node caps (migration `0069`; citation 1500 / topic 400 /
  viz 500; Admin → Settings) — the citation graph previously ran exact betweenness over an
  uncapped library scope inline; capped graphs keep the best-connected nodes and report
  `nodes_hidden`. Scopes above `ai_scope_job_threshold` compute on the worker
  (`analysis_graph_job`), results held in Redis 1h and fetched via the requester-gated
  `GET /jobs/{id}/result`; the Insights page resolves queued responses transparently.
- **Remaining (chunk C, planned):** ECharts-only graph component (drop Cytoscape), shared
  `<ChartHost>`/`<ScopePicker>` (+ scope-menu parity for Viz/Citation-summary), vocabulary
  unification, viz client-side restyle, cosine consolidation, lazy venue/author load, surface
  topic coherence/representatives.

Battery re-run, all green: ready-full (backend 1167 / agent 73 / migrations 4 / frontend 275 +
build), safety 160, e2e 33. (One local hiccup: root-owned `.vite` from in-container npm runs
broke `make frontend-install` once — cleaned; note in memory.)

## Batch: D3/D38/E3 + graph caps + references-panel + migration squash (2026-07-13)

- **D3** (`fe6776b`): Caddy TLS proxy in the prod overlay (`config/Caddyfile.example`, local CA
  for LAN or Let's Encrypt with a domain; INSTALL.md "TLS on the LAN"); agent refuses plaintext
  http beyond loopback unless `allow_insecure_http` + new `ca_cert` trust option; server logs a
  loud startup warning for plaintext non-loopback (PARACORD_ALLOW_INSECURE_HTTP acknowledges);
  agent tokens (already permanent) can now be minted with `token_ttl_days` at approval
  (migration `0068`) and expire with a 401.
- **E3** (`6ed3be0`): `access.visible_work_condition` exposes the visibility clamp as a SQL
  predicate; scope resolution accepts it interchangeably with the id set; files list + AI scope
  paths (incl. S15 jobs) filter in the database instead of shipping id sets as IN lists.
- **D38**: register updated — §10.2 backup/restore delta closed by the Backup feature.
- **Item 1** (`dd3c651`): citation + reference graphs cap external nodes at `max_external`
  (default 50, 0-500), keeping the most-cited/newest; controls on Insights + the reference-graph
  modal; hidden counts in the payload.
- **Item 2** (`dd3c651`): the References panel remembers its open/collapsed state instead of
  forcing itself open on every paper view.
- **Item 4** (`fddffb5` + `25198aa`): the 68-file migration chain squashed into ONE frozen-DDL
  baseline keeping revision id 0067 (existing DBs = head no-op; fresh DBs build in one step);
  pg_dump-diff-verified equal incl. server defaults + historical constraint names; the
  create_all-was-a-moving-target bug caught by pg-integration tests and fixed by pinning static
  autogenerate output; historical 0029-backfill replay test removed (scenario impossible,
  runtime invariant covered).
- **Insights audit quick wins** (`29420f1`): citation_summary N+1 fixed; stale cap comment;
  register F3b marked resolved. Full audit findings + workplan in the session handoff.

Battery (owner-requested): `make ready-full` (lint clean; backend 1165 passed/4 skipped; agent
73; migrations 4; frontend 275 + build), `make test-safety` (160 passed/1 skipped),
`make e2e` (33 passed/3 skipped) — all green, zero pytest warnings.

## Feature batch: backup/restore, batch-import rework, job cancel, staging titles (2026-07-13)

Four owner-requested features (handoff:
`docs/agent_handoffs/2026-07-13-feature-batch-backup-imports-jobs.md`):

- **Backup/export + restore** (`c8e14a3`): version-tolerant logical archives (JSONL per table +
  manifest + optional content-addressed PDFs), admin export via the new Admin → Backup tab;
  OWNER-only restore with merge/replace modes, typed REPLACE confirmation, a pre-restore
  compatibility report, column-intersection tolerance (defaults backfill new columns, dropped
  columns counted, _RENAMES map for renames, per-row SAVEPOINTs, same-table FK retry), sha256 PDF
  pairing from the archive or an import-root alias (unmatched files dropped like a manual delete),
  and an owner-lockout guard on replace.
- **Batch import rework** (`46910a6`): partial/sequential commits ("Import selected now" works
  DURING extraction, repeatedly; imported items leave the preview); the polling GET self-heals
  items whose worker died and finalizes wedged batches; the frontend polls live and unbounded
  (was a dead 60s cap).
- **Job cancel** (`46910a6`): POST /jobs/{id}/cancel + Cancel button for queued/scheduled/deferred
  jobs. ROOT CAUSE of the never-rerunning scheduled job: workers ran without --with-scheduler, so
  RQ retry intervals never fired — fixed in the supervisor.
- **Staging job titles** (`46910a6`): extract_staging_item_job rows show the staged paper's
  parsed title/filename + sha256 in the Jobs tab.

Verified: full backend suite 1163 passed / 4 skipped; frontend 275 + build. Restart both
containers on deploy (worker picks up --with-scheduler + new job modules).

## Consolidation StaleDataError hotfix (2026-07-13)

The reference-dupes scan failed on the real-data Postgres deployment with
`StaleDataError: UPDATE on reference_citations matched 0 rows`: the fold bulk-deleted loser
Reference rows before flushing the repointed link rows, and Postgres's ON DELETE CASCADE ate
them out from under the session (SQLite tests don't enforce FKs, so CI couldn't see it; the
failed job rolled back, so no data was harmed). Fixed by flushing links before the delete
(`fad8ea5`); regression test runs with SQLite `PRAGMA foreign_keys=ON` and was verified to fail
on the old ordering. Real-data PC: pull + restart, then re-run the scan.

## Docs source-of-truth + agent-guide merge (S17/S18, 2026-07-13)

`AGENTS.md` now declares `docs/reference/` the engineering source of truth (update it in the same
commit as the code) and keeps `SPECIFICATION.md` as product intent (banner added there);
`HINTS_FOR_AGENTS.md` merged into `AGENTS.md` and deleted; `docs/architecture/` archived into the
gitignored `documentation_archive.zip` (`5877681`). Also: failed-job error text now read via
`job.latest_result()` — the RQ `exc_info` DeprecationWarning in test output is gone (`7ca451e`);
OpenAPI schema regenerated for the reference-dupes + latest-summary endpoints (`f16f318`).
S19 resolved (`a457f3b`): misleading dead-twin keys + `agents.*` deleted from
`server.example.yaml`; never-wired blocks moved under a commented-out REFERENCE ONLY divider;
env-pointer keys kept by design. The example now parses with no unread live keys beyond the five
deliberate env pointers.

## Reference consolidation + admin review (S13/S14, 2026-07-13)

Owner policy B implemented (`64631c5`): a consolidation scan auto-folds conflict-free
duplicate canonical references (oldest survives, links/mentions repointed, metadata merged,
resolution ladder) and parks contradictions with a `|conflict:<id8>` dedup-key suffix for the
new **Admin → Reference dupes** tab (scan button + "X dupes resolved, Y contradictions found" +
per-group "Keep this resolution"). Scan runs from the button (coalescing queued job, inline
fallback) and once per server startup. 13 new backend tests + an AdminPage test; full backend
suite 1150 passed. Follow-up: `dedup_key` unique index + upsert once the real deployment has a
clean scan. Also fixed the reported E2E failure — a stale Vite optimize-dep cache (504s) from
running build/tests inside the live dev-server container; after cache clear + restart the full
`make e2e` battery is green (33 passed / 3 skipped).

## Structure-audit fixes, S-batch (2026-07-13)

Implements the owner-decided items from the 2026-07-12 structure audit
(`docs/WORKPLAN_2026-07-12_structure-audit-discussion.md`, decisions recorded at its top). On
`main` (not pushed), one commit per item; handoff:
`docs/agent_handoffs/2026-07-13-structure-audit-fixes-s-batch.md`.

- **S5** targeted duplicate scans no longer sweep the whole other table in the request.
- **S6/S7** enrich/chunk/embed/topic/keyword jobs retry transient failures (`Retry(max=2,
  [30,120])`); enrich raises on all-sources-failed; `_get` honors a capped `Retry-After` on
  429/503. Deterministic failures (DOI conflict) still never retry.
- **S10** the three unbounded in-process caches are now bounded TTL/LRU
  (`utils/bounded_cache.py`); summaries 128/15 min, layouts 32/30 min, previews 256/15 min.
- **S11** per-user preference files (`preferences.d/<uuid>.yaml`, legacy file read as fallback) —
  no more multi-process lost-update race.
- **S3** one canonical arXiv parser in `utils.normalization` (three divergent copies collapsed);
  extraction/import-staging normalize promoted DOIs; migration `0065` backfills stored
  works/references/external_papers identifiers with unique-index collision guards.
- **S1/S2** shared query-returning scope resolver (`services/scope_resolution.py`) with a
  **required** `visible_ids` clamp + `count_scope_works`; summarization/topic_modeling delegate.
- **S8/S9** the full-library rescan job matches references AND external citing papers via
  in-memory indexes (identifier/blocking/authors, built once) in 500-row commit batches.
- **S12** three-outcome citing fetch: authoritative-zero replaces the cache and stamps new
  `works.citing_fetched_at/_source` (migration `0066`); failed fetches keep the cache.
- **S20** citing fetch pages OpenAlex/S2 up to `app_config.citing_papers_fetch_cap` (default
  1000, migration `0067`, admin UI).
- **S15/S16** large-scope topic models/summaries run on the worker (stub jobs filled, per-scope
  deterministic ids, 202 + job id above `app_config.ai_scope_job_threshold` default 100, admin
  UI); `GET /ai/summaries/latest` read path; Insights page polls and refreshes.
- **S4** domain-error hierarchy (`app/errors.py`) + one handler; `shelf_membership` migrated;
  `build_works_query` moved to `services/works_query.py` (layering inversion fixed);
  `export_service.authors_by_work` public.

Verified in-container: backend `-m "not safety"` 1137 passed / 4 skipped; frontend 274 + build;
alembic base→0067 (+ 0064↔0063 round-trip) on scratch pgvector:pg17. Migrations 0064–0067 not
yet applied to the live DB (apply on next rebuild+restart; back up first — 0065 rewrites
identifier columns). Open: S13/S14 (consolidation job), S17–S19 (docs decisions).

## Citation/reference matcher improvements (2026-07-12)

Audit + upgrade of the local reference/citation matcher (owner request): F1 (precision *and*
recall) improvements, coverage of BOTH directions, lifecycle fixes, count sync. On `main` (not
pushed); commits `be35fc2` / `6055185` / `9aff7b2`. Handoff:
`docs/agent_handoffs/2026-07-12-citation-reference-matcher-improvements.md`.

- **Matcher F1.** rapidfuzz promoted to a **required** dependency (it was optional and absent from
  the deployed image — the fuzzy matcher silently ran on the token-less difflib fallback; rebuild
  api/worker images). New `title_similarity_pct` (punctuation-normalized, word-order tolerant,
  length-guarded containment so short generic titles can't match superset titles). arXiv-DOI
  (`10.48550/arXiv.*`) ⇔ bare-arXiv-id bridging across the exact stage, the D2 gate (preprint DOI
  vs journal DOI no longer disqualifies fuzzy), dedup keys (with legacy-key lookup), and the
  citation graph. Stopword-tolerant title blocking ("The X…" vs "X…" now compared at all). Year
  gate accepts ±1 (`reference_matching.year_tolerance`).
- **Incoming direction.** `external_papers` gains `resolved_work_id` + `arxiv_id` (migration
  `0064`, additive): every fetched citing paper runs through the same matcher/gates, so in-library
  citers are recognized — surfaced on the citing-papers endpoints, reference-graph citing nodes,
  and an "in library" badge in the UI. Self-match guarded; the rescan-all job backfills.
- **Pipeline reverse-rescan.** Extraction + enrichment (the moments a work actually gains
  title/DOI) now reverse-rescan still-external references and cached citing papers — previously
  only manual create/import did, so uploaded papers were never linked as targets until a full
  library rescan.
- **Lifecycle.** Merge moves incoming external citation links (dedup-aware, exactly reversed on
  unmerge) and repoints matcher-resolved external papers; delete prunes external papers whose only
  citer link was the deleted work and re-resolves references whose soft *suggestion* pointed at it.
- **Count sync.** `POST /works/{id}/citing-papers/fetch` now refreshes the cached
  `citation_count/_source/_fetched_at` from the provider's full total (OpenAlex `meta.count`; S2
  one cheap follow-up), so the list and the count snapshot can't drift. `_bare_doi` delegates to
  `normalize_doi` (no more duplicate external rows from `dx.doi.org`/`doi:` decorations).

Verified in-container: backend `-m "not safety"` 1107 passed / 4 skipped; frontend 274 + build;
alembic chain to 0064 on a scratch pgvector:pg17. Migration **not yet applied** to the live DB
(entrypoint applies on next restart; rebuild images first).

## issue_batch_12 (2026-07-11)

Reference→library matching ("likely local" citations) + reference-graph fixes. Plan:
`docs/WORKPLAN_2026-07-11_batch12.md` (owner decisions D1–D6 + items #1–#7 locked); handoff:
`docs/agent_handoffs/2026-07-11-issue-batch-12.md`. On `main` (not pushed). One commit per phase.

- **Phase 0 (done).** `normalize_title` now strips punctuation **before** collapsing whitespace so
  "KnowRob – A…" and "KnowRob: A…" normalize equal (also strengthens the work-vs-work dup scanner);
  `normalize_doi`/`split_arxiv_id` harden prefix handling (`http(s)`, `dx.doi.org`, bare host,
  lowercase `arxiv:`, `/pdf/`, `.pdf`). Column-picker Apply/Cancel moved above the list; reference
  graph shows citing papers by default.
- **Phase 1 (done) — canonical-reference refactor (#3).** `Reference` is now the **canonical** cited
  thing; the per-citing-work edge moved to a new **`ReferenceCitation`** link table (symmetric with
  `ExternalPaper`/`ExternalCitationLink`), so one reference is shared by every work that cites it and
  a resolution applies to all citing works at once. `Reference` also gains the matching columns
  (`normalized_title`, `authors` JSONB, `suggested_work_id`, `match_score`, `dedup_key`). Migration
  `0059_canonical_references` is a **lossless 1:1 structural expansion** (every existing row keeps its
  id, gets exactly one link); dedup/consolidation of duplicate reference rows is deliberately **not**
  in the migration — it's a separate forward-only opt-in job (Phase 1b, not yet built). Every
  `citing_work_id == work_id` read moved through the link table (`extraction`, `reference_graph`,
  `citation_graph`, `citation_summary`, `works`, `citations`, `export_service`,
  `duplicate_resolution`). 744 fast-tier tests green; migration parity + autogenerate-clean pass;
  downgrade→upgrade round-trip verified on dev Postgres.
- **Phase 2 (done) — matcher + config + persistence + rescan (D1/D2/D3).** New
  `services/reference_matching.py`: identifier gate (DOI/arXiv must match exactly when present on
  both sides, else disqualified — no fuzzy fallback) then fuzzy title (`similarity_pct` ≥ 90) with
  equal-year + author-overlap gates. Persists per the status rules: identifier → `local_match`;
  fuzzy → `likely_match` (soft, `suggested_work_id`+`match_score`, never written to
  `resolved_work_id`) unless the `use_fuzzy_match_as_confirmed` toggle promotes it to a hard link; a
  `confirmed_match` is locked and a `rejected_match` isn't re-proposed. Author matching
  (`services/author_matching.py`, owner #5/#6): (surname, first-initial), diacritic-folded, "et al"
  validated against the single best author. Wired into extraction (also adds `arxiv_id` to
  `ParsedReference`/TEI parse); **reverse-rescan** on new-work creation (import-from-reference +
  manual create) links still-external references without re-extracting; manual rescan endpoints
  (`POST /works/{id}/references/rescan` sync per-paper, `POST /works/references/rescan-all` enqueued +
  audited). YAML `reference_matching:` block → `Settings`. The `use_fuzzy_match_as_confirmed` bool +
  migration `0060` + effective/update helpers landed here (extraction needs the getter); the admin
  endpoint + UI toggle are Phase 3.
- **Phase 3 (done) — confirm/reject/import actions + admin toggle (#1/#4).**
  `PATCH /works/{id}/references/{reference_id}` `{action: link|reject|import}` — `link` confirms the
  suggestion (`confirmed_match`, locked; audited `reference.confirm`), `reject` → `rejected_match`
  keeping the suggestion (audited `reference.reject`), `import` runs the existing from-reference path.
  Acts on the canonical reference so it applies to all citing works; `work_id` scopes auth. The
  References panel (WorkDetail) now shows a "likely match · N%" badge with **Confirm match** / **Not a
  match** buttons and a **Rescan matches** button. Admin "Settings" tab gains a **Reference matching**
  section with the `use_fuzzy_match_as_confirmed` checkbox (exposed in `AppConfigOut`/`Update` +
  `PATCH /app-config`); turning it ON enqueues a library-wide rescan to promote existing likely
  matches.
- **Phase 4 (done) — authors (#4/#5/#6).** Persist + match landed in Phase 2 (`Reference.authors`,
  `author_matching`); this adds **display**: `ReferenceRead`/`ReferenceRecord` carry `authors`, and
  the References panel shows a reference's authors. Citing papers already show their authors
  (`ExternalPaper.authors`); graph-tooltip authors ship with Phase 5.
- **Phase 5 (done) — reference-graph rendering (#7 + overlap fix).** `build_reference_graph` emits a
  `likely_local` node kind carrying `suggested_work_id`/`match_score` (left unresolved so local
  metrics aren't computed on a guess) + `authors` on reference/citing nodes. `referenceGraph.ts`
  colours `likely_local` as a lighter tint of the in-library hue, and ports temporalMap's
  overlap-collapse (`groupByCoord` + count-badge marker + **enterable** tooltip listing ≤10 members
  with the true total + an "import all" affordance) so ~100 citing/external nodes stop pixel-stacking.
  `ReferenceGraphModal` delegates container clicks on the tooltip's `data-viz-open` /
  `data-viz-import-all` links (single open / multi-line import prefill); a `likely_local` click opens
  its candidate work. **Batch 12 complete** (fast-follow: multi-paper citation-graph resolver, D5,
  still deferred).

## issue_batch_11 (2026-07-10)

Owner-reported duplicate-PDF handling issues (follow-up to batch 10). Handoff:
`docs/agent_handoffs/2026-07-10-issue-batch-11.md`. On `main` (not pushed). Two commits.

- **Double-import / wrong-owner diagnosis.** SHA-256 dedup makes an attached duplicate PDF reuse the
  original `File` row (+ a new `FileWorkLink`); `File.status` is shared so paper B shows "extracted"
  instantly, and `extract_and_store` resolves the target work via an unordered first `FileWorkLink`
  (extraction.py:249) → metadata always lands on the file's original paper. **Full per-paper
  extraction of a shared PDF is deferred** (documented); this batch delivers awareness:
  - `WorkFileRead.also_in_count` (batched) + a clickable **"duplicate PDF"** badge in the Files
    section that runs a Library hash search to find the other paper(s).
  - Attaching an already-extracted deduped PDF no longer enqueues a misleading no-op re-extraction.
  - New **`shared_file`** duplicate detector (`find_work_candidates`) flags two works sharing one
    `File` row — closing the gap where shared-PDF pairs were only caught on DOI/title match.
- **Dedup actions:** "Ignore" is now **transient** — deletes the candidate (audit event kept) so a
  re-scan re-surfaces it; "Keep separate" stays permanent/reviewable/reopenable (status `rejected`,
  UI relabeled "Kept separate"; the now-unused "Ignored" filter dropped).

## issue_batch_10 (2026-07-10)

Eight owner-requested features (multi-PDF import, duplicates preview + open-in-paper-view,
best-source "all", editable authors in Details, new library columns, popup/tab autofocus, citation
summary venue/author sub-tabs, external citing papers). Plan: `docs/WORKPLAN_2026-07-10_batch10.md`
(design forks resolved by owner); handoff: `docs/agent_handoffs/2026-07-10-issue-batch-10.md`. On
`main` (not pushed). One commit per logical chunk; verified in the API + frontend containers.

- **8 — external citing papers.** `citing_papers` service (OpenAlex `filter=cites:` → Semantic Scholar
  `/citations` fallback; Crossref gives count only). **Normalized, deduplicated permanent storage**
  (`external_papers` + `external_citation_links`, migrations 0057→0058): a citer shared by several
  works is stored once + referenced; orphans GC'd on refetch. Endpoints
  `GET/POST /works/{id}/citing-papers[/fetch]` (on-demand, cap 100); paper-view "Citing papers" panel
  with per-row Import; reference graph `include_citing` → incoming `citing` nodes + "Show citing
  papers" toggle. ✅ done.

- **3 — "Set metadata from best source" → "all fields".** `field_name: "all"` on
  `POST /works/bulk-apply-metadata` promotes the best assertion for every promotable field
  (per-field helper `_promote_best_field`), a paper counting as applied if ≥1 field was set;
  locked fields still skipped. Frontend: "all fields" picker option + adjusted toast. ✅ done.
- **4 — editable authors in the Details panel.** New `POST /works/{id}/metadata/set` writes a
  user-sourced canonical `authors` assertion + locks the field (no Work column added). Details panel
  gains an Authors input seeded from the assertion; save persists only on change. ✅ done.
- **2 — duplicates preview fix + open both papers in the paper view.** Base/merge-from labels are
  now buttons that open the WorkDetail modal (reusing the CitationSummaryPage flow). `getMergePreview`
  is time-bounded (15s) and a null preview now renders an explicit "Preview unavailable" note instead
  of a permanent "Loading preview…". ✅ done.
- **5 — new library columns (file count, topics, badges incl. conflicts, tags).** `WorkRead` +
  `list_works` enriched via `_batch_library_columns` (4 grouped queries/page: files→count+status+
  text-layer, tags, conflict probe over metadata_assertions). Frontend: 4 registry columns (hidden by
  default), `PaperTable` render branches + badge-token→chip mapping. ✅ done.
- **7 — citation summary venue + author sub-tabs.** New `venue_author_summary` service +
  `GET /citations/venue-author-summary` (reuses `_scope_works`, SEE-clamped). Basic dedup: venues by
  case/punct-insensitive key, authors by last+first-initial; merged spellings surfaced as `variants`.
  Frontend: Overview/Venues/Authors sub-tabs in CitationSummaryPage with stat tables. External venue
  metadata deferred (free-text venue, no identifier). ✅ done.
- **6 — popup/tab autofocus + Move default title.** New `focusOnMount` action; ShelfPicker (Put into),
  WorkPicker (Move/Merge) autofocus their main field; Move picker seeded with the paper title; Search &
  Tags tabs focus their input on tab entry (edge-triggered on the new `visible` prop). ✅ done.
- **1 — multi-PDF staging import (extract-first preview + "import directly").** New staging model
  (`import_staging_batches`/`items`, migration 0056) + `import_staging` service: stage → record-free
  extract (worker `extract_staging_item_job`, inline fallback when the queue is down) → collision
  detect (same PDF/DOI/title) → commit (applies stored TEI, no GROBID re-run). Endpoints
  `POST /imports/upload-multi`, `GET /imports/staging/{id}`, `POST /imports/staging/{id}/commit`.
  Direct mode auto-accepts extracted, non-blocked items (blocks on same PDF or DOI). Frontend: Import
  tab multi-file card + preview table + commit. ✅ done.

## issue_batch_9 (2026-07-10)

Four more owner-reported items (reconcile false-positive, Jobs tab, find-on-web counter,
record consolidation) on `main` (not pushed). Plan: `docs/WORKPLAN_2026-07-10_batch9.md`;
handoff: `docs/agent_handoffs/2026-07-10-issue-batch-9.md`. One commit per logical chunk;
verified in the API + agent + frontend containers.

- **1 — reconcile wanted to un-index everything after deleting duplicates.** Root cause: reconcile
  diffs the local index against `get_my_files`, and deleting a paper record deletes its `AgentFile`
  row, so a just-deleted duplicate's file looked "absent". Made it content-aware: new
  `POST /agents/files/known-hashes` reports which of the agent's hashes still exist as a `File`
  linked to a paper; reconcile drops those candidates (content survives under the canonical paper),
  flagging only files whose content is genuinely gone. Degrades to the raw diff on an older server.
- **2 — Jobs tab.** (a) Nav semaphore enlarged with a soft glow, green/blue lightened + blue leaned
  cyan so idle vs running read apart. (b) Freeze/stuck-loading hardened: payload normalised so an
  absent `counts`/`jobs` can't throw mid-render, last-good status kept on a refresh error (tab stays
  interactive), a Retry placeholder only on a failed first load, overlapping-poll guard, 15s
  `getJobs` timeout, and a note explaining a positive filter count with no jobs in the recent window.
- **3 — find-on-web "1/1 downloaded" on failure.** The counter incremented per processed item; now
  only `attached`/`deduped` count as downloaded, and a failed batch shows `<ok>/<total>` in red with
  `(N failed)`.
- **4 — consolidate two records.** Added move-file (`POST /works/{id}/files/{file_id}/move`,
  re-points a `FileWorkLink`) and exposed the existing `merge_works` for arbitrary papers
  (`POST /works/{id}/merge` + `/merge-preview`, reversible via the existing `/unmerge`). New
  `WorkPicker` typeahead + "Move…"/"Merge…" actions in the paper detail.

## issue_batch_8 (2026-07-09)

Eight owner-reported items (test warnings, library UX, extraction robustness, jobs status, agent
dedup, search modes, keyword quality) on `main` (not pushed). Plan:
`docs/WORKPLAN_2026-07-09_batch8.md`; handoff: `docs/agent_handoffs/2026-07-09-issue-batch-8.md`. One
commit per logical chunk; verified in the API + frontend containers.

- **6 (critical) — agent scan&push duplicate papers.** `ingest_manifest` now looks up an existing
  `File` by sha256 before minting a filename-titled `index_only` stub Work; when the content is
  already in the library it links the `AgentFile` to that File's existing Work instead of creating a
  duplicate. Closes the one ingestion path that never went through the hash-deduped
  `_ensure_managed_file`.
- **3 — DOI collision.** Kept the fail-closed behavior (prevents duplicate accumulation); extracted a
  shared `app.services.doi_conflict` module so the message now names the offending DOI + the paper
  that holds it, and wrapped 4 previously-500ing endpoints (`update_work`, select/bulk-apply/delete
  metadata assertion, find-on-web apply) to return a clean 409. Cross-visibility caveat recorded in
  `ROADMAP.md`.
- **8 — keyword extraction overhaul.** YAKE (new light dep, guarded import) + RAKE fused via RRF, then
  phrase filtering (>4 words / no content word / mostly-stopword), boundary stop-word trimming,
  title/abstract/heading boosting, and plural-aware near-duplicate dedup. Optional corpus-IDF rerank
  (`build_corpus_idf`) for a future library-wide pass. api+worker images rebuilt.
- **4 — Jobs nav badge.** A 20s poll drives a semaphore dot next to the Jobs tab (red/yellow/green +
  blue when running/queued) with a `[N]` queued count; shared `lib/jobsHealth` helper.
- **7 — "both" search mode.** Library dropdown gains "both", wired to the existing unified hybrid
  (BM25F+ / dense RRF) `/search` endpoint; `SavedFilter.search_mode` widened to include `hybrid`.
- **2 / 5 — library filter panel.** Reset moved out of "More filters" next to Save current filter;
  scoped `.compact` sizing reclaims vertical space without a global button resize.
- **1 — test warnings.** `authorization` headers → `Annotated`; `HTTP_413_REQUEST_ENTITY_TOO_LARGE`
  → `HTTP_413_CONTENT_TOO_LARGE`; the FastAPI-internal pydantic alias warning narrowly ignored in
  `pyproject.toml`. Safety suite now 0 warnings (was 3).

## Easy-audit-items batch + CI fix (2026-07-09)

Self-contained audit items that needed no owner decision, plus an owner-reported CI regression, on
`feature/library-resize` (not pushed). Plan: `docs/WORKPLAN_2026-07-09_easy-audit-items.md`. Handoff:
`docs/agent_handoffs/2026-07-09-easy-audit-items.md`. One commit per chunk; each verified in the API
container (fast suite, migration parity on Postgres, upload-abuse safety, rate-limit/queue-cap) and
the frontend container (Vitest + `npm run build`).

- **Docs — audit merge.** Folded the extended audit's non-issue narrative into `docs/AUDIT.md` as
  Appendix A and removed the standalone `docs/AUDIT_EXT.md`, so there is a single audit register.
- **CI fix — flaky `no such table: groups`.** Seven services memoized optional-table existence in a
  dict keyed on `id(db.get_bind())`; CPython reuses a GC'd engine's address, so a later narrow test
  DB inherited a stale `True` and queried a missing table. Replaced all seven with one
  `WeakKeyDictionary`-backed `app.utils.table_presence` helper (purged on engine GC). Regression
  test added. Full fast backend suite green (671 passed).
- **AUDIT E2 — parser-level PDF validation.** `storage.probe_pdf_openable` opens uploaded bytes with
  PyMuPDF and fails closed on encrypted/page-less/unparsable PDFs, wired into all five upload
  handlers after the `%PDF` header check so invalid bytes never reach GROBID/OCR. Unit + upload-abuse
  tests; upload happy-path tests now send real openable PDFs.
- **AUDIT E1 — Redis fail-closed option.** `PARACORD_PRODUCTION_REQUIRE_REDIS` (default off): when
  set and Redis is unreachable, rate-limit → 503 (`unavailable` scope) and queue-capacity → 503
  instead of failing open; Jobs page shows a red "limits unavailable" banner
  (`queue_status.require_redis`). Login-throttle already degrades to a per-process window, left as-is.
- **AUDIT L7 — `Agent.revoked_at` removed.** Dead column (revocation is via `status`/`delete_agent`)
  dropped; migration `0055_drop_agent_revoked_at`, Postgres parity green. Spec table updated.

## issue_batch_7 — extraction/dedup, viz, library & metadata UX (2026-07-08)

Implemented the "ready to build" items from `docs/WORKPLAN_2026-07-08_batch7.md` (the triage doc,
intentionally uncommitted). Deferred by owner decision: **4** (agent reconcile — not reproducible),
**7** (OCR reader text — bigger, separate), **8 Tier 2** (venue abbreviation matching), **5i**
(co-citation plain-string hash — unlocated, low priority), and the broader **1a** instability.

- **1a** — agent-initiated `offer_teleport` now reuses the `index_only` stub Work via
  `AgentFile.work_id` (mirrors `complete_teleport`/`extract_and_index`) instead of creating a
  duplicate (`backend/app/services/agent_files.py`).
- **1b** — extraction (`assert_field`) + enrichment (`_store_external`) skip inserting a
  MetadataAssertion when an identical (work, field, source, value) row already exists (reuse it),
  ending the "same value duplicated every run" growth.
- **1c** — `enqueue_enrichment/embedding/chunking/topics/keywords` now use deterministic per-work
  job ids + in-flight skip (like extraction), so a manual re-run can't race the auto-chain and make
  results vary (`backend/app/workers/queue.py`).
- **6** — `extract_pdf_job`/`enrich_work_job` catch a narrowed `uq_works_doi` `IntegrityError`,
  roll back, mark the file failed (clearing the owed marker → no D7 retry loop), record a
  `metadata.doi_conflict` audit event, and surface a clear message instead of a raw SQL crash.
- **11** — GROBID venue+year for the *primary* paper are now mined from the TEI header monograph
  (`ParsedPaper.venue/year`, `parse_tei`, promoted in `store_parsed_extraction`). `Work.venue`
  already existed; only extraction was missing.
- **5b/5d/5h/5j** — viz gains a `keyword_similarity_to_focus` axis, colour-by-venue and
  colour-by-year (discrete per-year), and size-by-year (`visualization.py`, `VisualizationsPage`).
- **5a/5c/5e** — reference graph: 0-lane and n/a-lane each drawn as a labelled `markLine`; tooltip
  `confine`; explicit Y-axis tick formatters. Tick formatters also extended to temporal-map
  non-year axes and embedding-cluster PCA axes.
- **5f** — manual X/Y min/max view-range inputs on the temporal map (empty = auto), so a corrupt
  outlier year no longer stretches the plot.
- **5g** — clicking an external reference node jumps to Import and prefills the batch-import box
  with `"Title (year)"` via a new `pendingImportText` store; the ref-graph node payload now carries
  `venue`/`doi`.
- **9** — Find-on-web results gain a "Use metadata" action → `POST
  /works/{id}/find-on-web/apply-metadata` records the values as reviewable `web_find:*` candidate
  assertions (non-trusted → user promotes via "Use this"; arXiv id backfilled).
- **10** — the References tab "in library" badge is a button that opens the resolved paper.
- **3** — the library selection toolbar folds Delete/Re-extract/Extract-keywords/Extract-topics/
  Enrich into an action dropdown + Go (Put-into/Set-status kept separate), plus "Set metadata from
  best source" → `POST /works/bulk-apply-metadata` (prefers GROBID, skips locked fields).
- **12** — AI & Models batch keyword/topic extraction: `POST /admin/ai/keywords|topics/batch`
  (`all`/`missing`) + `GET /admin/ai/keyword-topic-status`, reusing the existing per-work RQ jobs.

No schema/migration changes (venue/year columns already existed). Every item shipped with tests;
backend fast tier + the touched slow suites (extraction/visualization/reference_graph) + full
frontend suite (233) + frontend build all green. See
`docs/agent_handoffs/2026-07-08-issue-batch-7.md`.

## Viz help + duplicates sub-tabs (2026-07-08)

Two small UX features on `main` (not pushed). Frontend-only; `npm run build` clean, full Vitest
suite green (224 passed / 4 skipped).

- **Visualization axis help.** New `AXIS_OPTION_HELP` / `axisOptionHelp()` in `lib/viz/vizHelp.ts`
  explain each temporal-map X/Y axis option (year, citation count, local degree, citation velocity,
  similarity/topic-similarity to focus) — what it is and how to read it. Surfaced as a "How to read
  each axis" list in the temporal map's "About this view" modal.
- **Reference-graph help modal.** `ReferenceGraphModal` gains a "ⓘ Help" button opening a modal
  that documents the layout/encodings (X = year, size = section-weighted citations, colour = node
  kind, the "n/a" lane), every selectable Y axis, and the "Local reference-to-reference edges"
  toggle. Help text lives as data (`REFERENCE_GRAPH_HELP` + a `help` field on each `REFERENCE_Y_AXES`
  entry) in `lib/viz/referenceGraph.ts` so it's unit-testable.
- **Duplicates split into two sub-tabs.** `DuplicatesPage` now partitions candidates client-side
  (one fetch) into a **Duplicates** tab (same-DOI/arXiv, fuzzy-title, identical-file pairs) and a
  **Multi-work files** tab (`multiwork_file`, mostly false positives) with per-tab counts and empty
  states, so the noisy multi-work results no longer bury the largely-correct duplicate results.

## Batch A — local-agent overhaul (2026-07-07)

Five phases of the local-agent overhaul (`docs/AGENT_OVERHAUL_DESIGN.md`, FINAL MODEL), one commit
per phase on `main` (not pushed). Agent-only. See
`docs/agent_handoffs/2026-07-07-batch-a-agent-overhaul.md`. Agent suite: 34 → 58 tests, all green;
`ruff check/format agent` clean.

- **Phase 1 — truthful status model (fixes the "no longer on this workstation" bug).** `present`
  now means **exists-on-disk** (re-`stat`ed by `state.refresh_presence()`), independent of scan
  membership; the buggy `mark_absent_except` is gone. `agent_ops.classify(real_path, config)`
  derives `watched` / `unwatched` (on disk, outside every enabled watched root — labelled "on disk,
  not in a watched folder") / `missing` (gone from disk — the correct "no longer on this
  workstation"). `sync` reports `report_source_removed` **only for truly-missing** files, never for
  merely-unwatched, and a subset scan never flags other roots' files.
- **Phase 2 — prune + unwatch dialog.** Per-item Forget/Prune, a "Prune unwatched" bulk button, and
  multi-select bulk Forget/Prune/Teleport/Block/Unblock/Re-extract in the Indexed tab; status badge
  counts + status/blocked filters + status sort. Removing/disabling a folder that leaves
  now-unwatched files opens a keep-by-default dialog (checkbox "also remove these from the index
  now" prunes only the listed ids). Forward auto-prune is a config toggle **default OFF**. Pruning
  never contacts the server.
- **Phase 3 — reverse-sync "Reconcile with server".** Distinct from forward "Scan & push". Dry-run
  preview, then un-indexes server-deleted files (only `SERVER_KNOWN_STATES`, never a never-pushed
  `index_only` row). Guarded one-shot delete-on-disk: strict watched-folder boundary (symlink
  escapes rejected), two-dialog gating + arm flag, hard cap 100 (refuses, no partial delete),
  self-disabling after one run, moves to a recoverable trash dir (never `unlink`).
- **Phase 4 — feedback + tooltips.** Spinner + completion toast on Scan & push / Reconcile /
  Refresh; tooltip audit across the GUI; auto-prune toggle surfaced in the Folders tab.
- **Phase 5 — CLI parity.** `reconcile` (`--apply`, `--delete-on-disk` gated by `--confirm-delete`),
  `prune-unwatched` (`--dry-run`), and bulk `forget <id>...`.

## Batch P — paper view / metadata (2026-07-07)

Three WORKPLAN Batch P items, one commit per item on `main` (not pushed). See
`docs/agent_handoffs/2026-07-07-batch-p-paper-view-metadata.md`.

- **P1a — live-refresh the open paper after a background job.** `WorkDetail` now polls the jobs
  queue (4 s, self-terminating) only while an extract/enrich/topic/keyword job is in flight for the
  open paper, and refetches the work (+ re-runs `loadDetail`) once every relevant job settles — no
  navigate-away/reload. Jobs are correlated by `target_id` (work id) or the file ids the paper owns
  (extraction is file-targeted). Started from the action handlers (with the returned job ids, so a
  fast job isn't missed) and auto-detected on open for a job already running (e.g. import
  extraction). A web-download also refetches so backfilled identifiers show immediately.
- **P1b — mutations refresh their view.** Audited every mutation handler across library/shelves/
  racks/tags/duplicates — shelves, racks, tags, duplicates, and library batch-delete already
  refetch. Fixed: **library single-delete** now also decrements the `{n} papers` counter and page
  count (the row was already spliced out, but the count/pagination were stale); **search-results**
  delete now removes the row from the results list (previously only closed the modal).
- **P2 — metadata-conflict "match %".** `GET /works/{id}/metadata` `FieldReview` gained
  `match_pct` (0–100, null when no conflict) = lowest pairwise similarity among the distinct
  conflicting values. New `normalize_for_similarity` (join line-break hyphenation, collapse
  whitespace, lowercase) + `similarity_pct` (rapidfuzz `max(ratio, token_set_ratio)`, difflib
  fallback) in `utils/normalization.py`; values differing only by formatting score ~100. The paper
  view shows a "N% match" badge next to each conflicting field.
- **P3 — arXiv id / DOI not filled from web/enriched papers (bug).** Root cause: the find-on-web
  download path dropped the candidate's identifiers (`WebCandidate` had no `arxiv_id`;
  `WebFindDownloadItem`/`download_and_attach` never carried or persisted doi/arxiv_id), and
  enrichment could never set `arxiv_id` (absent from `ExternalMetadata`/`_apply_field`). Fix: a
  shared `identifiers.backfill_identifiers` fills an EMPTY, unlocked `arxiv_id`/`doi` (normalized);
  wired into `download_and_attach` (candidate carries doi + arxiv id through the API) and into
  enrichment (arxiv id parsed from the arXiv Atom `id` and Semantic Scholar `externalIds.ArXiv`).
  User-confirmed/locked values are never overwritten.

Verified: full backend suite green; `make frontend-check` (181 passed / 1 skipped) + build; ruff +
openapi-check clean. `openapi.json` regenerated (FieldReview.match_pct, WebCandidateRead.arxiv_id,
WebFindDownloadItem.doi/arxiv_id).

## Batch L — library / insights UX (2026-07-07)

Four owner-facing UX fixes (WORKPLAN Batch L), one commit per item on `main` (not pushed). See
`docs/agent_handoffs/2026-07-07-batch-l-library-insights-ux.md`.

- **L1 — Inbox excluded from Put-into menus.** The default/Inbox shelf (the loose-paper fallback)
  is no longer offered as a move-target. Robust marker: `access_settings.default_shelf_id` surfaced
  as `is_default` on `ShelfRead` (no migration), filtered by a new `ShelfPicker.excludeDefault` on
  the single **Put into…** and batch **Put all into…** menus. Inbox stays visible elsewhere.
- **L2 — redundant Insights search removed.** Search has its own tab; dropped the Insights-tab
  search card + its state/handlers/styles + obsolete vitest, and the "semantic search" tab hint.
- **L3 — Jump-to-open button.** A library-toolbar button scrolls the open paper's row into view and
  briefly flashes it (`data-work-id` + a flash pulse). Off-page (not in the current result set) it
  explains rather than jumping.
- **L4 — scope summary honors the configured AI model.** `POST /ai/summaries` resolves the provider
  from the admin AI config (`summary_provider`/`summary_model`) when unset — like per-work summaries
  — so a configured `local_llm` model is used; the Insights UI shows an "extractive — set a model in
  Admin → AI" hint (or the fallback reason) whenever the result isn't model-based.

Verified: full backend suite 881 passed; `make frontend-check` 176 passed / 1 skipped + build;
ruff + openapi-check clean. Screenshots (not committed) in `/home/zednik/paracord-theme-shots/`:
`putmenu_no_inbox.png`, `insights_no_search.png`.

## Theming UX — distinct selected state + "load as template" (2026-07-07)

Two owner-reported theming fixes; frontend + one small read-only backend endpoint, on `main`
(not pushed). See `docs/agent_handoffs/2026-07-07-theme-picker-selected-state-and-template-load.md`.

**T1 — rack/shelf picker selection was invisible in the dark themes.** Two causes: (a) `--surface-hover`
in `mocha-warm`/`mocha-cool` was nearly identical to the button overlay background, so hover read as
no change (brightened it to sit clearly above the overlay; the two light themes were already fine);
(b) the selected row used the neutral `--status-success-bg`, too close to hover. Added an
accent-tinted **`--surface-selected`** (+ `-border`, `-text`) role token to the schema + all 4 YAMLs
(AA-contrast text, distinct from both base and hover), regenerated `themes.generated.ts`, and applied
it to the `.item.active` rows in `RacksPage`/`ShelvesPage` (kept on `:hover` too so the selection
stays highlighted under the cursor).

**T2 — "Load existing as template" in the admin custom-theme editor.** A picker of bundled + custom
themes prefills the YAML editor from an existing theme. Bundled YAML is now compiled into the app
(`build-themes.mjs` emits `bundledThemeYaml`); custom YAML is served by a new read-only
`GET /themes/{slug}/source` from the stored `yaml_source` (frontend `getThemeSource`). openapi.json
regenerated.

Verified: vitest 171 passed / 1 skipped (+ new selected/hover-token and template-load tests), build
clean, `test_custom_themes.py` 12 passed, `check_secrets` clean. Playwright shots (owner, 1440×900
@2x, uncommitted) in `/home/zednik/paracord-theme-shots/`: `picker_states_mocha-cool.png`,
`picker_states_latte-warm.png`, `theme_template_load.png`.

## Batch W — Makefile & Docker workflow robustness (2026-07-07)

Batch W of `docs/WORKPLAN_2026-07-06.md`; infra-only (Makefile / compose / entrypoint / docs),
committed on `main` (not pushed). Goal: `make init && make up-all` works out of the box and a
`git pull` + rebuild self-heals (deps, ownership, migrations) with no manual steps.

**Frontend node_modules self-heal — verified + solidified.** Traced all four cases in
`frontend/docker-entrypoint.sh` against the baked copy at `/opt/paracord-frontend` (Dockerfile) and
`.dockerignore` (which excludes `frontend/node_modules`, so the image's `npm ci` tree survives
`COPY frontend ./` and seeds a fresh volume): (a) fresh/empty volume → baked-copy restore, no
`npm ci`; (b) hash match → no-op; (c) hash mismatch with matching baked copy → fast restore;
(d) mismatch, no baked match → `npm ci` fallback. Marker `.paracord-package-lock.sha256` is written
**last** so a crash mid-restore just re-heals. **Hole fixed:** the old chown guard checked only the
top-level `node_modules` dir, so after a restore/`npm ci` (which rewrites contents as root while the
dir keeps its owner) the new files stayed root-owned and were never chowned. Rewrote the entrypoint
to track a `healed` flag and always `chown -R node:node` after any heal, in addition to the
fresh-root-volume check.

**Named-volume ownership audit.** Full table (volume → writer → ownership handling):

| Named volume | Written by | Runs as | Ownership fix |
|---|---|---|---|
| `paperracks_postgres` | postgres | postgres (official image) | image-managed; no action |
| `paperracks_ollama` | ollama | root (official image) | n/a |
| `paperracks_library` (`/app/storage`) | api + worker | `appuser` (UID 1000) | `backend/docker-entrypoint.sh` chowns before `gosu` (both share the entrypoint) |
| `paperracks_frontend_node_modules` | frontend | `node` (UID 1000) | `frontend/docker-entrypoint.sh` chowns before `gosu` |

`frontend/dist` is on the `./frontend` bind mount (not a named volume) and is also chowned by the
frontend entrypoint. The **agent** container runs non-root (`USER appuser`) but mounts only the
`./agent` bind mount — no root-owned named volume — so no entrypoint chown is needed (confirmed; no
change).

**Startup ordering.** `docker-compose.yml`: added `start_period: 30s` to the api healthcheck (so a
cold start running `alembic upgrade head` isn't marked unhealthy before it listens) and made the
**worker depend on `api: service_healthy`** — since both share the migration-on-start backend
entrypoint, an healthy api means the schema is at head before the worker touches any table. Postgres
(`pg_isready`) and Redis (`redis-cli ping`) healthchecks and the api→postgres/redis `service_healthy`
gates were already present.

**New Make targets** (`## help`-annotated, consistent `.PHONY`/`$(COMPOSE)` style): `make rebuild`
now `build --no-cache` **then** `up -d --force-recreate` (was build-only); `make fresh` — destructive
clean slate guarded by `CONFIRM=1`, drops `paperracks_postgres` + `paperracks_library` +
`paperracks_frontend_node_modules` (targeted via compose `project`+`volume` labels) and `frontend/dist`,
**keeps** `paperracks_ollama`, then `up -d --build`; `make smoke` — asserts postgres/redis/api
`/health`/frontend/worker, checks grobid/ollama only when their profiles are up, non-zero on failure.

**Proof (ran against the live stack, no data touched).** Rebuilt the frontend image with the new
entrypoint and recreated only the frontend container: entrypoint logged "restoring dependencies from
the baked image copy", wrote the marker, deps (`yaml`/`echarts`/`cytoscape`) came back **owned by
`node`**, served HTTP 200 — no manual `docker volume rm`. Then simulated a dependency change (bumped
`frontend/package.json` + `package-lock.json` version `0.0.0`→`0.0.1`, lock hash
`f939…`→`4989…`), rebuilt (npm ci reran) + recreated: the stale volume marker mismatched, the new
baked hash matched, deps restored from the baked copy, marker updated to `4989…`, HTTP 200 —
self-healed with no manual volume removal. Reverted the version bump and rebuilt/recreated to restore
the original state (marker back to `f939…`, `git diff` on the package files empty). `make smoke`
passes; api container health = `healthy`. Did **not** run `make fresh` (would drop the demo DB);
instead verified its volume-selection labels resolve to exactly the three app volumes (ollama
excluded) and `make -n fresh` parses. `check_secrets` clean.

Handoff: `docs/agent_handoffs/2026-07-07-batch-w-docker-workflow.md`.

## Reader Batch R — Dim/Dark tuning + reference-box overhaul (2026-07-07)

Batch R of `docs/WORKPLAN_2026-07-06.md`; frontend-only, committed on `main` (not pushed). **R1 —
Dim** is now a warmer, lighter cream: `readingMode.ts` `DIM_FILTER =
'sepia(0.5) saturate(1.12) brightness(0.98) contrast(0.96)'` (was `sepia(0.35) brightness(0.93)
contrast(0.95)`) — higher sepia + faint saturate for warmth, brightness near 1 so it barely dims (a
white page lands ≈ #faf9f2). **R2 — Dark** now reads as a *yellowish dark grey* (not near-black):
`DARK_FILTER = 'invert(0.82) hue-rotate(180deg) sepia(0.28) brightness(1.02)'`. Key trick: CSS
`invert(a)` maps white→`1-a`, so a **partial** `invert(0.82)` lands white on a warm dark grey
(≈ #333128, in the requested #2a2632–#332f3a band) and lifts black to warm-light AA-readable text;
hue-rotate keeps colours, sepia warms the field. Achieved with a CSS filter alone — no canvas backing
colour needed (a backing behind an opaque painted canvas wouldn't show). **R3 — reference anchor
boxes**: extracted the overlay geometry into a tested pure helper
`frontend/src/lib/reader/overlayBoxes.ts` (`overlayBoxStyle(box, scale)` +
`citationBoxesForPage`/`annotationBoxesForPage`), driven by the **same** `scale` the canvas renders
at so boxes track zoom/resize/re-render; `PdfReader.svelte` now draws citation + annotation overlays
**per page in BOTH paged and scroll views** (scroll mode previously drew none — each `.scroll-page`
canvas now gets its own sibling overlays). The R1/R2 filter still targets `.canvas-stage canvas` only,
so overlays stay untinted; click/jump behaviour unchanged. Verified: `make frontend-check` green (165
tests, 7 new in `overlayBoxes.test.ts`, `readingMode.test.ts` updated for the new strings; build OK);
`check_secrets` clean; re-captured (not committed) `reader_mode-{dim,dark}.png` in
`/home/zednik/paracord-theme-shots/`. **Deviation:** no `reader_refbox-{paged,scroll}.png` — no paper
in the stack has citation `pdf_coordinates` (so no real overlay boxes exist) and DB seeding was out of
scope; R3 is covered by the geometry unit tests + the shared annotation-overlay path. Handoff:
`docs/agent_handoffs/2026-07-07-reader-batch-r.md`.

## Reader "reading mode" — opt-in page-canvas easing (2026-07-04)

Added an opt-in **reading mode** to the PDF reader (`PdfReader.svelte`), frontend-only, committed on
`main` (not pushed). A segmented toolbar control **"Page: Original / Dim / Dark"** eases the rendered
page on the eyes without touching the document: **Original** (default, no filter — nothing changes
for users who don't opt in); **Dim** (`sepia(0.35) brightness(0.93) contrast(0.95)` — warm cream,
gently dimmed); **Dark** (`invert(1) hue-rotate(180deg) brightness(0.92) contrast(0.95)` — dark page,
light text, hues roughly preserved). The filter is applied to the **page canvas ONLY** via a
`--page-filter` CSS var on `.page-wrap` consumed by `.canvas-stage canvas`; the text-selection layer
and the highlight / citation / annotation overlays are DOM *siblings* of the canvas (not descendants),
so they keep their true colours — verified in Dark mode with a live search highlight staying
orange/amber (not inverted). Works in both paged and scroll view (all canvases share the var). The
choice persists per user in `localStorage` under `paracord.reader.readingMode` (mirrors
`paracord.reader.viewMode`) and restores on open. Mapping/persistence live in a testable helper
`frontend/src/lib/reader/readingMode.ts`. Verified: `make frontend-check` green (158 tests, 5 new;
build OK); `check_secrets` clean; 3 mode screenshots re-captured (mocha-cool, not committed) in
`/home/zednik/paracord-theme-shots/reader_mode-{original,dim,dark}.png`. Handoff:
`docs/agent_handoffs/2026-07-04-reader-reading-mode.md`.

## GUI bug fixes — header overlap, dark-theme metadata text, squashed charts (2026-07-03)

Fixed three owner-reported GUI bugs; frontend-only, committed on `main` (not pushed). **Bug 1**
(sticky nav overlapped content): the wrapped tab bar makes the header ~126px tall, but
`LibraryPage`'s split-pane assumed `calc(100dvh - 7rem)`, oversizing the pane so it overflowed the
viewport and the header covered content on scroll — `App.svelte` now measures the header (guarded
`ResizeObserver`, not `bind:clientHeight`, so App's jsdom tests don't crash) into `--app-header-h`
and the pane uses `calc(100dvh - var(--app-header-h,4rem) - 3rem)` (pane bottom = viewport exactly).
**Bug 2** (black-on-dark metadata text in dark themes): the global `input/select/textarea` rule set
a dark background but no `color`, so native controls defaulted to near-black — added
`color: var(--ink-strong)` (offending selector `:global(input),:global(select),:global(textarea)`
in `App.svelte`); now `#cdd6f4` on the dark surface, AA pass, light themes unaffected. **Bug 3**
(ECharts/Cytoscape squashed to ~50px): they init before the flex/tab container has its width — added
a guarded `ResizeObserver` per container calling `chart.resize()` (`VisualizationsPage`,
`CitationSummaryPage`) / `cy.resize()` + debounced `relayout()` (`CitationGraph`), disconnected on
destroy; charts now fill their container in light + dark. Also added
`optimizeDeps.include: ['echarts','cytoscape','cytoscape-fcose']` to `vite.config.ts` (kills the
first-import 504). Verified: `make frontend-check` green (153 tests, build OK); `check_secrets`
clean; screenshots re-captured (not committed) in `/home/zednik/paracord-theme-shots/`. Handoff:
`docs/agent_handoffs/2026-07-03-gui-bugfixes-header-darktext-charts.md`.

## Theming — Playwright E2E journeys (2026-07-03)

Closed the browser-E2E gap for theming (P3/P4 shipped with vitest + backend tests but no Playwright
coverage). Four new journeys under `e2e/tests/`, following the existing numbering/helpers/style
(auto-waiting locators, seed-own-data, idempotent, no arbitrary sleeps): **29** switch theme +
persist (pick a dark theme, assert `data-theme` flips **and** `--surface-base` actually changes,
server `/auth/me` + localStorage cache persist across reload, switch back); **30** all four bundled
themes apply from the picker (assert `data-theme` + a key element stays visible per theme) and the
Visualizations chart still builds under a dark theme; **31** follow-system via
`page.emulateMedia({ colorScheme })` — enabling the toggle resolves the light/dark member of the
current temperature pair (`latte-warm ↔ mocha-warm`) and re-picks on OS flip (the matchMedia change
listener fires); **32** admin uploads a tiny schema-complete custom theme YAML via the admin Themes
tab, it appears in the picker, applies live (`data-theme` = slug, `--surface-base` = its value), then
is deleted. **Frontend touch (testids only, no behavior change)**: `data-testid="theme-option-<id>"`
on each picker button and `data-testid="follow-system"` on the follow checkbox in
`ProfilePage.svelte`; two cleanup helpers in `e2e/helpers.ts` (`apiSetUserTheme`,
`apiDeleteCustomTheme`). Verified: full E2E suite **31 passed / 2 skipped** (the pre-existing GROBID
journey 9 + arXiv journey 19 external-service skips; no new skips); `make frontend-check` green
(**153 tests**, 1 skip; build OK); `check_secrets` clean. No backend change. Handoff:
`docs/agent_handoffs/2026-07-03-theming-e2e-journeys.md`.

## Theming P4 — custom / hand-edited YAML themes (2026-07-03)

Final theming phase (`docs/THEMING_DESIGN.md` P4): an owner/admin can add a theme from YAML at
runtime, **no rebuild**, and everyone can pick it. **Storage**: a `custom_themes` DB table (id/slug/
name/mode/temperature/yaml_source/created_by/created_at) — chosen over a storage-volume directory
because it backs up with the DB and keeps the canonical YAML in one place (migration
`0051_custom_themes`). **Backend**: `app/core/theme_schema.py` validates + palette-resolves the YAML
to the exact frontend `Theme` shape (reject → 400 on malformed YAML, missing required token role, bad
`id`/`mode`, or a slug colliding with a bundled id; omitted presentational `graph` keys are defaulted
from tokens); `app/core/palette_check.py` is a Python port of the frontend categorical validator that
produces **advisory warnings** (never rejects) for a low-readability palette. Endpoints: `POST
/admin/themes` + `DELETE /admin/themes/{slug}` (owner/admin, audit-evented `theme.uploaded`/
`theme.deleted`), `GET /themes` (list + swatch) and `GET /themes/{slug}` (resolved object) for any
authenticated user. Per-user theme validation moved to the service layer so a **custom slug** is a
valid preference (unknown id now → 400; malformed slug still → 422). **Frontend**: a runtime custom-
theme registry (`lib/theme/index.ts` `registerCustomTheme`/`getTheme`/`allThemes`) so a custom theme
applies through the *same* `renderThemeCss`/`VizTheme` path as a bundled one; the store gains
`customThemeOptions`/`allThemeOptions` + `loadCustomThemes`/`ensureThemeLoaded` (fetched on boot after
`/auth/me`, merged into the picker, and — when the wanted theme is custom — resolved + applied live);
an admin **Themes** tab in `AdminPage.svelte` (paste YAML → upload/replace/delete, shows readability
warnings). A theme is now portable YAML. Verified: full backend suite **877 passed**; migration parity
green; ruff clean; `backend/openapi.json` regenerated; `make frontend-check` green (**153 tests**, 1
skip; build OK). Runbook: `docs/runbooks/theming.md`. Handoff:
`docs/agent_handoffs/2026-07-03-theming-p4-custom-yaml-themes.md`.

## Theming P3 — theme picker + per-user persistence + live restyle (2026-07-03)

Shipped the switcher (`docs/THEMING_DESIGN.md` P3). **Backend**: `User.theme` (nullable `String(32)`;
NULL = boot default `latte-warm`), migration `0050_user_theme`, `theme` accepted by `PATCH
/auth/me` (validated against the bundled ids in `app/core/themes.py` → **422** on an unknown id) and
returned by `/auth/me`; `backend/openapi.json` regenerated. **Frontend**: a reactive theme store
(`lib/theme/store.ts`) — `activeThemeId`/`activeVizTheme` derived stores + `setTheme`/`initTheme`/
`reconcileTheme`/`setFollowSystem`; a data-driven **picker** in `ProfilePage.svelte` (grouped Light/
Dark, labelled Warm/Cool, colour swatches) that restyles live + persists via `updateProfile({theme})`.
**No-flash boot**: an inline `<head>` script in `index.html` sets `data-theme` + a cached surface
background before the module loads; `main.ts` calls `initTheme()` before mount; priority is
localStorage cache → server `theme` (reconciled in `App.svelte` after `/auth/me`) → default. **Live
restyle (no reload)**: the ECharts pages re-run `setOption` on `$activeVizTheme`; the Cytoscape
network re-applies its stylesheet + re-derives node colours in place (`restyle()`, no rebuild/
relayout). **Follow system** shipped (device-local, `prefers-color-scheme` + `matchMedia` listener,
picks the light/dark member of the current temperature pair). Verified: full backend suite **866
passed**; migration parity green; ruff clean; `make frontend-check` green (**149 tests**, 1 skip;
build OK). Handoff: `docs/agent_handoffs/2026-07-03-theming-p3-picker-persistence-live-restyle.md`.
Next: P4 (custom/hand-edited themes).

## Theming P2 — author + validate the 4 themes (2026-07-03)

Authored the four Catppuccin-based themes (`docs/THEMING_DESIGN.md`) as hand-editable YAML —
`latte-warm`, `latte-cool` (light) and `mocha-warm`, `mocha-cool` (dark), each warm/cool variant
sharing a hue set but differing in categorical ORDER + a faint surface undertone. P1's provisional
`default`/`default-dark` stubs are gone; the boot default is now **`latte-warm`**. The token schema
grew derived tints — `status-{success,warning,danger,info}-bg/-border` (badge/panel backgrounds),
`accent-note`/`-bg`/`-border` (the purple/indigo AI/semantic/role/tag family) — plus a `--muted`
alias of `--ink-muted`, and the `graph` block gained `grid`/`node_default`/`edge`/`warning_ring`/
`sequential`/`diverging`. **~186 hardcoded status/neutral colour literals across 23 Svelte
components were migrated onto role tokens; a grep confirms 0 `#`-hex colour literals remain**, so
switching themes recolours the whole GUI. The **Cytoscape network** (`CitationGraph.svelte`) now
reads node/edge/label/warning-ring colours + the categorical palette from the active theme's `graph`
block (via `resolveThemeById(data-theme)`), and the **ECharts** pages switched to `resolveThemeById`
so each theme's graph palette drives the charts. Each theme's `graph.categorical` was **validated
with `dataviz/scripts/validate_palette.js` against its own surface — all four PASS every check with
CVD ΔE 26.1 (light) / 16.4 (dark), above the ≥12 target** (raw Catppuccin accents fail; these are
validated derivatives). Sequential ramps pass the ordinal checks; text tokens meet WCAG AA
(≥4.74:1) on every surface and marks ≥3:1. A compact validator was ported into the repo
(`lib/theme/paletteCheck.ts`) and the theme tests run it per theme. `make frontend-check` green (141
tests pass, build OK; `prebuild` regenerates `themes.generated.ts`). Backend untouched. Handoff:
`docs/agent_handoffs/2026-07-03-theming-p2-four-themes.md`. Next: P3 (picker + persistence + live
restyle).

## Theming P1 — YAML→token pipeline + refactor (no visual change) (2026-07-03)

Built the substrate for the 4-theme system (`docs/THEMING_DESIGN.md`) with **zero visual change**.
Themes are now authored as hand-editable YAML under `frontend/themes/` (`default.yaml` ports the
current light look; `default-dark.yaml` preserves the pre-existing dark chart palette) and compiled
by `frontend/scripts/build-themes.mjs` (`npm run themes:build`, uses the `yaml` dep) into the
committed `frontend/src/lib/theme/themes.generated.ts`. The app/build/tests import that `.ts`, so
`npm ci`/`vite build`/vitest need no YAML at runtime — least-magic path chosen over a runtime YAML
import/Vite plugin. `lib/theme` emits role tokens (`--surface-*`, `--ink-*`, `--border-*`,
`--accent-*`, `--status-*`, `--radius-*`, `--font-family`) as a `[data-theme="<id>"]` CSS block;
`main.ts` injects the `default` theme and sets `<html data-theme="default">` before mount. The
ad-hoc `--pg-*` vars (App.svelte + Search/Library/Admin pages) were replaced by role tokens with
byte-identical values, and `lib/viz/theme.ts` now derives `VizTheme` from the theme objects' `graph`
section instead of a hardcoded palette. Byte-identical appearance is pinned by tests: the emitted
token→value map equals the pre-refactor literals, and `resolveTheme('light'|'dark')` equals the old
`VizTheme` constants. `make frontend-check` green (135 tests pass, build OK). The ~130 remaining
per-component hardcoded status/neutral colours are deferred (they can't collapse into the role set
without a value shift — P2 territory). Next: P2 (author + validate the 4 cozy themes).

## Everyday rename/reassign workflows — shelves, racks, tags, applied tags (2026-07-03)

Closed the user-facing gaps the E2E pass surfaced. **Shelf/rack rename** already had backend PATCH
endpoints emitting `shelf.modified`/`rack.modified` (D31.1) — only the UI was missing, so
`ShelvesPage`/`RacksPage` gained an inline rename control (librarian-gated, mirrors the existing
access/archive controls). **Tags** gained the missing mutations: `PATCH /tags/{id}` (contributor+,
re-derives `normalized_name`, 409 on a rename that would collide with another tag, emits
`tag.modified`) and `DELETE /tags/{id}` (editor+, deletes the tag and every `TagLink` so the tagged
papers/shelves/racks just lose the tag, emits `tag.deleted` with a `links_removed` count); the Tags
page got inline Edit (name/colour/description) and Delete controls. **Applied tags on a paper** are
now surfaced via `GET /works/{id}/tags` (SEE-guarded like `get_work`); `WorkDetail` lists them as
coloured chips each with its own remove control (the old blind apply/remove-by-select is gone). New
backend tests (`test_org_rename_and_tags.py`) cover rename shelf/rack/tag incl. role gates + audit
events, the tag-delete link cascade, and the SEE-safe applied-tags endpoint; E2E journeys 25–28 add
rename-shelf, rename-rack, tag rename+delete, and a paper losing a deleted tag. `openapi.json`
regenerated.

## Track C P5b — citation-graph depth (§8.9) + UMAP opt-in (2026-07-03)

The final workplan phase. **Part 1 — §8.9 citation-graph depth** (additive on the existing Cytoscape
graph, all modes preserved): `build_citation_graph` grew a `compute_metrics` gate (off for the viz
callers that only need edges/degree) that attaches per-node **weighted degree, PageRank** (pure-python
power iteration, weighted by mention count) **and exact Brandes betweenness** over the final
node/edge set. The Brandes impl moved into `citation_graph.py` and `citation_summary.py` now imports
it (one shared implementation). All three metrics ship on every node, so the frontend `size_by`
dropdown (degree/PageRank/betweenness) re-sizes the live graph **without a refetch or relayout**. A
`color_by` param (`none`/`shelf`/`tag`/`topic`/`status`) attaches one SEE-clamped categorical
`color_group` per local node (shelf coloring uses only non-private shelves so a private shelf name
never leaks); the frontend refetches on change and **re-colors in place** via a topology-signature
check (relayout only on a real topology change). A `warning` marker reuses the D31.4
`FileWorkLink.warning_state` + open-`DuplicateCandidate` signals → a red ring on flagged nodes; edge
width encodes mention count; an accessible (Okabe–Ito) legend maps color groups. New
**`GET /works/{id}/citation-neighborhood`** (`hops` 1–3, default 1; `node_mode`, `color_by`) returns
the local N-hop neighborhood of one focus paper as the same graph payload, SEE-clamped (404 unless
the caller may see the focus). **Part 2 — UMAP opt-in:** `embedding_cluster` gained a `layout` param
(`pca` default | `umap`); `umap-learn` is imported behind an `importlib` guard and degrades to PCA
with a note when absent, so the base api image (no umap) always renders. The layout cache is keyed by
`(scope, model, layout)`. `umap-learn` lives only in the opt-in **ml-extraction AI image**
(`backend/Dockerfile`), never in the base requirements/lock. Frontend gets a PCA/UMAP toggle that
surfaces the fallback hint. Full backend suite **852 passed** (+17); frontend **124 passed / 1
skipped** (+1, cytoscape stays a lazy chunk); ruff clean; `backend/openapi.json` regenerated (graph
`color_by` + node depth fields, neighborhood endpoint, viz `layout`). See
`docs/agent_handoffs/2026-07-03-track-c-p5b-graph-depth-umap.md`.

## Track C P5a — three more visualization views (2026-07-03)

Three more providers on the P2 viz seam (register-a-provider + register-a-renderer, no plumbing
change), all SEE-filtered and reusing the existing citation-graph resolution / P3 embedding source /
shared ECharts theme. **co_citation** — a node-link network over the scope with an `edge_context`
param: `coupling` (bibliographic coupling — two papers linked when they cite the same works, weight =
shared references, from the scope's `include_external` graph) or `co_citation` (two papers linked
when a third cites both, weight = shared citers, from the visible-library `local_only` graph; only
in-library citers are knowable, noted). Node size = degree, color reuses the shared status/work-type
helper. Rendered as an **ECharts `graph` (force) series** — chosen over a second Cytoscape path to
keep every view flowing through the one ECharts renderer registry the P2 scaffold established (node
`name` = work id, so the existing click-to-open handler works unchanged). **topic_river** — per-year
topic shares (reusing the embedding k-means clustering + TF-IDF labels from P3), an ECharts stacked
area / streamgraph; each year's shares sum to 1. **similarity_heatmap** — pairwise cosine matrix for
≤50 papers (top-N by recency past the cap, noted), reusing the P3 dense-vector source (hash-BOW
fallback), an ECharts heatmap, symmetric with a 1.0 diagonal. **VizPayload gained two backward-
compatible typed carriers** for non-scatter views (existing views leave them `None`): `series`
(`{years, topics:[{label, values}]}`) and `matrix` (`{labels, ids, values}`) — P5b/future charts
reuse these instead of overloading `notes`/`legend`. Full backend suite **835 passed** (+14);
frontend **123 passed / 1 skipped** (+17, echarts stays a lazy chunk); ruff clean; `backend/openapi.json`
regenerated (`edge_context` param + `series`/`matrix` on `VizPayloadResponse`). See
`docs/agent_handoffs/2026-07-03-track-c-p5a-more-viz-views.md`.

## Track C P4 — citation summaries (§8.11) (2026-07-03)

The README-headline analytics feature. A new `backend/app/services/citation_summary.py` computes six
scoped, SEE-filtered blocks over the same computed layer as the graphs/viz (reusing
`citation_graph._scope_works` + `access.visible_work_ids`): **most-cited local** (in-library works by
local in-degree, from `build_citation_graph`'s resolved local edges — never re-resolved),
**most-cited external** (scope works by `Work.citation_count`, P1), **frequently-cited-but-missing**
(unresolved references aggregated by normalized DOI/arXiv/title, ranked by citation frequency, each
carrying a representative `reference_id` for the `POST /works/from-reference` import path),
**bridge papers** (exact **Brandes betweenness centrality** on the undirected local citation graph,
method label `brandes_betweenness_undirected`, capped at `MAX_NODES=500`), **isolated papers** (scope
works with zero local links), and a **chronological distribution** (papers per publication year).
**Cache:** in-process dict keyed by a scope signature = sorted member work ids + max `updated_at` +
scope reference count + limit + schema version, returned as `version` with a `computed_at`; any
change to the scope's works/references changes the signature and recomputes (a persisted cache would
slot in at the same site). Read-only. The previously-empty `citations.py` router now serves
`GET /citations/summary` (auth + shelf/rack SEE-guard + saved-filter resolution, mirroring the viz
endpoint). **Frontend:** a new "Citation summary" tab (`src/pages/CitationSummaryPage.svelte`) surfaces
the six blocks (clickable works open the paper; missing works show an Import affordance) with a shared
ECharts year-distribution bar built by the pure, unit-tested `src/lib/viz/citationSummary.ts`;
`client.citationSummary` + types added. Full backend suite **821 passed** (+13); frontend
**106 passed / 1 skipped** (+3); ruff clean; `backend/openapi.json` regenerated (new endpoint +
`CitationSummaryResponse`). See `docs/agent_handoffs/2026-07-03-track-c-p4-citation-summaries.md`.

## Track C P3 — embedding-cluster map (PCA-2D) (2026-07-03)

Second visualization view on the P2 seam: an `embedding_cluster` provider
(`backend/app/services/visualization.py`) places the SEE-filtered scope papers in 2D by embedding
proximity. **Layout:** server-side **PCA-2D** (numpy SVD, mean-centered, sign-fixed for
determinism — no new dependency), the default from §2b/D3. **Vectors:** reused from the topic /
related-works path (`_paper_dense_vectors`) — a real model's stored vectors are never re-embedded on
the read path; un-indexed papers are skipped and surfaced as an "N papers not indexed — reindex"
note (D19). With only the hash-BOW baseline active it degrades by embedding each paper's text with
the baseline provider (dense, PCA-usable) plus an honest note, so the view still renders with the
default provider. **Coloring:** an embedding k-means `cluster` (reusing the topic modeller's
`_kmeans` + TF-IDF keyword labeller) → `color_group` = "N. keyword, keyword"; `size` = local degree
(shared metric helper). **Cache:** the computed layout is cached at the `_METRIC_CACHE_NOTE` site in
`_LAYOUT_CACHE` keyed by (scope signature, model) with a vector fingerprint that self-invalidates on
a vector change. Axes are the two fixed PCA components; `axis_options` omitted. **Frontend:**
`src/lib/viz/embeddingCluster.ts` reuses the P2 ECharts scatter shape (one series per cluster → a
cluster legend, hover = title + cluster, click → open paper), registered in the viz page (the axis /
color / edge controls hide for this view). Full backend suite **808 passed** (+11); frontend
**103 passed / 1 skipped** (+5); ruff clean; `backend/openapi.json` unchanged (view_type is a
free-form path param). See `docs/agent_handoffs/2026-07-03-track-c-p3-embedding-cluster-map.md`.

## Track C P2 — viz scaffold + temporal citation map (2026-07-03)

The architectural foundation for the D38 visualization module (P3-P5 build on this) plus the first
view. **Extensible seam:** `backend/app/services/visualization.py` is a provider registry —
`@register_viz("...")` + `get_viz(db, actor, view_type, scope, params) -> VizPayload`; adding a view
later is one server-side provider + one frontend renderer, no plumbing. `VizPayload` is normalized
(`view_type`, `nodes[{id,x,y,size,color_group,shape,label,meta}]`, `edges?`, `axes{x,y}`, `legend?`,
`notes`, `axis_options`). Scope + visibility are **reused** (`access.visible_work_ids` clamp +
`citation_graph._scope_works` resolver — a reader never gets a hidden paper); node cap `MAX_NODES=500`
with a truncation note. **`temporal_map` provider:** both axes independently selectable from a shared
set — `year`, `citation_count` (NULL→muted), `local_degree` (incoming-edge count reusing
`build_citation_graph`, always-on default Y), `citation_velocity`, `similarity_to_focus` (cosine via
`_paper_dense_vectors`), `topic_similarity_to_focus` (topic-term Jaccard); similarity axes return a
per-node `None` + note when no focus / no embedding model / no topics. Encodings: `size`
(local_degree/citation_count/none), `color_group` (status/work_type/none), `shape` reserved
(`in_library`), optional citation-edge overlay. **Endpoint:** `GET /api/v1/viz/{view_type}`
(+`GET /api/v1/viz/` list), auth + SEE-guard + saved_filter resolution like the graph endpoint;
`openapi.json` regenerated. **Frontend:** lazy ECharts (`echarts@^5.5.1`, separate 1 MB chunk — main
bundle stays ~357 kB), a `src/lib/viz/` view registry + shared Seaborn-like theme + temporal-scatter
renderer (pure `buildOption`, jsdom-testable), and a new **Visualizations** tab
(`VisualizationsPage.svelte`) with view/scope selectors, both axis dropdowns, size/color/focus
controls, edge toggle, click-to-open-paper, and `data-testid`s. FULL backend suite green (797 passed,
+19 viz tests); `ruff check/format` clean; frontend green (98 passed/1 skipped incl. 8 new + build). Both
similarity axes shipped (embedding infra reused cleanly); topic/shelf/tag coloring deferred to P3+.
See `docs/agent_handoffs/2026-07-03-track-c-p2-viz-scaffold-temporal-map.md`.

## Track C P1 — citation counts (D38 visualization prerequisite) (2026-07-03)

First slice of the D38 visualization module: fetch, store, expose and display an external citation
count per work. **Model + migration:** three nullable `Work` columns — `citation_count` (Integer),
`citation_count_source` (String(32)), `citation_count_fetched_at` (timestamptz) — via migration
`0049_work_citation_count` (chained off the `0048` head). **Fetch/parse
(`services/metadata_enrichment.py`):** `ExternalMetadata.citation_count` extracted per source
(Crossref `is-referenced-by-count`, OpenAlex `cited_by_count`, S2 `citationCount`; `citationCount`
added to the requested S2 fields), coerced by a `_as_int` guard so a missing field is `None` not
`0`. `enrich_work` snapshots the count from the highest-priority source that reported one —
**priority OpenAlex → Semantic Scholar → Crossref** (`CITATION_COUNT_PRIORITY`) — overwriting on
each run (newer wins) and recording source + `fetched_at`. Papers with no resolvable id stay NULL;
fail-open preserved (reads off the D8 per-source `metas`, so a raising connector never aborts the
rest and a source that returns no count leaves the prior snapshot untouched). **Expose:** `WorkRead`
gained the three fields; `openapi.json` regenerated. **Display:** `WorkDetail.svelte` shows
`Citations <n> via <source> · as of <date>` (locale-formatted) below Topics, with a graceful `—`
when NULL; the existing per-work Enrich action refreshes it (no new scheduler for P1). Full backend
suite green (778 passed); `make test-migrations` green (4 passed, parity + no-drift); `ruff
check/format` clean; `make frontend-check` green (WorkDetail.citations 2/2 + build). Parser +
`enrich_work` priority/fallback/NULL tests, a WorkRead-exposure API test, and frontend
render/graceful-dash tests added; fixtures carry realistic counts. See
`docs/agent_handoffs/2026-07-03-track-c-p1-citation-counts.md`.

## D31 spec-conformance — D31.4 search operators + D31.5 export formats/targets (2026-07-03)

Track B second batch (items 4–5 of D31). **D31.4 — additional search operators (§14.2):** extended
`services/search_query.py` (known-key set + `ParsedQuery` + parse dispatch) and
`api/v1/endpoints/works.py` `build_works_query` with the missing operators, all composing on top of
the SEE-visibility filter: `abstract:<text>` (abstract column), `summary:<text>` (a stored
`Summary.text`), `fulltext:<text>` (extracted body via `work_chunks.text`), `file:<name>` (a linked
`File.original_filename`), `has:grobid` (a `RawTeiDocument` for the work; `has:tei` alias),
`has:ocr` (a linked file with `text_layer_quality == "ocr_added"`), `duplicate:<yes|no>` (an OPEN
work-type `DuplicateCandidate`), `version:<yes|no>` (`version_group_id` set OR a `WorkVersion` row),
and `warning:<text|*>` (a `FileWorkLink.warning_state != "none"`; `*`/`any` = any warning, else a
substring match). All prior operators kept working. **D31.5 — export formats/targets (§8.13):**
added two renderers in `services/export_service.py` — `latex` (a `\cite{...}` line + a
`thebibliography` block, LaTeX-escaped) and `pandoc` (a `[@key; ...]` line + a Pandoc-Markdown
references list) — plus two export targets: `import_batch` (works by `Work.import_batch_id`) and
`missing_references` (unresolved `Reference` rows with no `resolved_work_id`, rendered as raw
reference strings). New formats surfaced in the frontend `ExportDialog` via `EXPORT_FORMATS` +
`ExportFormat`/`ExportScopeType` in `client.ts`. Full backend suite green (773 passed); parser +
endpoint tests per new operator and golden-output tests for both renderers + both targets added;
`ruff check/format` clean; `openapi.json` regenerated (new operators are doc-only, but the works
docstring changed); frontend typecheck + 88 tests green. See
`docs/agent_handoffs/2026-07-03-d31-4-5-search-operators-export-formats.md`.

## D31 spec-conformance — B1–B3 (audit wiring, summary provenance, annotation JSON) (2026-07-03)

Track B first batch (items 1–3 of D31). **B1 — audit-event wiring (§7.6):** emitted the events the
spec required but never fired — `shelf.created`/`shelf.modified`, `rack.created`/`rack.modified`,
`paper.metadata_edited` (manual metadata edit via `PATCH /works/{id}`), `annotation.created`, the RQ
job lifecycle `job.started`/`job.completed`/`job.failed` (via a `_audited_job` wrapper on every real
job in `workers/jobs.py`), and `backup.created`/`restore.completed` (a small `scripts/record_backup_event.py`
CLI wired into the Make backup/restore targets). Added an **append-only JSONL file sink** in
`services/audit.py` (best-effort/fail-open, append mode, path from the new `audit_log_path` setting,
default `./storage/audit/audit.jsonl` on the storage volume) that mirrors every DB event. Wired the
existing `/admin/audit-events` pagination into the Events admin view (prev/next + page indicator).
No existing count-asserting test broke (they all filter by `event_type`). **B2 — summary provenance
(§8.14.2):** migration `0048_summary_provenance` adds `provider_requested`, `provider_used`,
`fallback`, `source_sections`, `content_hash`, `created_by_user_id`, `params` to `Summary`; both
`summarize_work` and `summarize_scope` persist them and the read schema surfaces them. **B3 —
annotation JSON export (§8.8.7):** added `json` to the export format enum with a documented shape
(work, page, type, coordinates, selected_text, note, created_at, author). Full backend suite green;
`make test-migrations` (Postgres parity) green; `ruff check/format` clean; `openapi.json` regenerated.
Note: there is **no annotation *edit* endpoint** (only create + delete), so `annotation.edited` has
no wiring site — recorded as a deviation. See
`docs/agent_handoffs/2026-07-03-d31-b1-b3-audit-summary-annotation.md`.

## Frontend + Infra audit batch — D16, D17, D29, D2, D5, D24, D4 (2026-07-03)

Seven frontend/infra audit fixes; stack left healthy (api healthy, worker up with 2 RQ children).
**D16** replaces serial `await`-in-a-loop batch library actions with a chunked
`Promise.allSettled` helper (concurrency 6) that surfaces per-item failures in the status message and
keeps a single final refresh (`batchDelete/batchReextract/batchSetStatus/batchPutInto`). **D17** stops
the Cytoscape graph rebuilding+relaying-out on every display toggle: filters now show/hide elements on
the live instance (`applyFilters()`), edges carry stable ids, and rebuild+layout runs only on a data/
render-surface change (layout is laid out on the visible subset; explicit re-layout stays on the layout
select). **D29** verified all seven frontend majors (Vite 8, TS 6, pdfjs 6, vitest 4, jsdom 29,
vite-plugin-svelte 7, svelte 5) are stable/GA as of mid-2026 → **no version changes**. **D2** adds a
tuned CSP + `X-Frame-Options DENY` / `X-Content-Type-Options nosniff` / `Referrer-Policy no-referrer` to
`frontend/nginx.conf`, smoke-tested by serving the built bundle through nginx (document/JS/CSS/SPA route
all 200 with headers; `connect-src` relaxed to `http:/https:` because the API is a separate origin).
**D5** makes `make init` generate a random `POSTGRES_PASSWORD` (placeholder `change_me_generated_on_init`
in `.env.example`; existing `.env` untouched). **D24** compiles a hash-pinned `backend/requirements.lock`
(65 pkgs) installed via `--require-hashes` in both Dockerfile stages, and bumps httpx2 2.4.0→2.5.0; the
lock matched the already-installed known-good versions. **D4** runs api/worker/agent as `appuser` and the
frontend dev server as `node` (both UID 1000) via a root entrypoint that chowns the root-owned managed-
library / node_modules volumes then drops privileges with gosu; verified health 200, storage writable,
worker supervisor spawns. Verified: `make frontend-check` green (88 passed), full backend suite **749
passed**, no Python touched (ruff N/A). See
`docs/agent_handoffs/2026-07-03-frontend-infra-audit-batch.md`.

## Track A performance batch — D13, D14, D15, D19, D20, D22 (2026-07-03)

Six performance audit fixes, all degrading gracefully with Redis/Ollama down; the SQLite-without-Redis
unit suite stays deterministic. **D13 (HIGH)** takes the BM25F+ lexical rebuild off the search read
path: on Postgres a search now serves the persisted/last-known index and, when the corpus signature
moved, enqueues a coalesced background rebuild job (`rebuild_bm25_job`, fixed id `bm25-rebuild`) and
serves the slightly-stale index meanwhile — a search right after an edit may transiently miss that
edit until the worker refreshes the on-disk copy; only a genuine cold start builds synchronously.
`build_index` now reads body text from the materialized `work_chunks` rows (one bulk SELECT) instead
of re-parsing each work's TEI XML; title/abstract come from the work row so an un-chunked paper is
still indexed and title/abstract chunks aren't double-counted. SQLite builds synchronously (no Redis).
**D14** adds `embed_many()` (Ollama `/api/embed` batch input; sentence-transformers `.encode(list)`;
hash-BOW list-comp) used by both backfills so activating a real model is one round-trip per batch, not
20 k sequential calls; `POST /search/reindex` is routed to the queued reindex job, falling back to a
synchronous in-request build when the queue is unavailable (response gains `queued`/`job_id`). **D15**
forces full-library duplicate scans onto the worker regardless of the `background` flag (queued shape;
503 when the queue is down); single-work/-file scans stay synchronous. **D19** keeps topic views
read-only: `_paper_dense_vectors` no longer embeds un-indexed papers inline — papers without a
pre-indexed chunk vector for a column model are skipped and counted, surfaced as
`unindexed_work_count` (topics) / `summary.unindexed_works` + note (graph). **D20** replaces the
topic-graph O(n²) pure-Python cosine with a single numpy op (normalize once, `M @ M.T`, threshold);
edges are identical to the old loop (stable sort tie-break, round(4)). **D22** commits HNSW
provisioning (ALTER TABLE ADD COLUMN + CREATE INDEX) in its own short transaction *before* the reindex
backfill loop, so the DDL locks aren't held for the whole job. Verified: full backend suite **749
passed**, ruff clean (backend + agent), `openapi.json` regenerated (D14 reindex `queued`/`job_id`,
D15 scan description, D19 `unindexed_work_count`). See
`docs/agent_handoffs/2026-07-03-track-a-performance-batch.md`.

## Track A audit batch — D6, D8, D9, D10, D11, D12 (2026-07-03)

Six correctness/security audit fixes, each fail-open on optional external services. **D6** SSRF-guards
the admin-set `ollama_url`: `ai_config._validate` now rejects a URL that isn't http(s) to a loopback
IP / `localhost` / bare docker-service name unless `ALLOW_EXTERNAL_OLLAMA=true` (reuses find-on-web's
host classification style). **D8** makes `enrich_work` per-source resilient — each source is queried
in its own try/except, a failure is recorded in the new `failed` list (also surfaced as the
`enrich_work_job` return value) and the remaining sources still run; chained chunk/embed enqueue is
untouched. **D9 (contract change)** reworks folder import (`storage.import_server_folder`): the batch
row is committed up front in its own txn, each file imports inside a SAVEPOINT so one bad file only
rolls back itself (counted in the new `stats["errors"]`), and status/stats finalize in one commit at
the end — **partial imports are now visible** (a scan with some unreadable files still commits the good
files instead of rolling the whole batch back). The owed-extraction marking (D7) moved into the same
final commit; the endpoint response shape is unchanged (`stats` gained an `errors` key). **D10** gates
the RQ worker supervisor on `alembic current == head` via `wait_for_migrations()` (bounded wait, fails
open after `PARACORD_MIGRATION_WAIT_TIMEOUT`s so a misconfigured DB starts anyway rather than wedging).
**D11** adds `default_shelf.backfill_loose_papers_onto_default`, run idempotently in the FastAPI
lifespan, so any loose paper is placed on the default shelf on startup (safe across API workers,
no-ops on an empty/at-head DB). **D12** makes multimode topic clustering skip a model whose per-work
embedding dimension doesn't match its registered/expected dim (logged warning) instead of padding —
if every model is skipped it falls back to the TF-IDF baseline. Verified: full backend suite **738
passed**, ruff clean (backend + agent), `openapi.json` unchanged (no API surface change).
See `docs/agent_handoffs/2026-07-03-track-a-audit-batch.md`.

## D35 + D37 — drop dead ML-extraction seam; pgvector on by default (2026-07-02)

Two decided audit items sharing the backend config. **D35** removes the dead ML-extraction seam:
the `full_ml` OCR backend and the `extraction_backend`/Nougat/Marker flags never had a real
extractor (GROBID was always the structured extractor; PyMuPDF is the shipped hard extractor and is
now its own `pymupdf` OCR backend). Removed the `extraction_backend` Setting + the
`advanced_extraction` YAML mapping, dropped `full_ml` from `OCR_BACKENDS` (now
`none|ocrmypdf|pymupdf`), removed the `full_ml` route + `run_ml_extraction`/`ml_extraction_available`
in `ocr.py`, and pruned the `nougat`/`marker`/`full_ml` entries from `detect_providers`. A legacy
row still holding `ocr_backend="full_ml"` degrades to the `Settings` default on read
(`get_ai_config` tolerates any out-of-range value), and migration `0047` rewrites the stored value
to NULL. Frontend: the AI-settings OCR card no longer offers `full_ml` or the ML-extraction install
banner. **D37** flips `pgvector_enabled` default False→True: a registered real embedding model gets
sub-linear HNSW ANN search out of the box, while the default `hash_bow` (and SQLite / no-pgvector)
transparently falls back to the JSON + Python-cosine path. The flag is a no-op off Postgres, so the
SQLite suite is unaffected; a defensive `_pgvector_rank` empty-result→None guard makes an
un-backfilled `vector_pg` column fall back instead of returning a spurious empty result. Verified:
full backend suite **720 passed**, migration parity **4 passed**, frontend green + build, ruff clean,
`openapi.json` unchanged.

## D39 — queue-length cap + admin queue/worker controls (2026-07-02)

Added a pending-queue depth cap plus admin recovery controls, extending D1's overload protection.
(1) A new `max_queue_len` knob on the `app_config` singleton (default 1000, migration
`0046_max_queue_len`, admin-editable from the Settings tab). (2) A fail-open capacity guard
(`services/queue_capacity.assert_queue_has_capacity` + `queue.pending_queue_depth`) runs at the
start of every job-creating request (folder/upload/identifier/BibTeX/RIS/CSL import, file &
work extract/re-extract, agent extract/teleport push, `/search/reindex`); it rejects with 429 when
the pending RQ queue is already at the cap and **allows** the request when the depth can't be
measured (Redis unreachable) — a dropped enqueue is already surfaced by D7's `extraction_queued`.
The unit suite forces the fail-open path via an autouse conftest fixture. (3) Admin-only
`POST /jobs/clear-queue` (empties the pending queue) and `POST /jobs/reset-workers` (requeues jobs
stranded in the StartedJobRegistry, clears the FailedJobRegistry) recover a stuck queue; both record
an audit event and degrade gracefully (never 500) when Redis is down. Since the API can't restart
the worker *processes* (they run under the supervisor in the worker container), the reset response
notes a full reset is `docker compose restart worker`. Frontend: an app-wide "queue full" toast
(triggered from the client request wrapper on a 429/503 "queue is full" detail) plus admin-only
Clear queue / Reset workers buttons on the Jobs tab. Verified: full backend suite 722 passed,
migration parity 4 passed, agent 34 passed, frontend green + build; ruff clean.

## D1 — overload protection + shared throttle (2026-07-02)

Added shared, fail-open overload protection across four slices. (1) The login throttle
(`login_throttle.py`) moved from a per-process dict to a Redis sliding-window sorted set keyed by
username, shared across API workers; it falls back to the in-process dict when Redis is unreachable
(fail-open, never fail-closed) and keeps the injectable clock + public API. (2) A new
`services/rate_limit.py` + ASGI middleware enforces per-client (bearer-token, else IP) and global
requests-per-minute ceilings via a Redis fixed-window counter, returning 429 + `Retry-After`;
it fails open when Redis is down and exempts health/docs. (3) A `max_batch_items` cap (default 100)
rejects oversized client import batches (BibTeX/RIS/CSL, agent manifest, citation batch) with 413;
server-folder scans are exempt and the local agent chunks oversized scans (reads the cap from
`/agents/me`). (4) The worker container now runs a supervisor (`app/workers/supervisor.py`) that
launches the owner-configured `rq_worker_count` (default 2, read once at start — restart to apply;
falls back to the default if the DB is unreachable). All three new knobs plus the rate limits live
on the `app_config` singleton (migrations `0043`–`0045`), are admin-editable from the Settings tab,
and go through the existing app-config PATCH. Verified: backend fast tier green, migration parity
4 passed, agent tests green (+chunking), frontend 82 passed + build; ruff clean.

## D7 — extraction-enqueue visibility + self-healing recovery (2026-07-02)

Fixed the silent-drop of extraction jobs when Redis is down at import time. (1) Every import/extract
path now captures the `enqueue_extraction` result and surfaces `extraction_queued: bool` in its
response (`ImportBatchRead`, `WorkFileRead`, `IdentifierImportResponse`, agent extract/teleport
responses); the frontend shows a "retry automatically" warning when false. (2) The Jobs tab has a
red/yellow/green queue-health semaphore backed by new `redis_reachable`/`worker_count`/`queued`
fields on the jobs endpoint (degrades without a 500 when Redis is down). (3) A durable
`File.extraction_requested_at` marker (migration `0042_file_extraction_owed`) is set in the same
commit that queues extraction and cleared by the worker on terminal success **or** failure; a
startup lifespan sweep (+ admin `POST /jobs/reprocess-pending`) re-enqueues anything still owed.
Extraction now uses the deterministic RQ job id `extract-{file_id}` (dash, not colon — RQ 2.x
rejects colons) so a re-enqueue of an in-flight file is a no-op. Agent keeps items retryable
(`extract_queue_failed`) when the server couldn't queue. Verified: backend fast tier 519 passed,
migration parity 4 passed, agent 33 passed, frontend 81 passed + build.

## Full audit + consolidated decisions + auto-fixes (2026-07-02)

A six-pass audit (security, efficiency, stability, tech-stack suitability, plus verification of
every open item in the old followup/needs-discussion docs) was consolidated — after a second
round of doc-merging — into the **AUDIT / DISCUSSIONS / ARCHIVED_AUDIT_LOG** triad above, which
supersedes `docs/DECISIONS.md`, `FOLLOWUP.md`, `docs/NEEDS_DISCUSSION.md`, and the old
1 100-line `docs/AUDIT.md` (all preserved in `docs/archive/audit_docs_pre-2026-07-02.zip`).
~30 unambiguous fixes were applied and committed in this pass (agent file perms + GUI XSS,
import-batch IDOR, RQ 900 s job timeout, extraction-failure rollback discipline, transactional
`make restore`, batched hot-path queries, HTTP-client + embedding-provider caching,
default-shelf hooks for the last creation paths, derived-OCR-copy cleanup, 4 dead deps removed,
and more — full list in `ARCHIVED_AUDIT_LOG.md` §2026-07-02). Verified with `make test-full`
(660 backend + 32 agent green) and `make frontend-check` (75 green + build). `httpx2` was
verified online as the legitimate Pydantic-maintained httpx fork. Open items await owner
decisions in `AUDIT.md` + `DISCUSSIONS.md`, each with a recommendation.

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

## Library pagination + shelves/racks columns (2026-07-02)

D18 + D32 implemented. `GET /api/v1/works` now returns a `PaginatedWorks` envelope
(`items/total/page/pages/per_page`): effective page size = per-request override, else the new
per-user `papers_per_page` profile field, else the server default, clamped to an owner/admin-editable
global maximum (`AppConfig` singleton + `GET/PATCH /admin/app-config`). Each row also carries its
SEE-filtered `shelves`/`racks` (D32), batch-loaded in O(1) queries per page. Frontend: Library
pagination controls (prev/next, page dropdown, go-to-page), opt-in Shelves/Racks columns, a Profile
"Papers per page" field, and an Admin "Settings" tab for the global max. Verified: fast backend tier
508 passed + migration parity 4 passed (Postgres); frontend 78 passed + build green. See
`docs/agent_handoffs/2026-07-02-library-pagination-and-columns.md`.

## Current status

**Milestones 0–7 have an implemented vertical for every acceptance contract (all
`test_future_milestones.py` tests enabled and green). Much of M3–M7 is backend-complete but
under-exposed in the single-page UI, and several "AI" features are deliberate lightweight
stand-ins — see `docs/ARCHIVED_AUDIT_LOG.md` for how those gaps were closed and
`docs/DISCUSSIONS.md` for what to build next.**

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

> The authoritative lists now live in **`docs/AUDIT.md`** (current issues) and
> **`docs/DISCUSSIONS.md`** (open choices); the historical findings below are archived in
> **`docs/ARCHIVED_AUDIT_LOG.md`**. The items below are kept as quick pointers only.

**Top-priority from the 2026-06-25 audit (see ARCHIVED_AUDIT_LOG.md for detail):**
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
- D36: Playwright E2E is wired into CI (new `e2e` job in `.github/workflows/ci.yml`) and the suite expanded to 19 journeys — added pagination, annotate, export, duplicates-review, admin-settings, jobs-health, and (network-gated) identifier-import. Fixed the E2E helpers for the D18 paginated `/works` envelope and made E2E provisioning raise the D1 rate limits so the browser suite isn't throttled. Local: 17 passed / 2 skipped (GROBID + online-only). See `docs/agent_handoffs/2026-07-02-d36-e2e-journeys-and-ci.md` (notes a ProfilePage "Papers per page" number-field bug found but left for follow-up).
- Batch C citation-summary enrichments (2026-07-07): the Citation-summary tab gained (C2) an open-in-paper-view icon on every internal item (reuses the Search-tab `WorkDetail` modal via `getWork`); (C1) an on-demand external-reference preview — `GET /citations/external-preview` composes the identifier-based enrichment connectors (arXiv/Crossref/OpenAlex/Semantic Scholar), identifier-only egress, short in-process TTL cache, graceful "no preview available" on no-id/failure; (C3a) a per-user import/ignore worklist keyed by the stable normalized missing-work key (new `missing_work_decisions` table, migration 0052) with `GET/PUT/DELETE /citations/worklist`, surviving recompute, ignored items collapsing; (C3b) BibTeX/CSV export of the frequently-cited-but-missing list (`GET /citations/missing-export`); (C3c) a headline library-coverage metric (`coverage_held/total/pct` = held vs held+external resolvable cited works). FULL backend suite 909 passed; `make test-migrations` 4 passed (autogenerate no-drift); frontend 188 passed / 1 skipped; openapi regenerated. See `docs/agent_handoffs/2026-07-07-batch-c-citation-summary-enrichments.md`.
- E2E hardening (2026-07-03): expanded to 25 journeys — added rack lifecycle (add/remove shelves + delete keeping shelves), shelf organisation (multi-shelf + reassign + never-loose invariant), tags (create/apply/remove), the Profile-form "Papers per page" save (the d36 follow-up — now verified working), and visualizations + citation-summary rendering. Fixed a real UX bug: the Duplicates page fetched candidates before the queued full-library scan (D15) finished, so "Scan now" showed 0 — it now polls the scan job to completion before reloading. Added an endpoint-level reader-SEE test for `/works/{id}/citation-neighborhood`. Local: 23 passed / 2 skipped (GROBID + online-only), 0 failed. See `docs/agent_handoffs/2026-07-03-e2e-hardening-and-async-ux-fixes.md`.
- Batch S — deeper safety/attack/web-stability battery (2026-07-07): added a `@safety`-marked adversarial suite under `backend/tests/safety/` (10 files, 158 tests) run by a NEW `make test-safety` target and deselected from BOTH `make test` (`-m "not slow and not safety"`) and `make test-full`/CI (`-m "not safety"`). Coverage: AuthZ/IDOR fuzzing across the newer viz/citation/neighborhood surfaces + per-user worklist/import-batch isolation + mass-assignment; role-ladder / admin-can't-manage-admins/owner privilege escalation; rate-limit + login-throttle + queue-cap under burst & concurrency (trip + recover, no bypass); SSRF (internal/loopback/link-local/metadata IPs, bad schemes, shadow-library hosts, cross-host redirects, admin `ollama_url`); path traversal (`../`/absolute/symlink escapes on the file resolver + managed-stream endpoint); upload abuse (413/400 for oversized/non-PDF/malformed/zero-byte/bomb-style); XXE (external-entity + billion-laughs safe at lxml 6.1.1); SQL-injection (search-query allowlist, sort allowlist, `vec_*` slug allowlist); auth-token/session opacity + revoked/expired rejection + agent-token/enrollment abuse; nginx CSP/security-header assertion (skips cleanly — nginx config lives in the frontend image, not the API container). **No real holes found — every existing guard held**; the battery is regression coverage. Sole observation (by-design, not a hole): `Agent.revoked_at` is dead code — agent revocation is via `status != "approved"` or `delete_agent`, both of which correctly 401 a stale token (asserted). `make test-safety` → 157 passed / 1 skipped; full core suite unchanged at 909 passed / 158 deselected. See `docs/agent_handoffs/2026-07-07-batch-s-safety-battery.md`.

- Batch D — duplicate-resolution overhaul (2026-07-07): reworked Merge/Link so nothing is left half-empty. **Model**: `Work.merged_into_id` (self-FK, SET NULL) marks a hidden "shadow"; `Work.merge_record` (JSONB, none-as-null) stores the single-level reversal record on the shadow; new `work_links` table backs the Link relationship (migration `0053_work_merge_shadow`, applied to the live DB). **Merge** = true consolidation into the user-chosen base: fills the base's empty fields (title/abstract/year/venue) with a provenance assertion, records differing values as reusable metadata `conflict` assertions (never overwrites, respects locked fields), *transfers* the unique-indexed identifiers (doi/arXiv — cleared on the shadow so `uq_works_doi`/`uq_works_arxiv_base_id` can't collide), moves every owned entity (file links, shelf/rack memberships, tags, outgoing refs+mentions, annotations, versions, non-field assertions), and redirects INCOMING references via a reusable `redirect_references(source→base)` helper. The source becomes a hidden shadow excluded from EVERY work-returning path (library/search lexical+semantic+chunk+bm25/graph/all viz/topic/export/related/reading-queue/shelf-works/annotation-search) — clamped in `access.visible_works_query` + `_visible_work_condition` and at each raw `select(Work)` source (admin's `None` sentinel leaked otherwise). **Unmerge** (`POST /works/{id}/unmerge`) reverses exactly the most recent merge from the record. **Flatten-on-re-merge**: merging into an already-merged base finalizes the prior shadow (drops its record, keeps it hidden) so unmerge is always single-level. **Link** (`link_as_version` action) now records a bidirectional `work_links` row — both papers + files kept, nothing moved/hidden; shown in a new `/works/{id}/related-links` endpoint + "Linked papers" detail section. Everything is one transaction per action. **UI**: Duplicates page now shows a base/merge-from pair with a ⇄ swap control, a live merge preview ("fills N fields, adds M conflicts, moves K files, hides the other"), renamed Merge/Link; WorkDetail gains an Unmerge button (when `has_reversible_shadow`) + Linked-papers section. FULL backend suite green (1079 passed / 1 skipped); `make test-migrations` 4 passed (autogenerate no-drift); frontend 194 passed / 1 skipped; new backend `test_duplicate_merge.py` (14) + updated `test_duplicates_api.py`; new `DuplicatesPage.test.ts` (4) + `WorkDetail.merge.test.ts` (2); openapi regenerated; live-Postgres merge→unmerge smoke verified (identifier transfer + redirect + hide + restore). See `docs/agent_handoffs/2026-07-07-batch-d-duplicate-resolution-overhaul.md`.
- CI hotfix (2026-07-07): the `features-2026-07-07` push went CI-red on one flaky safety test, `backend/tests/safety/test_safety_rate_and_throttle.py::test_queue_cap_not_bypassed_by_concurrency`. Root cause: under true parallelism the shared in-memory SQLite connection raises `sqlite3.InterfaceError: bad parameter or other API misuse` (auth-session/capacity-check query) which the TestClient (`raise_server_exceptions=True`) re-raises out of `pool.map`, so the assertion never ran — a pure test-harness artifact (production is Postgres), not a product defect. Fix: `submit()` now catches the harness exception and records it as a 5xx rejection, and the test asserts the actual security invariant — **no concurrent request slips past a full queue** (no 2xx; every request rejected; any request handled cleanly (4xx) is the cap 429) — instead of the incidental `429 in statuses`. Verified: target test 10/10 stable, full safety suite 157 passed twice, full bare `pytest` (CI mirror) 1080 passed / 1 skipped. **Gate-gap lesson:** `make test-full` excludes the `@safety` battery but CI runs bare `pytest` which includes it — mirror CI with bare `pytest` (or `test-full` + `test-safety`) before pushing.

- `ready-full` warning cleanup (2026-07-07): triaged every warning/notice in a clean `make ready-full`
  (all suites were already green) and fixed the substantive ones at the root — see
  `docs/READY_FULL_WARNINGS_ASSESSMENT.md` (working doc, intentionally uncommitted). **W1 (real):**
  `npm audit` flagged echarts `<6.1.0` moderate XSS (GHSA-fgmj-fm8m-jvvx); our charts render imported
  paper metadata, a plausible cross-user vector under the few-LAN-users assumption, so bumped
  `echarts` 5.5→6.1 (`0 vulnerabilities`). The API surface we use (init/setOption/resize/on) is
  unchanged; verified every viz view renders a live `<canvas>` with zero console errors + E2E viz
  journeys + 194 unit tests. **W2 (pgvector, proper fix not suppression):** the migration-parity test
  emitted `SAWarning: Did not recognize type 'vector'` ×6 when reflecting the raw pgvector columns
  (`embeddings.vector_pg`, `work_chunks.vec_*`, kept off the ORM by design). Added `pgvector` as a
  runtime dep and `import pgvector.sqlalchemy` in `app/db/base.py`, which registers the `vector` type
  in the PG dialect's `ischema_names` so reflection recognizes it — reflection-only; the app still
  reads vectors via raw SQL and we deliberately do NOT register the psycopg adapter, so that path is
  untouched (verified: parity 4 passed/0 warnings + 105 vector-path tests green). **W3 (strengthened,
  not hidden):** the jsdom `Not implemented: navigation to another Document` log came from the C3b
  export test clicking a real `<a download>`; the test now captures the click and positively asserts
  the user gets the server-named file per format (.bib/.csv) with a blob created+revoked each time —
  the log is gone as a side effect of a better assertion. **W8:** deduped `frontend config` in the
  Makefile `PY_PATHS`. **Skipped by owner decision:** W4/W5 (npm update-notifier/funding notices),
  W6 (Vite >500 kB chunk — only echarts, already split+lazy; no clean per-chunk exemption, so the
  500 kB signal is kept for everything), W7 (the by-design "node_modules stale — restoring baked"
  self-heal message). Commits: `bf74e03` (pgvector), `6b19748` (export test), `974fe36` (Makefile),
  `abdf368` (echarts). api/worker + frontend images rebuilt so the baked copies carry the new deps.

- issue_batch_6 "all clear" items (2026-07-07): triaged `issue_batch_6.md` into all-clear vs
  needs-discussion (`docs/WORKPLAN_2026-07-07_batch6.md`; both intentionally uncommitted working
  docs) and implemented the six unambiguous ones. **2a** — `reindex_status` reported the impossible
  "7 / 3 papers indexed" because it counted raw `Embedding` rows (incl. stale rows for deleted/merged
  papers) against a total of current works; now both aggregates run over the same population (text,
  non-shadow) and `indexed` counts distinct joined works, so `indexed ≤ total`. **1e** — reworded the
  similarity / topic-similarity axis-unavailable notes to actionable paper-facing text (pick a focus
  paper / run topic modeling / reindex) instead of the raw axis key. **1g** — the visualization
  view-type selector now orders by a renderer `order` hint so the temporal map leads and is the
  default (was alphabetical → co-citation first). **1b** — the temporal-map year axis renders whole
  years (2019, 2020) via `minInterval:1` + integer formatter, not ECharts' default fractional,
  thousands-separated ticks (2,019 / 2,019.2). **3** — agent bulk **Prune** now prunes only the
  unwatched rows among a selection (watched files kept; Forget/unwatch first) via
  `agent_ops.prune_selected`, toast "kept N watched"; **Forget** still removes all selected. **7** —
  the library toolbar gained a **Refresh** button that re-fetches the current view + counts (keeps
  page/filters) so agent-pushed papers appear without a full browser reload. Each shipped with a
  test; commits `081852c` (2a), `4e0d977` (1e), `e6f970d` (1g+1b), `4ccef25` (3), `d24aae7` (7). The
  remaining seven issues (1a viz help, 1c reindex-vs-no-PDF messaging, 1d default citation edges, 1f
  overlap jitter/hover, 2b lexical-index staleness, 4 Scan&push server-entry semantics + agent help
  tab, 5 weighted per-paper reference graph, 6 stored per-paper AI summary) are deferred to owner
  discussion (workplan §B). See `docs/agent_handoffs/2026-07-07-issue-batch-6-all-clear.md`.

- issue_batch_6 "needs discussion" build-out (2026-07-07/08): after validating all §B decisions with
  the owner (recorded in `docs/WORKPLAN_2026-07-07_batch6.md`, plus design docs
  `docs/PAPER_REFERENCE_GRAPH_DESIGN.md` and `docs/B6_SPEC_index_only_stub_and_agent_help.md` — all
  three intentionally uncommitted working docs), implemented all eight. **B8** — a Summarise/
  Regenerate action in the paper view over the existing per-work summary machinery; new
  `summary_type='auto'` resolves server-side to the configured provider (`02a73a2`). **B5** — the
  lexical-index status self-refreshes against the current corpus (`cache_info(db)` + a `stale` flag)
  and a manual `POST /ai/lexical-rebuild` + "Rebuild index" button, fixing the "warm — 1 papers"
  staleness (`ce6d149`). **B2** — the viz "not indexed" notice now splits into reindexable vs
  needs-a-PDF (no chunks) via a structured `reindex_hint`, listing the file-less papers to open
  (`40677b7`). **B3** — temporal-map citation edges default ON with a configurable per-graph edge
  limit that suppresses + notes above the threshold (`71e95b9`). **B4** — overlapping temporal-map
  points collapse into one count-badged marker with an enterable tooltip listing each paper + open
  links (`582b5d8`). **B1** — visualization help: per-view description, an "About this view" popup,
  and a top-right "Visualization types" overview with requirements, from a data module `vizHelp.ts`
  (`04f50aa`). **B6** — `index_only` "Scan & push" now creates a promotable server paper stub
  (`AgentFile.work_id`, migration 0054; extract/teleport enrich it; deleting it drops the agent file
  so Reconcile un-indexes locally) gated by an agent "create library stubs" toggle; plus an agent
  Help tab and a "not extracted" library badge (`4832fd4`, `3be5015`, `0ab853d`). **B7** — a per-paper
  weighted reference graph: `GET /works/{id}/reference-graph` (section-classifier + local/external
  split + per-section mention counts), a paper-view modal renderer (year × section-weighted
  citations, base highlighted, no-year lane, base→ref star + optional local ref→ref edges), and
  editable section weights in Profile applied client-side (`8d4b785`, `d7fd2d5`). Every item shipped
  with tests; gate run after each + a final full gate. Migration 0054 applied to the live DB;
  api/worker/frontend images rebuilt (pgvector + echarts 6 baked from the earlier round).
  See `docs/agent_handoffs/2026-07-08-issue-batch-6-needs-discussion-buildout.md`.
- B7 extended — **selectable Y axis** for the reference graph (2026-07-08): the endpoint now emits per
  local node `citation_count`, `local_degree`, and `topic_similarity` to the base paper, and the modal
  has a Y-axis picker — weighted mentions (default), mention count, citation count, topic similarity,
  local citation degree. Local-only axes park external/unresolved nodes on a labelled "n/a" lane drawn
  with a dashed outline (not a zero) + a count note; X stays year (`62b8071`). Also fixed a recurring
  local-dev flake: `pdfjs-dist` was optimized on-demand so the reader E2E journeys intermittently 504'd
  under the parallel run — added it to Vite `optimizeDeps.include` alongside the chart libs (`b24e2bc`).
  Final full gate green: bare `pytest` 1093 / frontend 209 / e2e 32.

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

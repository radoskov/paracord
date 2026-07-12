# Workplan — Batch 12 (2026-07-11)

Reference→library matching ("likely local" citations) + related reference-graph fixes.
Owner decisions on the v1 discussion points are **locked** below; the one remaining open fork is
**D6 (canonical-reference refactor sequencing)**.

## Owner ask (recap)

References extracted from a paper's bibliography rarely link to library papers even when the cited
paper is genuinely local, because titles differ by punctuation/dash/case ("KnowRob: A knowledge
processing infrastructure…" locally vs. "KnowRob – A Knowledge Processing Infrastructure…" as
cited). The reference shows as "external" and its **Import** creates a near-duplicate `Work`. Add a
tolerant title(+year+author) matcher that surfaces **"likely local"** candidates the owner can
confirm/reject, so the tedious import→extract→scan-dupes→resolve loop mostly disappears.

---

## Decisions locked (owner, 2026-07-11)

**Matching pipeline & gates**
- **D1 — similarity function:** use `similarity_pct` (`normalization.py:34-56`, token-set + ratio,
  0-100, already the "% match" convention in the metadata-conflict UI). Verified to score the
  KnowRob pair **98.0** already.
- **D2 — identifier is the authoritative gate:** if **both** the reference and a candidate work
  carry a DOI (or arXiv id), they must be **identical after normalization** → match; if they differ
  → **not a match** (skip that candidate entirely, don't fall through to fuzzy). Only when
  identifiers are absent on one/both sides does the fuzzy title(+year+author) pipeline run. Rationale
  (owner): an occasional workshop-vs-journal false-positive from the same team/year is far less
  annoying than the current mass of false "external/missing" flags for papers already in the library.
- **Year gate:** when **both** years are present they must be equal; otherwise year is ignored.
- **No hardcoded 99% auto-tier.** Replaced by the `fuzzy_as_confirmed` toggle (below): a single
  configurable title threshold decides *candidacy*; the toggle decides whether a candidate is a
  **hard** link or a **soft** suggestion.

**Config & control (owner items #1, #2)**
- **#1 — "use fuzzy as confirmed" runtime toggle** in the Admin settings tab. OFF (default): fuzzy
  candidates are **soft** `likely_match` suggestions needing one click. ON: a fuzzy candidate ≥
  threshold (gates passed) becomes a **hard** link (`resolved_work_id` set) and is included in **all**
  graph/metric calculations (ref→ref edges, citation degree, topic similarity). **Home: fold a
  `use_fuzzy_match_as_confirmed: bool` into the existing `AppConfig` DB singleton** — it already has
  the exact machinery: model `backend/app/models/app_config.py:42` (single owner-editable row),
  overlay service `services/app_config.py` (`effective_*`/`update_*`, DB row wins over `Settings`
  default), admin endpoint `PATCH /app-config` (`admin.py:296`, audit-logged), and an AdminPage
  **"Settings" sub-tab** (`AdminPage.svelte:79`, `saveAppConfig()` `:329`). Lighter-touch than a new
  `WebFindSettings`-style table; the runtime matcher reads it via `effective_use_fuzzy_..._as_confirmed(db)`.
- **#2 — thresholds/params in YAML:** add a `reference_matching:` block to
  `config/server.local.yaml`, flattened in `_server_settings_from_yaml`
  (`backend/app/core/config.py:30-128`) onto pydantic `Settings` fields (env-overridable like every
  other setting) — following the existing `web_find:` block and its `web_find_batch_match_threshold:
  float = 0.6` precedent (`config.py:219`). Params: `enabled`, `title_similarity_threshold`
  (default 90), `author_overlap_threshold` (default 0.5), `require_year_match` (default true),
  `identifier_gate` (default true). These are operator-tuned/boot-fixed (`Settings` is
  `@lru_cache`d); only the `fuzzy_as_confirmed` **toggle** needs runtime edits, hence the `AppConfig`
  home above.

**Reference identity & status (owner items #3, #4)**
- **#3 — shared references across works:** references that are identical after normalization must be
  **one shared record**, linked to every citing work, rather than duplicated per work. See **D6** —
  this is the canonical-reference refactor; it reshapes the core model so its sequencing is the one
  open decision.
- **#4 — a real Confirm action exists.** Status set:
  `unresolved` · `external` · `likely_match` · `local_match` · **`confirmed_match`** · `rejected_match`.
  - identifier (DOI/arXiv) match → `local_match` (stable, rescan-safe).
  - fuzzy ≥ threshold + gates: `fuzzy_as_confirmed` ON → `local_match`; OFF → `likely_match`.
  - user **Confirm/Link** → `confirmed_match` (LOCKED — rescans never revert or re-suggest).
  - user **Not a match** → `rejected_match` (keeps `suggested_work_id`/`match_score` so a rescan
    won't re-propose the *same* candidate, but a *different, better* candidate may still surface).
  `resolved_work_id` is set for `local_match`/`confirmed_match`; `suggested_work_id`+`match_score`
  carry the current guess for `likely_match`/`rejected_match`. **A fuzzy guess is never written to
  `resolved_work_id` while soft** — that column drives ref→ref edges and metrics
  (`reference_graph.py:130-155`) and a wrong guess would corrupt them silently.

**Authors (owner items #4-D4, #5, #6)**
- **D4 — authors now, shown everywhere:** persist `Reference.authors` (parsed by
  `tei_parser.py:17-26` today, silently dropped at `extraction.py:186-200`), display authors in the
  References list, the citing-papers panel, and graph tooltips.
- **#5 — initials+lastname author matching:** normalize each author to `(surname, first-initial)`,
  diacritic-folded, handling both "Last, First" and "First Last". Two authors match when surnames
  are equal AND (either first-initial absent OR the initials agree): "London, Jack" ≈ "J. London" ≈
  "London, J." (J is Jack's initial); "R. London" ✗ "Jack London" (R≠J) — correctly lowers the
  ratio, possibly below 50%.
- **#6 — "et al" handling:** if a reference author list contains "et al", validate against **one**
  author only — the **best surname match** among the candidate work's authors (usually but not always
  the first). A citation that truncates authors *without* "et al" is a bad citation: it simply lowers
  the overlap ratio and we accept that mismatch (no special-casing).

**Rendering (owner items #7 + the two graph asks)**
- **D5 — scope to the per-paper reference graph now;** defer the multi-paper citation-graph resolver
  (its own ephemeral, never-committed DOI/arXiv path, `graph.py:111-124`) to a fast-follow.
- **#7 — likely-match colour** in graphs: close to the local colour but distinguishable (a lighter
  tint / reduced saturation of the local hue), not a brand-new far-off colour.
- **Citing-papers overlap fix** (see §Phase 5): confirmed root-caused — a frontend rendering bug.
- **Show citing papers in the reference graph by default.**
- **D3 — persist + rescan:** matching is computed and persisted (not live/ephemeral), and re-run on
  re-extract and via an explicit rescan (per-paper + library-wide).

---

## Investigation deltas since plan v1

- **Config homes found** (above): YAML `Settings` for numeric params, `WebFindSettings`-style DB
  singleton for the admin toggle. No new config subsystem needed.
- **DOI/arXiv prefix normalization has real gaps** (matters now that D2 makes identifiers the gate):
  - `normalize_doi` (`normalization.py:16`) strips only `https://doi.org/` and `doi:`. **Misses**
    `http://doi.org/` (http), `http(s)://dx.doi.org/`, bare `doi.org/`.
  - `split_arxiv_id` (`duplicate_detection.py:330`) strips only `arXiv:` and
    `https://arxiv.org/abs/`, **case-sensitively and before lowercasing**. **Misses** lowercase
    `arxiv:`, `http://arxiv.org/abs/`, `https://arxiv.org/pdf/`, trailing `.pdf`.
  - Both need hardening (Phase 0) or the identifier gate silently fails on prefixed identifiers.
- **Citing-nodes-in-reference-graph root cause (owner's overlap suspicion confirmed):** the backend
  emits **all** ~100 citing nodes — no limit (`reference_graph.py:219-244`; the 100 cap is upstream
  at store time, `citing_papers.py:31 MAX_CITING=100`, shared by both the panel and the graph). The
  bug is pure frontend: in `referenceGraph.ts` citing nodes have `weighted:0`/`mention_count:0` and
  null metric values, so under every Y axis they collapse to a single Y (0, or the shared "n/a"
  lane) and, bucketed by year on X (`referenceGraph.ts:54-67,135-190`), **pixel-stack** as identical
  10px dots — 100 papers read as "a handful." **`temporalMap.ts` already solves this**
  (`groupByCoord` `:73-82`, count-badge marker `:150-169`, enterable member-listing tooltip
  `:96-115`, host click-delegation `VisualizationsPage.svelte:190-194`). External reference nodes
  have the same latent stacking when they share a year on the n/a lane — fix covers both.
- **Reference model is single-citing-work today** (`Reference.citing_work_id`, single FK,
  `citation.py:23`); sharing (#3) requires a many-to-many, symmetric with the existing
  `ExternalPaper`/`ExternalCitationLink` incoming-citation model — see **D6**.

---

## Design (phased)

### Phase 0 — Independent low-risk fixes (no decision needed; can land immediately)
1. **`normalize_title` whitespace/punctuation order bug** (`normalization.py:6-11`): strip
   punctuation *before* collapsing whitespace so "KnowRob – A…" and "KnowRob: A…" normalize equal.
   This also silently strengthens the **existing** work-vs-work duplicate scanner
   (`_fuzzy_title_candidates`, `duplicate_detection.py:286-323`). Add a regression test for the
   dash/colon case.
2. **DOI/arXiv prefix hardening** (`normalize_doi`, `split_arxiv_id`): cover `http(s)`, `dx.doi.org`,
   bare host, lowercase `arxiv:`, `/pdf/`, trailing `.pdf`; lowercase before prefix-stripping.
3. **Column-picker Apply/Cancel to the top** (`ColumnPicker.svelte:87-91`): move the `.actions`
   block above `<ul class="cols">` (or render it both above and below) so the owner needn't scroll a
   long column list to confirm. Trivial.
4. **Show citing papers in the reference graph by default:** flip the default of the `include_citing`
   toggle in `ReferenceGraphModal.svelte` / its caller (default the checkbox on; endpoint already
   supports `include_citing=true`, `works.py:1520-1538`).

### Phase 1 — Canonical-reference refactor (#3) · **gated on D6**
Split the per-work `Reference` into a deduplicated canonical record + a citing-work link table,
mirroring `ExternalPaper`/`ExternalCitationLink`:
- **`Reference`** becomes the canonical cited-thing: `title`, `normalized_title`, `doi`, `arxiv_id`,
  `year`, `authors` (JSON), `resolved_work_id`, `resolution_status`, `suggested_work_id`,
  `match_score`. Deduped by **key = normalized DOI → else arXiv base → else (normalized_title, year)**.
- **`ReferenceCitation`** link table: `(reference_id, citing_work_id, source_tei_id, created_at)` —
  which papers cite this reference. Replaces `Reference.citing_work_id`.
- **`CitationMention`** keeps `reference_id` + `citing_work_id` (per-paper mentions/section weights
  are unchanged — the canonical reference is shared but the *mentions* stay per-citing-work, so
  `build_reference_graph`'s section weighting still works).
- Rework the idempotent re-extract in `store_parsed_extraction` (`extraction.py:183-200`): instead of
  delete+recreate `Reference` rows for the work, **unlink** this work's `ReferenceCitation`s + mentions
  and re-link/create canonical references by dedup key.
- Update every `Reference.citing_work_id == work.id` read to go through the link table:
  `build_reference_graph` (`reference_graph.py:89-95`), `list_work_references` (`works.py:1506-1512`),
  merge propagation (`duplicate_resolution.py:281-282,388`), the multi-paper graph loader.
- **Payoff that makes this worth it:** matching then runs **once per canonical reference**, not once
  per (work, reference) pair, and a resolution/confirmation applies to *all* citing works at once —
  cheaper and globally consistent.

**Owner decision (D6): do Phase 1 first.** Migrating an existing real-data deployment is safe
**provided the structural change is split from the deduplication** — the risk is never the link
table, it's silently merging two existing reference rows with conflicting resolutions.

**Phase 1a — structural migration (lossless, mechanical, reversible).** A pure **1:1 expansion**;
every existing `Reference` keeps its row and `id`. Paired schema+data migration in the house style
(`0029_access_control_backfill.py`: `op.get_bind()` + `sa.text`, idempotent-ish, dual Postgres/SQLite):
  - create `reference_citations`; insert **one** link per existing reference (`reference_id`,
    `citing_work_id`, `source_tei_id`, `created_at` copied over). Set-based `INSERT … SELECT` with
    `gen_random_uuid()` on Postgres (fast over tens of thousands of rows); batched Python fallback on
    SQLite/tests. **Never** a per-row Python loop over the whole table.
  - add nullable `references.normalized_title` / `authors` / `suggested_work_id` (FK→works SET NULL) /
    `match_score`; backfill `normalized_title` from existing `title` in batches (via the *fixed*
    normalizer). `authors` starts NULL — old references recover authors only on re-extraction (the
    original TEI would need reparsing); **not a blocker**.
  - drop `references.citing_work_id` / `source_tei_id` via **`batch_alter_table`** so SQLite can
    rebuild the table (Postgres drops in place). Runs in one transaction on PG → clean rollback on
    failure.
  - **No `resolved_work_id` / `resolution_status` / `CitationMention` is touched.** Zero data loss by
    construction. Downgrade reconstructs the single `citing_work_id` from the one link — **reversible
    until any consolidation (1b) runs.** Data-integrity test: `count(reference_citations) ==
    count(references)` and every mention still resolves post-migration.

**Phase 1b — consolidation (separate, forward-only, reviewable — NOT in the schema migration).**
Row-sharing happens *going forward* as papers re-extract (new extraction path links by dedup key),
plus an **opt-in "consolidate references"** maintenance job. That job **must skip + report** any
dedup group whose members have conflicting `resolved_work_id` or mixed statuses (e.g. one
`confirmed_match`, one `external`) rather than auto-picking; it only auto-merges unambiguous
duplicates (repointing `reference_citations` + `citation_mentions` to the survivor, deleting losers,
audit-logged). A real deployment can therefore upgrade with **zero data change** and run — or never
run — consolidation later. Matching resolves references to *works* regardless of whether duplicate
reference *rows* are ever merged, so 1b is pure tidiness, not a correctness dependency.

### Phase 2 — Matcher + config + persistence + rescan (D1, D2, D3, #2)
- **`config/server.local.yaml` `reference_matching:` block** → new `Settings` fields (§Decisions #2).
- **`find_reference_match(db, reference)`** in a new `services/reference_matching.py`:
  1. **Identifier gate (D2):** if the reference has a DOI/arXiv id, look up works by normalized
     identifier (reuse `_local_work_index`/`_identifier_keys` shape, `citation_graph.py:326-379`); an
     identifier present on both sides that *differs* disqualifies that candidate.
  2. Else **fuzzy:** candidate works via the first-token blocking key (`_blocking_key`,
     `duplicate_detection.py:29-36`) over `Work.normalized_title`; `similarity_pct(ref.title,
     work.canonical_title) ≥ title_similarity_threshold`; year gate; author-overlap gate (Phase 4).
  3. Best candidate → set status per the `fuzzy_as_confirmed` toggle (§Decisions #4).
- **Persist at extraction:** call the matcher in `store_parsed_extraction` for each (canonical)
  reference; also **populate `arxiv_id` from the parsed reference** (add `arxiv_id` to
  `ParsedReference` + TEI parse; currently absent) so the identifier gate has arXiv to work with.
- **Reverse rescan on new work:** when a `Work` is created (manual/create/import/from-reference),
  check existing `external`/`unresolved` references against **just that one new work** (cheap — one
  candidate), mirroring `_local_work_index`'s reverse identifier lookup
  (`citation_graph.py:342-359`). This is what fixes the owner's backlog without re-extracting.
- **Explicit rescan** (D3): per-paper + library-wide `POST` endpoint following the
  `POST /duplicates/scan` precedent (`duplicates.py:149-204` — sync for one work, enqueued on the
  worker for a full scan). Also re-run on re-extract (already covered by the extraction hook).

### Phase 3 — Confirm/reject/import actions + admin toggle (#1, #4)
- **`PATCH /works/{work_id}/references/{reference_id}`** body `{action: "link"|"reject"|"import"}`
  (with Phase 1, this acts on the canonical reference and applies to all citing works):
  - `link` → `resolved_work_id = suggested_work_id`, status `confirmed_match` (locked).
  - `reject` → status `rejected_match` (keep suggestion for display; don't re-propose it).
  - `import` → existing `import_reference_as_work` (`works.py:872-907`), unchanged.
- **Admin toggle** `use_fuzzy_match_as_confirmed`: add the bool column to `AppConfig`
  (`app_config.py` + migration), `effective_*`/`update_*` in `services/app_config.py`, expose in
  `AppConfigOut`/`AppConfigUpdate` + `PATCH /app-config` (`admin.py`), checkbox in the AdminPage
  "Settings" sub-tab. Flipping ON should offer/trigger a library-wide rescan so existing
  `likely_match`es promote to hard links.

### Phase 4 — Authors (D4, #5, #6)
- Persist `Reference.authors` (canonical, JSON list) from `ParsedReference.authors`.
- **Author-matching util** in `reference_matching.py`: surname+initial normalization, "et al"
  single-author validation, overlap ratio vs `author_overlap_threshold` (§Decisions #5/#6). Gate is
  **skipped** when either side has no authors (a signal you can't compute can't disqualify).
- **Display authors** in: References list (`WorkDetail.svelte` refs panel), citing-papers panel
  (`ReferenceRead` gains an `authors` field; the citing-papers path already has data —
  `ExternalPaper.authors` exists as a `"; "`-joined `Text` column, `external_citation.py:37` — so
  just surface it), graph tooltips.

### Phase 5 — Reference-graph rendering (D5, #7, overlap fix)
- **`likely_local` node kind** in `build_reference_graph` (`reference_graph.py`), carrying
  `suggested_work_id`/`match_score`, left unresolved so local-only metrics aren't computed on a guess.
- **`referenceGraph.ts` colour:** `likely_local` = a lighter tint of the local hue (#7); extends the
  batch11 base/external palette work.
- **Overlap collapse (the citing-papers fix):** port `temporalMap.ts`'s `groupByCoord` (`:73-82`) +
  count-badged cluster marker (`:150-169`) + **enterable** tooltip (`:96-115`) into
  `buildReferenceGraphOption`, covering **citing and external** nodes. Tooltip lists up to **10**
  members but shows the **true total** count; each member is a clickable `[data-viz-open]` link, plus
  an **"import all"** affordance over the cluster. Add container-level click delegation in
  `ReferenceGraphModal.svelte` (currently only `chart.on('click')`, `:79-95`) reusing its existing
  open-vs-import branch; "import all" iterates members (note: `pendingImportText` holds a single
  value today — extend to accept multiple lines or call the batch-import path).

---

## Remaining open details (non-blocking)

**D6 is decided — canonical-reference refactor first, migrated safely** (see Phase 1a/1b above). The
migration is a lossless 1:1 structural expansion; deduplication is a separate, forward-only,
reviewable job that a real-data deployment can run later or never. No further owner input needed to
start Phase 1.

- **Author-overlap denominator (without "et al"):** ratio over the reference's listed authors vs.
  over the max of both lists. *Recommend ref-side denominator;* tunable via `author_overlap_threshold`.
  Settle during Phase 4 — does not affect earlier phases.
- **`pendingImportText` for "import all":** the store holds a single value today; extend it to accept
  multiple lines or call the batch-import path. Decide during Phase 5.

---

## Sequencing

0. Phase 0 fixes (independent, land anytime).
1. Phase 1a structural migration (1:1 expansion) + read-path rewrites; **verify data integrity on a
   Postgres copy with real data before proceeding.** Phase 1b consolidation job ships but stays opt-in.
2. Phase 2 matcher + config + persistence + rescan (title/year + identifier gate).
3. Phase 3 actions + admin toggle.
4. Phase 4 authors (persist + display + author gate).
5. Phase 5 graph rendering (likely_local colour + citing/external overlap collapse).
6. Fast-follow (deferred, D5): extend the matcher to the multi-paper citation graph and fix its
   never-committed resolver (`graph.py:111-124`).

## Cross-cutting (per AGENTS.md)

- **Tests:** `normalize_title` dash/colon regression; DOI/arXiv prefix cases; matcher unit tests
  (identifier gate incl. conflicting-DOI rejection, title-threshold boundary, year gate, blocking
  key); author-matching (initials, "et al", R≠J mismatch); extraction wiring; reverse-rescan on
  new-work; link/reject/import endpoint; `fuzzy_as_confirmed` on/off behaviour; canonical-reference
  dedup + link-table re-extract idempotency (Phase 1); overlap-collapse frontend test. Nearest
  homes: `test_reference_graph.py`, `test_citation_graph.py`, `test_import_reference_and_keys.py`,
  `test_metadata_match_pct.py`, `columns.test.ts`.
- **Migrations:** new columns/tables need parity + autogenerate-clean tests (standing rule, top of
  `PROGRESS.md`). Phase 1 is the big one.
- **Audit events** on confirm/reject/import and library-wide rescan (bulk/destructive-action rule).
- Update `PROGRESS.md` + `CHANGELOG.md` + a `docs/agent_handoffs/` note; commit per logical chunk
  (`area: description`, no Co-Authored-By). **Do not push without explicit approval.**

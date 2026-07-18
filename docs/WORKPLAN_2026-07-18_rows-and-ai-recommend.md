# Workplan — (1) "Rows" grouping layer + (2) AI "Recommend categorization"

Status: **DECISIONS RESOLVED 2026-07-18 (see below + Part C). Implementing in Part-D order.**
Author: agent (2026-07-18).

This plan is written against a full code inventory (three read-only sweeps). It lists every seam
that must change, the migration/rollout, a comprehensive test battery, and the open questions.

## DECISIONS (RESOLVED 2026-07-18) — supersede any "assumption" wording below

- **C1 Hierarchy:** Row ⊃ Rack ⊃ Shelf ⊃ Paper. A paper's row is inferred work→shelf→rack→row.
- **C2 Cardinality:** rack↔row many-to-many (`row_racks`).
- **C3:** AI scope summaries also accept `row`.
- **C4:** ALL deletes emit audit events — add `row.deleted` AND backfill `rack.deleted`/
  `shelf.deleted`/`tag.deleted`.
- **C5 Scoring:** rank points = `K − p + 1` (p = 1-based pick). Parent→child propagation factor
  `0.5`. The multi-parent combine is a **pre-run user choice (dropdown): sum | median | max** of the
  picked parents' scores, then ×0.5.
- **C6 Ranking vs affinity:** a **pre-run option** (Ranking or Affinity). If Affinity is chosen but
  the LLM doesn't return usable numbers, fall back to Ranking **and surface that fact** in the UI.
  Each per-paper result exposes **two popups**: (a) raw rank/score/affinity table, (b) the raw LLM
  input+output for that paper.
- **C7:** add an Ollama `format:"json"` helper (with free-text-parse fallback).
- **C8 Scale:** background RQ job with progress+cancel; embedding pre-filter is an **optional**
  toggle; max-papers cap **default 100** (configurable) with a warning.
- **C9 Caching:** results are **cached** per (scope + settings + model) with timestamp — NOT
  ephemeral — with an explicit "recompute" action. New persistence model (see B0a). Reason: a full
  run can take hours; a dropped connection / tab refresh must not force a full recompute.
- **C10 No-LLM:** degrade to embedding-cosine ranking (no affinity); message the user clearly.
- **C11 UI:** introduce a **sub-tab bar inside Insights** (like the Import tab's sub-tabs). Existing
  Insights content becomes one sub-tab (e.g. "Analysis"); "Recommend categorization" is a new sub-tab.
- **C12 Order:** Rows fully first, then the AI feature (Part D sequence).

---

## PART A — "Rows" grouping layer

### A0. The hierarchy decision (BLOCKING — see Part C #1)

Working assumption for the rest of this plan: **Row is the new BROADEST layer that contains racks.**

```
Row  ⊃  Rack  ⊃  Shelf  ⊃  Paper(work)
 (new)   (via     (via      (via
         row_racks) rack_shelves) shelf_works)
```

Rationale: the user called it a "base grouping layer" and "rows of book racks" (a row is a line of
racks). So a paper's row membership is INFERRED three hops up: work → shelf → rack → row. This
mirrors exactly how a paper's *rack* is inferred today (work → shelf → rack). If instead Rows sit
*between* Rack and Shelf, every join direction below flips — hence this is blocking.

Assumption: **Rack↔Row is many-to-many** (`row_racks`), mirroring the existing many-to-many
`rack_shelves`. (Part C #2.)

### A1. Data model + migration

`backend/app/models/organization.py`:
- `Row` model (`rows` table) — copy `Rack` verbatim: `id, name(idx), description, status(default
  "active"), access_level(default "open"), created_by_user_id, created_at, updated_at`.
- `RowRack` join (`row_racks`) — copy `RackShelf`: composite PK `(row_id→rows CASCADE,
  rack_id→racks CASCADE)`, `rack_id` standalone-indexed (access filters query by the child), plus
  `added_by_user_id`, `added_at`, `position`.
- `TagRow` (`tag_rows`) — copy `TagRack`: PK `(tag_id, row_id)` + `ix_tag_rows_row_id`.

Migration `backend/alembic/versions/0078_rows.py` (down_revision = current head
`0077_file_extraction_degraded`): create `rows`, `row_racks`, `tag_rows` with the same
indexes/FK-CASCADE as the `0075_tag_scope.py` + `0067` templates. **Models and migrations are
separate definitions** — after adding, verify on Postgres with the parity + autogenerate-clean
tests, then on the live DB run `docker compose exec api alembic upgrade head` (hot-reload does NOT
run migrations). Restart the worker for new job code. (Deploy memory.)

### A2. Access control + grants — `backend/app/services/access.py`, `models/group.py`, `groups.py`
- `models/group.py`: `GRANT_TARGET_TYPES = ("rack","shelf")` → add `"row"`. `GroupGrant`/`DefaultGrant`
  already store `target_type`/`target_id` as strings — no schema change, just the enum + validation.
- `access.py`: add `can_see_row`, `can_modify_row` (reuse generic `_can_see_target`/`_can_modify_target`
  with `target_type="row"`), `visible_rows_query`. Extend `can_see_scope_container` (currently
  hard-codes `("shelf","rack")`) to accept `"row"`. Add `row` to `granted_target_ids` callers.
  Export the new names in `__all__`.
- `services/groups.py` `_check_target`: `model = {rack:Rack, shelf:Shelf, row:Row}[target_type]`.
- `schemas/group.py`: docstrings `'rack' | 'shelf'` → add `'row'`.

### A3. Scope resolution — `services/scope_resolution.py`, `api/scope_params.py`
- `SCOPE_TYPES` add `"row"`; add a `row` branch in `scope_works_query`: `Work → ShelfWork → Shelf →
  RackShelf → Rack → RowRack (row_id == scope_id)`, `.distinct()` (mirrors the rack branch, one hop
  deeper).
- `scope_params.resolve_scope_or_404` already delegates container visibility to
  `can_see_scope_container` — works once A2 is done.

### A4. Endpoints + schemas — new `api/v1/endpoints/rows.py`, mount in `router.py`
- New `rows.py` copied from `racks.py`: `GET ""` list (visible), `POST ""` create (default access
  level), `PATCH /{row_id}` (name/description/status/access_level), `GET /{row_id}/racks`,
  `POST /{row_id}/racks` (add rack), `DELETE /{row_id}/racks/{rack_id}`, `DELETE /{row_id}`
  (with `delete_racks` query flag mirroring rack's `delete_shelves`). Inline Pydantic schemas
  `RowCreate/RowUpdate/RowRead/RowRackRead/RowRackAdd`. Audit events `row.created`/`row.modified`
  (matches the rack/shelf created/modified pattern; note deletes currently emit no event — keep
  parity or add `*.deleted` for all three as a small consistency fix — Part C #6).
- `router.py`: mount `rows` at `/rows`.
- Description field: included by copying Rack (which already has `description`). ✅ satisfies the
  "allow adding row descriptions" requirement.

### A5. Tag scoping to rows — `api/v1/endpoints/tags.py`
- `TAGGABLE_MODELS` add `"row": Row` (so a Row can itself be tagged via TagLink `entity_type="row"`).
- `TagRead` add `row_ids`; `TagScopeUpdate` add `row_ids`; `_tag_reads` batch-load TagRow; `PUT
  /{tag_id}/scope` replace TagRow too; `GET /tags` add optional `row_id` filter.
- `GET /tags/assignable?work_id=`: extend the inference — a paper's rows = its shelves→racks→rows;
  a tag is offered if global OR its scope matches the paper's shelves/racks/**rows**.

### A6. Graph coloring + every color_by / scope Literal
- `services/graph_color.py`: `MEMBERSHIP_COLOR_KINDS` add `"row"`; `EMPTY_GROUP["row"]="unrowed"`;
  add a `row` branch in `membership_groups`: `ShelfWork→Shelf→RackShelf→Rack→RowRack→Row`, applying
  `_visibility_condition` to Shelf, Rack AND Row (a row name surfaces only if the viewer may see the
  whole path). Access-aware, same as the rack branch.
- Backend Literals to extend with `"row"`: `citation_graph.py:50 ColorBy`, `graph.py:57 color_by`,
  `citations.py:43 _ScopeType`, `visualization.py:28 _SCOPE_TYPES`, `scope_resolution.py
  SCOPE_TYPES`, and (Part C #10) optionally `ai.py` scope Literals (currently library/shelf/rack).
- `services/topic_graph.py` `_attach_memberships`, `reference_graph.py` membership dict,
  `visualization.py` `_membership_map` — all iterate `("shelf","rack","tag")`; add `"row"`.

### A7. Membership display, search, saved filters
- `works.py`: `_batch_shelf_rack_refs` → also fetch rows (work→…→row); `WorkRead`/`WorkShelfMembership`
  and the `WorkShelfRackRef`/nested types gain rows; `GET /works/{id}/shelves` nested view can show
  the shelf's racks' rows.
- `works_query.py` + `search_query.py`: add a `row:` search operator (SEE-filtered subquery via the
  3-hop join) and a `row_id` filter param, mirroring `rack:`/`rack_id`.
- `saved_filters.py` + `schemas/saved_filter.py`: add `row_id`.
- `duplicate_resolution.py`: merge repoints row membership? Rows attach to racks, not works, so a
  work-merge doesn't touch RowRack — no change needed (confirm during impl).
- `default_shelf.py` `hard_delete_shelf` cascades RackShelf; add an analogous `hard_delete_rack`
  path if racks can be hard-deleted, cleaning RowRack (FK CASCADE already handles row/rack deletes).
- Other scope consumers needing a `row` branch/label: `export_service.py` (scope_type shelf/rack
  branches), `summarization.py` `_SCOPE_DESCRIPTOR` (human labels) + the container lookup
  `Shelf if scope_type=="shelf" else Rack` (make it row-aware), `citation_summary.py` /
  `venue_author_summary.py` (pass-through — usually free once scope_resolution knows `row`), and
  `ai.py` scope Literals (C3 = yes, add `row`).

### A8. Frontend
- `api/client.ts`: `Row` type; `WorkShelfRackRef`/membership types gain `rows`; `GraphColorBy` and
  `GraphScopeType` add `"row"`; full `*Row*` method set (`listRows, createRow, updateRow, deleteRow,
  listRowRacks, addRackToRow, removeRackFromRow`) + tag scope `rowIds`.
- `lib/catalog.ts`: add `rows` store + `refreshRows/ensureRows`, include in `resetCatalog`.
- `pages/RowsPage.svelte` (new, from `RacksPage.svelte`): CRUD + manage which racks are in a row +
  description editing. Add a "Rows" top-level tab in `App.svelte` (near Racks/Shelves/Tags).
- `components/ScopePicker.svelte`: add `<option value="row">` + a row-id select branch;
  `lib/scope.ts` `scopeSelectionReady`/`resolveScopeRequest` add the `row` case.
- Color-by selectors: `CitationGraph.svelte` (`topicColorBy` union + both `<option>` lists),
  `VisualizationsPage.svelte` `COLOR_OPTIONS` + `MEMBERSHIP_COLOR_KINDS`, `ReferenceGraphModal.svelte`
  colorBy select, `lib/viz/temporalMap.ts` membership branch, `lib/viz/referenceGraph.ts`
  `referenceNodeGroups`/`referenceColorGroups`. (The shared `colorGroups.ts` is generic already.)
- `lib/columns.ts` + `ColumnPicker.svelte` + `PaperTable.svelte`: add a `rows` library column
  (default off), populated from the work's inferred rows.
- `components/WorkDetail.svelte`: "Where is this?" panel shows rows (via the shelf→rack→row chain);
  tag assignable logic already server-driven.
- `pages/TagsPage.svelte`: add row scoping (filter-by-row, per-tag row checkboxes) + `setTagScope`
  rowIds.
- `pages/AdminPage.svelte`: grant target type dropdown add "row".

### A9. Docs — `docs/reference/` (update in the SAME commits per AGENTS.md)
- `02_data_model.md` (rows/row_racks/tag_rows + the 3-hop inference), `03_backend_services.md`
  (access/scope/graph_color/tags row additions), `04_api_surface.md` (rows endpoints), `08_security.md`
  (row grant target type + access levels), `10_user_workflows.md` (managing rows), `11_future_and_
  revision_notes.md` (revision note). LaTeX mirror if compiled.

### A10. Test battery (Rows)
- **Model/migration**: migration parity + autogenerate-clean tests (Postgres) include the new tables;
  add `Row/RowRack/TagRow.__table__` to every trimmed-fixture test that lists `Shelf/Rack/RackShelf`
  (e.g. `test_citation_enrichments.py`, `test_citation_graph.py`, `test_reference_graph.py`,
  `test_topic_graph.py`, `test_visualization.py`, others) — else the new joins error.
- **Access/IDOR** (`test_access_control.py`, `safety/`): a private row is dropped for a non-owner
  (colour + scope + tag-scope + grant), visible to owner/granted; grant target_type "row" works.
- **Scope** (`test_scope_resolution.py`): `row` scope resolves work→shelf→rack→row; empty/no-link → 0.
- **Graph colour** (`test_citation_graph.py`/`reference`/`topic`/`visualization`): colour-by `row`
  infers via shelves; owner sees private row; multi-row node → multi-membership list; `unrowed`
  default. (Mirror the rack tests added on 2026-07-17.)
- **Endpoints** (`test_api_flows.py`): row CRUD, add/remove rack, delete with/without racks, 404/403.
- **Tags** (`test_org_rename_and_tags.py`): tag scoped to a row is offered to a paper in that row.
- **Search/saved filters**: `row:` operator + `row_id` filter.
- **Regression ("not breaking anything")**: run full `make test-full` + up-extraction/up-ai profiles;
  every existing rack/shelf test must still pass unchanged.
- **Frontend**: `client` row methods, `scope.test.ts` row case, `colorGroups`/viz row option,
  RowsPage smoke, ScopePicker row option.

### A11. Rollout / deploy
Live DB is at head; hot-reload runs code but not migrations. Order: land migration → `exec api
alembic upgrade head` → restart worker → (image rebuild only if deps change; none expected). NEVER
`docker compose run api sh` (entrypoint migrates). Clear `.vite` + restart before any e2e.

---

## PART B — AI "Recommend categorization" (Insights)

New feature: for a chosen paper scope, an LLM (or embedding fallback) recommends **tags** or
**categories (rows/racks/shelves)** per paper from the paper's *features* (title, abstract, keywords,
topics), which the user reviews and accepts.

### B0. Reuse map (from the AI infra sweep)
- LLM call: `get_ai_config(db)` + `_ollama_generate(prompt, model, base_url)` in `summarization.py`
  (free text; **no JSON mode today** — Part C #7). Input budget `LLM_INPUT_CHAR_BUDGET = 11000`.
- Embeddings: `resolve_embedding_provider`, `dense_cosine`/`sparse_cosine` — for a **candidate
  pre-filter** and a **no-LLM fallback ranking**.
- Execution: RQ `_enqueue_scope_job` + `@_audited_job` + `job_report_progress`/cancel; result-payload
  pattern (`analysis_graph_job` → `GET /jobs/{id}/result`, requester-gated). Frontend polls `getJobs`.
- Accept precedents: `accept_topic_as_tag`, `create_shelf_from_topic`; add-to-shelf choke point
  `shelf_membership.add_work_to_shelf_checked`; `addTagLink`. RRF idiom for fusing rankings.
- Provenance/guardrails: suggestions are **interactive, not auto-applied**; accept → existing
  audited endpoints. Ephemeral result payload (Part C #9).

### B0a. Caching model (C9) — `models/recommendation.py` + migration
- New `RecommendationRun` row (mirrors the `Summary` provenance shape): `id`, `scope_type`,
  `scope_id` (nullable; library sentinel `uuid.UUID(int=0)`), `mode` ("tags"|"categorization"),
  `params_hash` (stable hash of the settings that affect output: mode, k, scoring=ranking|affinity,
  parent_combine=sum|median|max, prefilter on/off, cap), `params` (JSON, human-readable settings),
  `model_name`, `provider_used`, `fallback` (bool — e.g. affinity→ranking or no-LLM→embedding),
  `result` (JSON payload — the per-paper suggestions + raw LLM in/out for the popups), `status`
  ("running"|"done"|"failed"), `created_by_user_id`, `created_at`, `updated_at`. Cache lookup key =
  `(scope_type, scope_id, mode, params_hash, model_name)`; a Run is reused unless the user hits
  "recompute". Keep an LRU cap (e.g. last N runs) like the summary model cache. Payload is tens of
  kB–a few MB (text) → fine in Postgres JSON.

### B1. Backend service — `services/recommendation.py` (new)
- **Paper features** per work: `canonical_title`, `abstract` (verbatim), `keywords` (JSON),
  `topics` (JSON). Assemble a compact per-paper feature block within the 11k-char budget.
- **Pre-run options** (all persisted in `params`): `mode` (tags|categorization), `k`,
  `scoring` (ranking|affinity), `parent_combine` (sum|median|max — categorization only),
  `prefilter` (bool), `cap` (default 100).
- **Tags mode** (`recommend_tags(...)`): candidate set = assignable tags NOT already applied (reuse
  the `/tags/assignable` inference). LLM returns top-K ranked (+ affinity if `scoring=affinity`).
  Per paper → `[{tag_id, name, rank, affinity?}]` plus the raw LLM in/out.
- **Categorization mode** (`recommend_categories(...)`): for each paper and each kind in
  (row, rack, shelf) INDEPENDENTLY: candidates = all rows/racks/shelves the actor can see (name +
  description), optionally embedding-pre-filtered to top-M. LLM returns top-K ranked (+ affinity).
  Combine (C5):
  - base points for the p-th pick (1=best): `K − p + 1`. If `scoring=affinity`, the per-item base
    value is the affinity (0–100) instead; if affinity is absent → fall back to rank points and set
    `fallback=true` (surfaced in UI).
  - propagate: a rack's score `+= 0.5 × combine(picked parent-rows' scores)`; a shelf's score
    `+= 0.5 × combine(picked parent-racks' combined scores)`; `combine ∈ {sum, median, max}` per the
    `parent_combine` option; parents = the RowRack/RackShelf links (access-filtered).
  - final ranking = shelves by combined score (top-K shown); each shelf carries its per-kind rank,
    affinity, rank-points and the combined-score breakdown (for popup a).
  - No-LLM fallback (C10): rank candidates by embedding cosine (paper vec vs `name + " " +
    description` vec), `fallback=true`, no affinity, clear UI message.
- **Access:** candidates are access-filtered (never suggest a shelf/rack/row the actor can't see).
- Output: `RecommendationRun.result` JSON: per paper → suggestions + `raw_llm` (input+output per
  kind) for the popups.

### B2. Job + endpoints
- `workers/jobs.py`: `recommend_job(run_id, actor_user_id)` (`@_audited_job`, own session,
  `job_report_progress` per paper, cancellable) — writes into the `RecommendationRun` row (status
  running→done/failed). `queue.py`: `RECOMMEND_JOB` const + `enqueue_recommend` (via
  `_enqueue_scope_job`, requester-gated), `_FUNC_LABELS`/`_target` entries.
- `api/v1/endpoints/recommend.py` (new, mounted): `POST /recommend` (scope + options) → find-or-create
  a `RecommendationRun` (return cached if fresh, else enqueue) → `{run_id, job_id?, status}`;
  `GET /recommend/{run_id}` returns the cached result; `POST /recommend/{run_id}/recompute` forces a
  new run. Accept actions reuse existing `POST /shelves/{id}/works` and `POST /tags/{id}/links`.
  Enforce the cap (C8) with a warning.

### B3. Frontend — Insights sub-tab bar (C11) + "Recommend categorization" sub-tab
- **Refactor `InsightsPage.svelte` to a sub-tab bar** (like `ImportPage`'s `import-tabs`): the
  existing analysis content becomes an "Analysis" sub-tab; add a "Recommend categorization" sub-tab.
  Each sub-tab has its own scope picker (or a shared one at the page top — decide during impl).
- Recommend sub-tab: `<ScopePicker>` + `resolveScopeRequest`; **pre-run options**: mode
  (Tags|Categorization), K, scoring (Ranking|Affinity), parent-combine (Sum|Median|Max, categorization
  only), embedding-prefilter toggle; a Run button (shows cached run if present, with its
  timestamp/model/settings and a **Recompute** button) → poll job → render.
- **Tags result**: per paper, the ranked proposed tags (affinity bar when present); checkboxes to
  accept zero/one/more → `addTagLink`.
- **Categorization result**: per paper, the top-K **shelves** by combined score; **two popups** per
  paper (C6): (a) raw rank/score/affinity table across kinds + the combined-score breakdown; (b) the
  raw LLM input+output. Each shelf row has an "Add to shelf" control (multi) → `addWorkToShelf`;
  hovering a shelf shows its description + the racks/rows it's in.
- A clear banner when `fallback=true` (affinity→ranking, or no-LLM→embedding).
- UI patterns copied from the existing queued→poll→render AI actions (WorkDetail summary/topics).

### B4. Test battery (AI recommend)
- Backend unit: the combine/scoring math (deterministic given fixed LLM output) — feed a fake
  LLM/embedding and assert the propagation + final ranking; the JSON-output parser + its degradation;
  the candidate pre-filter; the max-papers cap; assignable-tag candidate set excludes applied tags.
- Access: candidates are access-filtered (a private shelf/row the actor can't see is never suggested).
- Job: enqueue/label/requester-gated result; progress/cancel.
- Frontend: mode toggle, K control, accept-tag / add-to-shelf calls, modal renders, empty/no-LLM
  states.

---

## PART C — Discussion points (need the owner's decisions)

**C1 (BLOCKING). Where does "Row" sit?** I read "base grouping layer" + "rows of book racks" as
**Row contains racks** (Row ⊃ Rack ⊃ Shelf ⊃ Paper); a paper's row is inferred work→shelf→rack→row.
But "a layer below racks" could instead mean **between Rack and Shelf** (Rack ⊃ Row ⊃ Shelf). These
give opposite join directions and change the entire plan. Please confirm which.

**C2. Rack↔Row cardinality.** Many-to-many (a rack can sit in several rows), mirroring rack↔shelf?
Recommended for consistency/flexibility. Or strictly one row per rack?
Answer: Yesm a rack can sit in several rows.

**C3. AI scope for rows.** Should AI *scope summaries* (currently library/shelf/rack only) also accept
`row`? Recommended yes for consistency; small extra work.
Answer: Yes, scope summaries should accept rows as well.

**C4. Deletes emit audit events?** Today rack/shelf *deletes* emit no audit event (only create/modify).
Should I add `*.deleted` events for row (and, for consistency, backfill rack/shelf/tag delete events)?
AGENTS.md says every destructive action should be audited — I lean yes.
Answer: Yes, all deletes should emit audit events.

**C5. Combine formula (AI categorization).** You were unsure. I propose: p-th pick → `K−p+1` base
points; a rack gains `0.5 ×` its picked parent-rows' points; a shelf gains `0.5 ×` its picked
parent-racks' combined points; multiple parents summed; final ranking = shelves by combined score.
Confirm the base formula and the `0.5` propagation factor (your example used slightly different
numbers — `K−p` vs `K−p+1`).
Answer: For the multiple parents, allow the user to choose approach before computation (simple 
dropdown) - sum parent score, median of parents, maximum of parents. Apply 0.5 reduction afterwards.
For the rank score formula, use `K-p+1`.

**C6. Affinity vs rank.** Local LLMs are unreliable at numeric scores. Plan: ask for JSON
`{name, rank, affinity 0–100}`; if affinity is missing/garbage, fall back to rank-only (rank-points
drive everything). Acceptable? (If you want affinity to *replace* rank-points when present, say so.)
Answer: as I asnwered in the CLI, offer both ranking and affinity approaches as a pre-run option.
If affinity is selected and the LLM does not provide it, fall back to ranking but make sure 
this fact is surfaced for the user. Ideally, for the result panel, add two buttons: one will show
(in a popup window) the already mentioned "raw" rank, score and affinity values for the paper.
The other will show the raw LLM output (and possibly input) for that paper (again in popup).

**C7. Add a JSON output mode?** There's no structured-LLM-output today. I'd add an Ollama
`format:"json"` helper (with a free-text-parse fallback like the existing `_categorize_sections`).
OK to add this small infra piece?
Answer: Yes, add the format helper.

**C8. Performance / scale.** Categorization = 3 LLM calls per paper (row/rack/shelf), tags = 1 —
so a 100-paper scope is 300 calls, minutes on a local model. Plan: background job with progress +
cancel, an embedding pre-filter to shrink prompts, and a **max-papers cap** (say 50, configurable)
with a warning. Is a cap acceptable, and what default?
Answer: Yes, make the process a background job with progress + cancel. The embedding pre-filter
should be an option. Make the default cap 100.

**C9. Persist suggestions or ephemeral?** Simplest: compute → show → accept via existing endpoints;
nothing persisted (re-run to recompute). Or persist suggestions (MetadataAssertion/review-queue) for
later? I lean ephemeral (matches your interactive-accept flow).
Answer: I am leaning towards cached approach, with option to recompute. Similar to summaries,
the results should be cached for the given scope / request combination (rememeber to record date&time,
settings and model). What can often happen is that a connection is broken or something requires the user
to refresh the tab or close and re-open the browser. This would require recomputation of the whole
process, which may take up to a few hours. Therefore, caching of the last result (for given settings)
is the best approach. Since it is a bunch of text, I am assuming we are talkin about maybe several tens
to hundreds of kB, maybe a few MB? That would not be that bad.

**C10. No-LLM fallback.** If only the extractive/embedding stack is configured (no generative LLM),
should the feature (a) refuse with a message, or (b) run an **embedding-cosine** ranking (paper vs
category name+description) without affinity? I lean (b) as a graceful degrade.
Answer: (b), just message it clearly to the user.

**C11. UI placement.** Insights has no real sub-tabs (it's stacked cards; Visualizations & Citation
summary are separate top-level tabs). Options: (a) a new **card** inside Insights reusing its scope
picker; (b) a new **top-level tab** "Recommend"; (c) a lightweight sub-tab switch inside Insights.
You said "sub-tab under Insights" — I lean (a) card, or (b) if you'd rather it be its own tab.
Answer: I would a new sub-tab (like Import or Admin tab). With just a card, the page would become 
overcrowded.

**C12. Sequencing.** Rows (Part A) is a large cross-cutting migration; the AI feature (Part B) is
mostly independent but its Categorization mode *needs* Rows to exist. Recommended order: land Rows
fully (A1–A11) first, verify, then build the AI feature. Confirm you want them sequenced this way
(vs. building the AI Tags mode in parallel, which doesn't depend on Rows).

---

## PART D — Suggested sequencing (after C answered)

1. Rows data model + migration + access + scope + endpoints (A1–A4) → tests → verify on live DB.
2. Rows in graph colour / scope Literals / tags / membership / search (A5–A7) → tests.
3. Rows frontend (A8) + docs (A9) → tests → visual pass.
4. AI Recommend backend (B1–B2) + tests.
5. AI Recommend frontend (B3) + tests → visual pass.

Each numbered step = its own reviewable commit set with PROGRESS.md + a handoff note, per AGENTS.md.

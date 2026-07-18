# Handoff: Rows grouping layer — Phase 1 backend foundation (IN PROGRESS)

Big multi-phase feature. Plan of record: `docs/WORKPLAN_2026-07-18_rows-and-ai-recommend.md`
(owner decisions are in Part C + the "DECISIONS RESOLVED" block at the top). Hierarchy:
**Row ⊃ Rack ⊃ Shelf ⊃ Paper**; a paper's row is inferred work→shelf→rack→row. Rack↔Row is M2M.

## Done (committed, tested)

- `948d209` — `Row`/`RowRack`/`TagRow` models (`models/organization.py`, exported in
  `models/__init__.py`) + Alembic `0078_rows` (down_revision `0077_file_extraction_degraded`).
  Verified: `pytest -m slow backend/tests/test_migration_parity.py` (4 pass — parity +
  autogenerate-clean on throwaway Postgres).
- `bc8ba60` — backend wiring:
  - `access.py`: `can_see_row`/`can_modify_row`/`visible_rows_query`; `can_see_scope_container`
    handles `"row"`; new names in `__all__`.
  - `models/group.py` `GRANT_TARGET_TYPES` += `"row"`; `services/groups.py` `_check_target` maps row.
  - `scope_resolution.py`: `SCOPE_TYPES` += `"row"`; `row` branch (work→shelf→rack→row, distinct).
  - `graph_color.py`: `MEMBERSHIP_COLOR_KINDS` += `"row"`, `EMPTY_GROUP["row"]="unrowed"`, access-aware
    `row` branch (visibility on shelf+rack+row).
  - `api/v1/endpoints/rows.py` (new, mounted at `/rows`): CRUD + `GET/POST/DELETE /{row_id}/racks` +
    `DELETE /{row_id}?delete_racks=`. Audit events `row.created/modified/deleted` (+ `rack.deleted`
    on cascade — note: standalone rack/shelf/tag delete audit backfill C4 still TODO).
  - `"row"` added to Literals: `citation_graph.ScopeType`/`ColorBy`, `graph.py` scope+`color_by`,
    `citations.py _ScopeType`, `visualization.py _SCOPE_TYPES`, `ai.py` (6 scope literals).
  - `topic_graph`/`reference_graph` membership loops iterate `("shelf","rack","row","tag")`.
  - Tests: `test_scope_resolution.test_row_scope_infers_via_shelves_and_racks`,
    `test_citation_graph.test_color_by_row_infers_via_racks_and_respects_viewer` (+ `Row`/`RowRack`
    added to that file's trimmed fixture). Full run of scope/access/citation-graph/tags = 94+ green,
    ruff clean.

## Not yet done (workplan A5–A11, then B)

- **A5 Tag scoping to rows** — `tags.py`: `TAGGABLE_MODELS["row"]`, `TagRead.row_ids`,
  `TagScopeUpdate.row_ids`, `_tag_reads` load `TagRow`, `PUT /{id}/scope` write `TagRow`,
  `GET /tags` `row_id` filter, and `GET /tags/assignable` infer rows via shelves→racks→rows.
- **A7 Membership/search** — `works.py` `_batch_shelf_rack_refs` + `WorkRead`/`WorkShelfMembership`
  gain rows; `works_query.py`/`search_query.py` `row:` operator + `row_id`; `saved_filters.py` +
  `schemas/saved_filter.py` `row_id`; `export_service.py` + `summarization.py` scope label/container
  for `row`.
- **A8 Frontend** — `api/client.ts` `Row` type + `*Row*` methods + `GraphColorBy`/`GraphScopeType`
  `"row"`; `lib/catalog.ts` `rows` store; `RowsPage.svelte` (from RacksPage) + "Rows" tab in
  `App.svelte`; `ScopePicker.svelte` + `lib/scope.ts` `row`; colour selectors (CitationGraph,
  VisualizationsPage `COLOR_OPTIONS`+`MEMBERSHIP_COLOR_KINDS`, ReferenceGraphModal, temporalMap,
  referenceGraph); `lib/columns.ts`+ColumnPicker+PaperTable `rows` column; WorkDetail "where is this"
  rows; TagsPage row scoping; AdminPage grant target "row".
- **A9 Docs** — `docs/reference/02_data_model.md`, `03_backend_services.md`, `04_api_surface.md`,
  `08_security.md`, `10_user_workflows.md`, `11_future_and_revision_notes.md`.
- **A10 Tests** — rows endpoint CRUD/IDOR (`test_api_flows`/`safety`), tag-scope-to-row, `row:`
  search, saved filter; **add `Row`/`RowRack`/`TagRow.__table__` to EVERY trimmed-fixture test that
  lists Shelf/Rack** (else the new joins break there): e.g. `test_citation_enrichments.py`,
  `test_reference_graph.py`, `test_topic_graph.py`, `test_visualization.py`, and any others.
- **A11 Rollout** — run `docker compose exec api alembic upgrade head` on the live dev DB BEFORE any
  live/e2e use of row features (hot-reload does NOT migrate; the `rows` tables don't exist in the live
  DB yet — current backend code only touches them via the new opt-in endpoints/colour/scope, so the
  live app is unaffected until those are exercised). Then `make test-full`.
- **Part B** — the entire AI "Recommend categorization" feature (see workplan B0a caching model, B1–B4).

## Assumptions / notes

- `delete_row?delete_racks=true` hard-deletes racks the caller can modify (drops their RackShelf
  links; RowRack cascades). Racks are M2M with rows, so this removes the rack from ALL rows — same
  semantics as rack's `delete_shelves`.
- `ColorBy`/scope are Python `Literal`s (not runtime-enforced), so `build_citation_graph` accepted
  `"row"` before the Literal was updated; the Literals were updated so the Pydantic API layer accepts
  it too.

## Security implications

- Row access mirrors rack access exactly (grant target type, access levels, scope-container SEE,
  colour visibility). A private row is dropped for non-owners in colour + scope, visible to
  owner/granted — covered by the new colour test; extend the safety/IDOR suite in A10.

## Next recommended step

- Continue A5 (tag scoping to rows) → A7 → A8, committing each as its own chunk with PROGRESS +
  handoff updates, then run the live migration + full regression before Part B.

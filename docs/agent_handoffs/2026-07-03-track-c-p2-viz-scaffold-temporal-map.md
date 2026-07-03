# Handoff — Track C P2 viz scaffold + temporal citation map (2026-07-03)

Built the architectural foundation for the D38 visualization module (P3-P5 build on this) plus the
first view: the Litmaps-style temporal citation map. Extensible **provider (server) → normalized
`VizPayload` → view-registry (frontend)** seam. All work committed on `main` (not pushed).

## Commits (on `main`)

- `48757c8` — `backend: add extensible viz provider registry + temporal_map + /viz endpoint`
- `2526ae3` — `frontend: add lazy ECharts view registry, shared theme + temporal-map visualizations page`
- (this docs commit updates `PROGRESS.md` + adds this note)

## 1. The extensible backend seam — `backend/app/services/visualization.py`

**`VizPayload`** (the contract every provider returns; the endpoint mirrors it 1:1):

```
VizPayload(
  view_type: str,
  nodes: [VizNode(id, x, y, size, color_group, shape, label, meta)],
  axes: {"x": {key, label}, "y": {key, label}} | None,
  edges: [VizEdge(source, target, weight)] | None,   # None unless the edge toggle is on
  legend: {color_by, groups} | None,
  notes: [str],
  axis_options: [{key, label}] | None,               # server-driven dropdown option set
)
```

**Registry (how a new provider slots in for P3-P5) — a one-function change, no plumbing:**

```python
@register_viz("embedding_cluster")           # P3 example
def embedding_cluster(db, actor, scope: VizScope, params: dict) -> VizPayload:
    ...
```

`register_viz(view_type)` is a decorator writing into `_PROVIDERS`; `get_viz(db, actor, view_type,
scope, params)` dispatches (raises `ValueError` → 404 for an unknown type); `available_view_types()`
lists them for the selector. The endpoint and the frontend never change to add a view — register a
provider server-side and a renderer client-side.

**Scope + visibility (reused, never re-implemented):** `get_viz` clamps every scope with
`access.visible_work_ids(actor)`, and scope resolution delegates to `citation_graph._scope_works`
(the same resolver the citation graph endpoint uses — library / shelf / rack / search_result /
selected_papers / import_batch / saved_filter). A reader never gets a hidden paper as a node or edge.

**Node cap:** `MAX_NODES = 500` (mirrors the graph's cap concept); truncation adds a `notes` entry.
`_METRIC_CACHE_NOTE` marks where a scope-keyed cache goes in P3+ (per-request compute is fine now).

## 2. The `temporal_map` provider (P2's one view)

Axis value functions (both X and Y draw from the same set; independently selected via
`params['x_axis']` / `params['y_axis']`, both default-safe):

- `year` → `Work.year`.
- `citation_count` → `Work.citation_count` (P1); NULL → point muted (`None`) on that axis, not dropped.
- `local_degree` → distinct in-library citing papers = incoming-edge count from
  `build_citation_graph(selected_papers, node_mode="local_only")` over the capped node set. Citation
  **resolution is reused, not re-implemented.** Always available; the default Y axis.
- `citation_velocity` → `citation_count / max(1, current_year - year)` (both required, else `None`).
  `current_year` = `params['current_year']` or `datetime.now(UTC).year` (fine in app code).
- `similarity_to_focus` → cosine of the work's dense embedding to a focus paper
  (`params['focus_work_id']`), reusing `topic_modeling._paper_dense_vectors` (the related-works /
  topic-graph embedding path). Unavailable-with-note when: no focus set, focus not visible/found,
  only the hash-BOW baseline is active, or the focus isn't indexed for the model.
- `topic_similarity_to_focus` → Jaccard overlap of the focus's `Work.topics` term set vs each work's
  (topic-space, reusing the existing per-paper topic terms). Unavailable-with-note when no focus or
  the focus has no topic terms.

Encodings: `size` (`size_by` = local_degree [default] / citation_count / none), `color_group`
(`color_by` = status [default, `reading_status`] / work_type / none), `shape` = `"in_library"`
(reserved — all temporal-map nodes are in-library works; the field is kept for P5's
cited-but-absent nodes). Optional `edges` overlay (`include_edges`) reuses the citation graph's
resolved local edges among the scope papers. `legend` lists the distinct color groups present.

## 3. Endpoint — `backend/app/api/v1/endpoints/visualization.py`

`GET /api/v1/viz/{view_type}` (auth = `require_authenticated_user`, mounted with `dependencies=
auth_required` under `/viz`). Query params: `scope_type` (default `library`), `scope_id`,
`work_ids` (repeatable), `x_axis`, `y_axis`, `size_by`, `color_by`, `focus_work_id`,
`include_edges`, `embedding_model`, `current_year`, `max_nodes` (1..MAX_NODES). SEE-guards the
scope container (404), resolves + visibility-clamps a `saved_filter` scope exactly like the graph
endpoint. Unknown view type → 404, unknown axis → 400. Also `GET /api/v1/viz/` → registered view
types. `backend/openapi.json` regenerated (+510 lines; two `/viz/` paths).

## 4. Frontend — view registry + lazy ECharts + shared theme + page

- `frontend/package.json` + `package-lock.json`: added `echarts@^5.5.1` (lock refreshed via Docker
  node; `npm ci` + `--dry-run` lock-check stay in sync). **Lazy-loaded** (`await import('echarts')`
  in the page), so it is a separate 1 MB chunk — the main bundle stays ~357 kB (verified in build
  output).
- `frontend/src/lib/viz/registry.ts` — `view_type → VizRenderer` registry (`registerRenderer` /
  `getRenderer` / `registeredViewTypes`). `buildOption(payload, theme)` returns a plain
  `EChartsOptionLike` object (NOT typed against echarts) so the module never imports the heavy
  bundle and the mapping is unit-testable in jsdom.
- `frontend/src/lib/viz/theme.ts` — shared theme (Seaborn "deep"-like colorblind-aware categorical
  palette, light/dark surfaces, fonts) + `colorForGroup`. P3-P5 renderers reuse this.
- `frontend/src/lib/viz/temporalMap.ts` — the temporal scatter renderer: one scatter series per
  color group (so ECharts renders a legend), per-point `symbolSize` from `node.size`, muted points
  (null x/y) excluded, optional `lines` series for the citation-edge overlay, hover tooltip
  (title / year / citations / local degree), inside-datazoom. Registers itself on import.
- `frontend/src/pages/VisualizationsPage.svelte` — a **Visualizations** tab (added to `App.svelte`
  TABS + lazy-mounted panel, mirroring Insights). View-type selector, scope picker
  (library/shelf/rack/search_result/selected_papers), both axis dropdowns fed from
  `payload.axis_options`, size/color dropdowns, a focus-paper dropdown that appears only for the
  `*_to_focus` axes, a citation-edge toggle. Changing any control re-fetches once a payload exists
  (explicit `on:change` handler — no reactive fetch loop). Lazy-inits ECharts; click a point →
  `pendingLibraryOpen` + `#library` (reuses the existing open-paper action). `data-testid`s on every
  control + `viz-chart` / `viz-notes` for E2E later. Resizes on tab re-show (#9).
- `frontend/src/api/client.ts` — `VizPayload` / `VizNode` / `VizEdge` / `VizAxis` / `VizParams`
  types + `visualization(viewType, params)` (builds the query string) and `listVizViewTypes()`.

## Tests added

- `backend/tests/test_visualization.py` (19 tests, `@pytest.mark.slow`): registry has temporal_map;
  axes map correctly (year→x, citation_count→y, NULL citation_count → muted not dropped);
  local_degree = incoming citation count + edge overlay; citation_velocity (incl. missing-year →
  None); similarity axes unavailable-with-note without a focus; topic_similarity Jaccard; size +
  color encodings + legend; node-cap truncation note; shelf scope filter; **SEE-filter hides a
  private-shelf work from a reader**; unknown view type raises. Endpoint tests: auth required (401),
  payload build, list view types, node-cap note, unknown view type (404), bad axis (400),
  private-shelf scope 404 for a reader.
- `frontend/src/lib/viz/temporalMap.test.ts` (8 tests): registry lookup; axis→ECharts-axis-name
  mapping; color-group series split + muted-point exclusion; size→symbolSize; edge-overlay lines
  series; tooltip content; single-series/no-legend when color_by is none. (No real ECharts/WebGL in
  jsdom — the pure `buildOption` is asserted.)

## Verification

- FULL backend suite: `docker compose exec -T api python -m pytest backend/tests -q` → **797 passed**
  (all green; +19 from this task).
- `ruff check backend agent && ruff format --check backend agent` → clean (host).
- `make frontend-check` equivalent in Docker: `npm ci` + `npm run test` (**98 passed / 1 skipped**,
  incl. the 8 new) + `npm run build` (green; echarts is a separate lazy chunk).
- `backend/openapi.json` regenerated + committed.

## Deviations / notes

- **Similarity axes shipped** (both `similarity_to_focus` and `topic_similarity_to_focus`) — the
  embedding infra (`_paper_dense_vectors`) reused cleanly, so no need to fall back to
  "unavailable" stubs. They correctly return per-node `None` + an axis note when no focus / no real
  embedding model / no topic terms, rather than erroring.
- **`color_by` launch set is `status` + `work_type` + `none`** (single-valued columns, always
  clean). The design mentions topic/shelf/tag coloring too; those are many-valued and deferred to
  P3+ (topic coloring rides on the P3 embedding-cluster/topic work). Adding one is a branch in
  `_color_group` + an option in the page — no contract change.
- **Scope picker** in the page covers library/shelf/rack/search_result/selected_papers; the endpoint
  also accepts import_batch/saved_filter (parity with the graph), just not surfaced as page controls
  yet.
- Did not touch `AUDIT.md` / `DISCUSSIONS.md` / `WORKPLAN_2026-07.md` / `VISUALIZATION_DESIGN.md` /
  `DECISIONS.md`.

## Next recommended task

**Track C P3 — embedding-cluster map.** Register an `embedding_cluster` provider (server-side PCA-2D
over the scope's dense vectors, cached per (scope, model, embedding-version) — this is where
`_METRIC_CACHE_NOTE` points) and an `embedding_cluster` renderer (reuse the shared theme + a scatter
similar to temporal_map). Topic/cluster coloring extends `_color_group`. The whole seam is in place.

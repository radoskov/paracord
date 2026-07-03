# Handoff — Track C P5b: citation-graph depth (§8.9) + UMAP opt-in (2026-07-03)

The **final** workplan phase (`docs/WORKPLAN_2026-07.md` Track C P5). Two additive parts: §8.9
network depth on the existing Cytoscape citation graph, and an opt-in UMAP layout for the
embedding-cluster view. All existing graph modes (`local_only`/`include_external`,
`collapse_versions`, the P2 live show/hide filters) are preserved. Committed on `main` (NOT pushed).

## Commits (on `main`)

- `efb0789` — `backend: citation-graph §8.9 depth — centrality sizing, color-by, warnings, neighborhood`
- `46b9f86` — `backend: opt-in UMAP layout for embedding_cluster (AI extra image)` (also carries the
  regenerated `backend/openapi.json` covering BOTH backend slices — graph `color_by`/node depth
  fields + neighborhood endpoint + viz `layout` — since openapi is a single generated artifact)
- `aea0b53` — `frontend: citation-graph §8.9 depth controls (sizing, color-by, warnings, legend)`
  (all `client.ts` changes land here, including the `VizParams.layout` + `legend.layout` plumbing the
  next commit's page uses)
- `22cb5e5` — `frontend: embedding_cluster PCA/UMAP layout toggle`
- (this docs commit updates `PROGRESS.md` + adds this note)

## Part 1 — §8.9 depth

### Centrality (server-side, `backend/app/services/citation_graph.py`)
- `build_citation_graph(..., compute_metrics=False, color_by="none")`. `compute_metrics` is a gate:
  **off by default** so the many per-request viz callers (`visualization.py`) that only read
  `graph.edges`/degree don't pay the centrality cost. The graph endpoint + neighborhood pass `True`.
- On every node (`GraphNode` gained `degree`, `pagerank`, `betweenness`, `color_group`, `warning`):
  - `degree` = weighted (mention-count) incident degree.
  - `pagerank` = weighted directed PageRank (`_pagerank`, pure-python power iteration, damping 0.85,
    dangling redistribution; sums to ~1). Fine at the node cap.
  - `betweenness` = exact Brandes over the undirected graph. **The Brandes impl moved from
    `citation_summary.py` into `citation_graph.py`** (`_betweenness`); `citation_summary.py` now
    imports it — one shared implementation, as the prompt asked. Behaviour unchanged (P4 bridge-papers
    still green).
- **All three metrics ship on every node**, so `size_by` (degree/pagerank/betweenness) is a pure
  client re-style — no server `size_by` param, no refetch, no relayout on switch.

### color-by / warnings
- `color_by` ∈ `none|shelf|tag|topic|status` → one categorical `color_group` per **local** node
  (external nodes never colored). `status`/`topic` read off the work; `shelf`/`tag` pick the
  alphabetically-first membership (default `unshelved`/`untagged`). **shelf coloring considers only
  non-private shelves** (`_NON_PRIVATE_SHELF_LEVELS`) so a private shelf's name never surfaces as a
  color — the one SEE nuance the service enforces without needing the actor (it takes `visible_ids`,
  not a `User`). `summary["color_by"]` echoes the choice.
- `warning` = reuses D31.4 signals: a `FileWorkLink.warning_state != "none"` OR membership in an open
  `DuplicateCandidate` (either side flagged) — the same signals the `warning:` / `duplicate:` search
  filters key off, so the graph badge agrees with the library filters.

### Neighborhood endpoint
- **`GET /api/v1/works/{work_id}/citation-neighborhood`** (in `works.py`, so the path is exactly
  `/works/{id}/…`; the graph router is mounted at `/graphs`). Query: `hops` (1–3, default 1),
  `node_mode` (default `local_only`), `color_by` (default `none`).
- `build_citation_neighborhood` BFS-expands over local citation links **both directions** (works the
  focus cites — resolved via the references-aware `_local_work_index`; works that cite the focus — by
  persisted `resolved_work_id` or exact DOI match), capped at `MAX_NEIGHBORHOOD_NODES=500`, then
  builds the **induced subgraph** via `build_citation_graph(scope_type="selected_papers", …,
  compute_metrics=True)` (reuses all resolution + centrality + color/warning). Response mirrors the
  citation-graph shape (`summary` adds `focus_work_id` + `hops`). 404 unless the caller may SEE the
  focus; neighborhood clamped to `visible_work_ids`.
- **Response models are defined locally in `works.py`** (`NeighborhoodNodeRead/EdgeRead/…`), NOT
  imported from `graph.py` — importing the graph endpoint module into `works.py` creates a circular
  import (`works → graph → saved_filters → works.build_works_query`). Same field shape as the graph
  endpoint's node so the frontend reuses one `GraphNode` type.

### Frontend (`CitationGraph.svelte`, `client.ts`, `InsightsPage.svelte`)
- `size_by` dropdown re-sizes the live nodes (`applySizing`, reads the shipped metrics; degree falls
  back to client incident-weight so the topic graph still sizes). `color_by` dropdown refetches (via
  the `load` callback, whose signature is now `(nodeMode, collapseVersions, colorBy)`) and **re-colors
  in place**: `renderGraph` computes a topology signature (sorted node ids + edge count); a matching
  refetch updates node `color`/`warn` data on the existing instance and skips the relayout — only a
  real topology change (node_mode/collapse/scope) rebuilds + relays out (honors D17 / "relayout only
  when topology changes"). Warning nodes get a red ring (`node[warn = 1]`), edge width `mapData(weight,
  1, maxWeight, 1, 8)`, and an Okabe–Ito color legend renders below the graph.

## Part 2 — UMAP opt-in (`visualization.py`, `endpoints/visualization.py`, Dockerfile, requirements)

- `embedding_cluster` param `layout` ∈ `pca` (default) | `umap` (unknown → `ValueError` → 400).
- `_project_2d(matrix, layout)` → `(coords, effective_layout, note)`. `umap` path: `_umap_available()`
  (`importlib.util.find_spec("umap")`) guards; absent → PCA + a note; present → `_umap_2d` (lazy
  `import umap`, `random_state=42`, returns `None` when <3 points → PCA). **The base api image has no
  umap, so the path always degrades to PCA** — tests do NOT install umap.
- Layout cache `_LAYOUT_CACHE` re-keyed to `(scope_sig, model, layout)` (tuple now
  `(vector_hash, coords, assignments, effective_layout, layout_note)`) so PCA/UMAP cache
  independently. UMAP's numba JIT cold-start on first call is expected; the cache absorbs repeats.
- `legend["layout"]` carries the **effective** layout so the frontend reflects a UMAP→PCA fallback.
- `umap-learn>=0.5` added to the **ml-extraction** target in `backend/Dockerfile` (alongside
  nougat/marker), documented in `requirements.txt` — **never** in the base `requirements.txt`/`.lock`.
- Frontend: a PCA/UMAP `layout` dropdown on the embedding-cluster view; when UMAP is chosen but the
  payload reports `legend.layout === 'pca'`, a "needs the AI extra image" hint shows.

## Files changed

- `backend/app/services/citation_graph.py` — `compute_metrics`/`color_by`, `GraphNode` depth fields,
  `_attach_node_metrics`, `_betweenness` (moved here), `_pagerank`, `_color_groups`,
  `_warning_work_ids`, `build_citation_neighborhood`, `_direct_citation_neighbors`.
- `backend/app/services/citation_summary.py` — imports the shared `_betweenness` (removed its copy).
- `backend/app/api/v1/endpoints/graph.py` — `color_by` on request; depth fields on `GraphNodeRead`;
  `compute_metrics=True`.
- `backend/app/api/v1/endpoints/works.py` — neighborhood endpoint + local response models.
- `backend/app/services/visualization.py` — `layout` param, `_project_2d`/`_umap_available`/`_umap_2d`,
  cache re-key, `legend["layout"]`, `LAYOUT_ALGORITHMS`/`DEFAULT_LAYOUT`.
- `backend/app/api/v1/endpoints/visualization.py` — `layout` query param.
- `backend/Dockerfile`, `backend/requirements.txt` — `umap-learn` in the AI extra only.
- `backend/openapi.json` — regenerated.
- `backend/tests/test_citation_graph.py` (+12), `backend/tests/test_visualization.py` (+5).
- `frontend/src/api/client.ts` — `GraphSizeBy`/`GraphColorBy`, node depth fields, `colorBy` on
  `citationGraph`, `citationNeighborhood`, `VizParams.layout`, `legend.layout`.
- `frontend/src/components/CitationGraph.svelte` (+ `.test.ts`), `frontend/src/pages/InsightsPage.svelte`,
  `frontend/src/pages/VisualizationsPage.svelte`, plus updated `InsightsPage.scopes.test.ts` /
  `client.additional.test.ts` expectations.

## Tests

- **Backend** (`test_citation_graph.py`, `@pytest.mark.slow`): metrics off by default → zero
  centrality; hub ranks above spokes (PageRank + degree, sums ~1); betweenness flags only the bridge;
  edge weight = mention count; color_by status/topic; color_by shelf skips a private shelf; color_by
  tag defaults `untagged`; warning from file-link + open-duplicate (both duplicate sides flagged);
  neighborhood returns the 1-hop set (2-hop excluded) + summary; neighborhood `None` for a hidden
  focus; endpoint ships depth fields + `color_by`; endpoint neighborhood 1-hop + 401 unauth + 404
  missing. (`test_visualization.py`): `layout=umap` degrades to PCA + note (umap absent); umap used
  when available (monkeypatched stand-in); cache keyed by layout (2 entries); unknown layout →
  `ValueError`; endpoint `?layout=umap` degrades to PCA.
- **Frontend**: `CitationGraph.test.ts` — `load` now called with the color arg; color_by change
  refetches with the chosen group. Updated `InsightsPage.scopes.test.ts` + `client.additional.test.ts`
  for the new `color_by: 'none'` in the citationGraph payload.

## Verification

- FULL backend suite: `docker compose exec -T api python -m pytest backend/tests -q` → **852 passed**.
- `ruff check backend agent && ruff format --check backend agent` → clean (host).
- `make frontend-check` → vitest **124 passed / 1 skipped** + build green (cytoscape + echarts stay
  separate lazy chunks; not in the initial bundle).
- `backend/openapi.json` regenerated + committed. `python scripts/check_secrets.py` clean before each
  commit.

## Assumptions / deviations

- **size_by is client-side** (all three centrality metrics ship per node) rather than a server param —
  this is what enables relayout-free re-sizing; the endpoint only takes `color_by` (which needs
  server-computed groups). This satisfies "compute server-side" (metrics ARE computed server-side) and
  "re-style without relayout for size/color changes".
- **shelf color = non-private shelves only** — the service takes `visible_ids` (a set), not a `User`,
  so it can't evaluate per-user private-shelf grants; restricting to open/visible shelves is the safe
  default that never leaks a private shelf name. A reader with a granted private shelf simply won't see
  it as a color group.
- **Neighborhood incoming edges** use `resolved_work_id` + exact-DOI match (not arXiv-only-unresolved
  references) for discovery; `build_citation_graph` persists resolution, so in practice most references
  carry `resolved_work_id`. The induced-subgraph step re-resolves fully.
- **openapi.json committed in the second backend commit** (single generated artifact spanning both
  slices); the first backend commit has no openapi.
- **Neighborhood response models are local to `works.py`** (not shared with `graph.py`) to avoid a
  circular import; if these ever diverge, extract the graph read-models into a `schemas` module both
  import.
- Not touched: `AUDIT.md` / `DISCUSSIONS.md` / `WORKPLAN_2026-07.md` / `VISUALIZATION_DESIGN.md` /
  `DECISIONS.md`.

## Status

This completes the Track C P5 workplan (P1 counts → P2 scaffold → P3 cluster → P4 summaries → P5a
views → **P5b depth + UMAP**). No further Track C viz phase is queued.

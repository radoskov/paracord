# Handoff — Track C P5a: co-citation, topic-river, similarity-heatmap views (2026-07-03)

Three more visualization views registered on the existing P2 provider→renderer scaffold. No plumbing
change: each view is one `@register_viz` provider + one frontend renderer + a view-registry entry +
a selector option. All work committed on `main` (NOT pushed).

## Commits (on `main`)

- `1b2a7c9` — `backend: add co-citation/coupling, topic-river and similarity-heatmap viz providers`
  (all three backend providers + VizPayload `series`/`matrix` + endpoint `edge_context`/`series`/
  `matrix` + backend tests + regenerated `backend/openapi.json`)
- `b359dc1` — `frontend: co-citation network view (ECharts graph) + P5a client types`
  (coCitation renderer + test + shared client types + page wiring/control gating)
- `24048be` — `frontend: topic-river stacked-area view`
- `e9f935b` — `frontend: similarity-heatmap view`
- (this docs commit updates `PROGRESS.md` + adds this note)

**Deviation from "commit per view":** the three *backend* providers ship in one commit — they live in
the single `visualization.py` module, so per-hunk splitting across commits wasn't clean (interactive
`git add -p` is unavailable here). The three *frontend* renderers are one commit each, per view.

## VizPayload additions (reuse these in P5b+)

Two backward-compatible typed fields on `VizPayload` (backend dataclass + Pydantic
`VizPayloadResponse` + frontend `VizPayload` interface). Every existing view leaves them `None`:

- `series: dict | None` — stacked time-series for the topic river:
  `{"years": list[int], "topics": [{"label": str, "values": list[float]}]}`. One value per year,
  aligned to `years`. Frontend type: `VizSeries`.
- `matrix: dict | None` — labelled square matrix for the similarity heatmap:
  `{"labels": list[str], "ids": list[str], "values": list[list[float]]}`. Row/column order matches
  `labels`/`ids`. Frontend type: `VizMatrix`.

Future chart views (P5b timelines/distributions) should carry data in `series`/`matrix` rather than
overloading `notes`/`legend`. The endpoint already maps both through.

## co_citation edge semantics + renderer choice

- Provider `params['edge_context']`: `coupling` (default) | `co_citation`; unknown → `ValueError`
  (endpoint 400).
- **coupling** = bibliographic coupling. Built from the scope's own `build_citation_graph(...,
  node_mode="include_external")` edges: `cited(w)` = the set of targets `w` cites (a shared target
  may be an *external*, not-in-library work — that's the coupling signal). Weight(a,b) =
  `|cited(a) ∩ cited(b)|`.
- **co_citation** = classic co-citation. Built from the whole visible library's
  `build_citation_graph(scope_type="library", node_mode="local_only")` edges: for each citer, its
  scope-work targets are pooled and every pair gains one shared citer. Weight(a,b) = number of
  in-library works citing both. **Limitation (noted on the payload):** external citing papers have no
  stored references, so co-citation counts only in-library citers.
- Nodes = all (capped) scope works; `size` = degree (distinct linked neighbours), `color_group` via
  the shared `_color_group` helper; `x`/`y` are `None` (no fixed coordinates).
- **Renderer: ECharts `graph` force series**, not Cytoscape. The P2 scaffold drives every view through
  one ECharts renderer registry (`buildOption(payload, theme) -> option`) and the Svelte host only
  does `echarts.init` + `setOption`; adding a Cytoscape path would fork the host. The graph node
  `name` is set to the work id, so the page's existing click-to-open handler (`params.data.name`)
  opens the paper with no change. Edge width encodes weight; one ECharts category per color group.

## Files changed

- `backend/app/services/visualization.py` — `co_citation`, `topic_river`, `similarity_heatmap`
  providers; `VizPayload.series`/`.matrix`; helpers `_coupling_edge_weights`,
  `_co_citation_edge_weights`, `_cosine_matrix`; consts `CO_CITATION_CONTEXTS`, `HEATMAP_CAP`.
- `backend/app/api/v1/endpoints/visualization.py` — `edge_context` query param; `series`/`matrix` on
  `VizPayloadResponse` + mapping.
- `backend/openapi.json` — regenerated (`scripts/dump_openapi.py`).
- `backend/tests/test_visualization.py` — +14 tests (P5a section).
- `frontend/src/api/client.ts` — `VizSeries`/`VizMatrix` types, `series`/`matrix` on `VizPayload`,
  `edgeContext` param + `edge_context` query.
- `frontend/src/lib/viz/coCitation.ts` (+ `.test.ts`), `topicRiver.ts` (+ `.test.ts`),
  `similarityHeatmap.ts` (+ `.test.ts`) — **new** renderers.
- `frontend/src/pages/VisualizationsPage.svelte` — register the three renderers, edge-context control,
  per-view control gating (`isTemporal`/`isCluster`/`isNetwork`/`isChart`), `hasData` render/empty
  condition covering `series`/`matrix`.

## Tests

- **Backend** (`test_visualization.py`, +14, `@pytest.mark.slow`): coupling links two works sharing a
  reference (weight 1, correct degree/isolated node, no coordinates); co-citation links two works
  cited together (weight 1 + in-library-citers note); unknown edge_context → `ValueError`; co-citation
  SEE-filter; topic-river per-year shares sum to 1 + aligned value rows; topic-river excludes no-year
  papers (note); topic-river SEE-filter (a reader's stream excludes the hidden 2015 paper's year);
  heatmap symmetric with 1.0 diagonal + correct off-diagonal cosine; heatmap caps by recency (note);
  `HEATMAP_CAP == 50`; heatmap SEE-filter (hidden id absent); endpoint lists + builds all three
  (edges/series/matrix present); endpoint bad edge_context → 400.
- **Frontend** (+17 across three files): registry registration; graph-series/category/link mapping +
  node & edge tooltips (co-citation); year axis + stacked series + 0..1 axis + empty-series safety
  (topic river); category axes + `[col,row,value]` cells + visualMap floor/cap + tooltip + empty-matrix
  safety (heatmap).

## Verification

- FULL backend suite: `docker compose exec -T api python -m pytest backend/tests -q` → **835 passed**.
- `ruff check backend agent && ruff format --check backend agent` → clean (host).
- `make frontend-check` → vitest **123 passed / 1 skipped** + build green (echarts stays a separate
  lazy chunk: `echarts-*.js`, not in the initial bundle).
- `backend/openapi.json` regenerated + committed.
- `python scripts/check_secrets.py` → clean, before each commit.

## Assumptions / deviations

- **co_citation renderer = ECharts `graph`**, not the existing Cytoscape path (rationale above); this
  is the one renderer choice the prompt left open.
- **topic_river uses shares (each year sums to 1)**, i.e. a 100%-stacked prevalence stream, matching
  the "share of works in each topic" wording; switch to raw counts by dropping the `/year_total`
  division if absolute volume is wanted later.
- **co_citation builds the whole visible-library graph once** for the co-citation context (citers can
  sit outside the scope). Fine at the documented scale (mostly single-user / few LAN users); if a
  library ever grows large, cache it at the `_METRIC_CACHE_NOTE` site or restrict citers to the scope.
- **similarity_heatmap trims by recency** (publication year desc, stable within a year, no-year last)
  past the 50-paper cap; degree-based trimming is the alternative the prompt allowed.
- Not touched: `AUDIT.md` / `DISCUSSIONS.md` / `WORKPLAN_2026-07.md` / `VISUALIZATION_DESIGN.md` /
  `DECISIONS.md`.

## Next recommended task

**Track C P5b** — §8.9 network depth (PageRank/centrality node sizing, color-by, edge thickness by
mention count, per-work neighborhood endpoint) and the **UMAP opt-in** embedding layout (image extra;
numba/llvmlite). The `series`/`matrix` carriers are in place for any further chart views.

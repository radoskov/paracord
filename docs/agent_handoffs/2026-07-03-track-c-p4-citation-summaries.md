# Handoff — Track C P4 citation summaries (§8.11) (2026-07-03)

The README-headline citation analytics. Turns the previously-empty `citations.py` router into a
scoped, cached, SEE-filtered summary endpoint over the same computed layer as the graphs/viz. All
work committed on `main` (not pushed).

## Commits (on `main`)

- `backend: add scoped citation-summary analytics service (§8.11, Track C P4)`
- `backend: implement citations/summary endpoint + regenerate OpenAPI`
- `frontend: add Citation summary panel + client method and year-distribution chart`
- `tests: cover citation-summary metrics, cache, SEE-filter and panel data mapping`
- (this docs commit updates `PROGRESS.md` + adds this note)

## Files changed

- `backend/app/services/citation_summary.py` — **new** analytics service.
- `backend/app/api/v1/endpoints/citations.py` — implemented `GET /citations/summary` (was an empty
  `APIRouter()`; already mounted in `router.py`, no router change needed).
- `backend/openapi.json` — regenerated (adds `/api/v1/citations/summary` + `CitationSummaryResponse`
  and the ranked/missing/year sub-models).
- `backend/tests/test_citation_summary.py` — **new**, 13 tests (`@pytest.mark.slow`).
- `frontend/src/api/client.ts` — `citationSummary()` + `CitationSummary` / `RankedWork` /
  `MissingWork` / `YearCount` / `CitationSummaryParams` types.
- `frontend/src/lib/viz/citationSummary.ts` — **new** pure data mapping (`buildChronologicalOption`,
  `yearLabel`).
- `frontend/src/lib/viz/citationSummary.test.ts` — **new**, 3 vitest cases.
- `frontend/src/pages/CitationSummaryPage.svelte` — **new** panel.
- `frontend/src/App.svelte` — new "Citation summary" tab (import + tab entry + lazy-mounted view).

## Metrics implemented (all SEE-filtered, over the capped scope)

- **Most-cited local** — in-library works ranked by local in-degree, from `build_citation_graph`'s
  resolved local edges (`include_external` mode; local target = node with a `work_id`). Never
  re-resolved here.
- **Most-cited external** — scope works ranked by `Work.citation_count` (P1); works with no count
  are excluded.
- **Frequently-cited-but-missing** — references that resolve to an *external* node (reusing
  `citation_graph._local_work_index` + `_resolve_reference`) aggregated by `_missing_key`
  (normalized DOI → arXiv → normalized title), ranked by distinct-citing-works then total mentions.
  Each carries a representative `reference_id` (preferring a ref with a DOI + real title) so the UI
  can call `POST /works/from-reference/{id}`. A reference resolving to a hidden work is **not**
  surfaced (no leak).
- **Bridge papers** — see method below.
- **Isolated papers** — scope works whose id never appears as source or target of a *local* edge
  (citing only external/missing works still counts as isolated — no in-library link).
- **Chronological distribution** — `Counter(work.year)`; known years ascending, unknown-year bucket
  (`year=None`) last.

## Bridge-centrality method

**Exact Brandes betweenness centrality** on the **undirected, unweighted** local citation graph
(`_betweenness`), scores halved (undirected double-count). Method label surfaced to the client as
`BRIDGE_METHOD = "brandes_betweenness_undirected"` (and in the response's `bridge_method`). Exact
(not an approximation) is affordable because the local graph is capped at `MAX_NODES = 500` — O(V·E)
in pure Python is fast at that size; over the cap the scope is deterministically truncated (title
order) with a `notes` entry. Direction is dropped because a bridge connects clusters regardless of
citation direction. A two-triangles-joined-by-one-paper fixture confirms the joining paper ranks #1.

## Cache signature

In-process `_SUMMARY_CACHE: dict[str, CitationSummary]` keyed by `_scope_signature` =
`sha1(schema_version :: sorted member work ids :: max(updated_at) over the scope :: scope reference
count :: limit)`. Returned to the client as `version` alongside `computed_at`. Any edited/added/
removed scope work (bumps ids or `updated_at`) or any (un)resolved reference (bumps the count)
changes the signature → recompute; identical inputs hit the cache (a test monkeypatches
`build_citation_graph` to raise and proves the hit skips it). An in-process dict is enough at this
scale (mostly single-user / a few LAN users); a persisted cache would slot in behind
`citation_summary` keyed by the same signature (noted in the module docstring).

## Tests

- Backend (`test_citation_summary.py`, 13): most-cited-local ordering; external ranking by count +
  None excluded; missing aggregation by DOI (case/prefix-normalized) **and** by title, with a
  representative `reference_id`; isolated detection; **bridge detection** (two triangles + one
  joining paper → that paper #1); chronological counts + ordering; **SEE-filter** (a reader's summary
  excludes a private-shelf work and counts only visible papers); **cache hit** (no recompute) and
  **cache invalidation** (a new reference → new `version`); empty-scope; endpoint auth (401),
  build, and private-shelf 404 for a reader.
- Frontend (`citationSummary.test.ts`, 3): `yearLabel` known/unknown; `buildChronologicalOption`
  category labels + bar counts; empty-block safety.

## Verification

- FULL backend suite: `docker compose exec -T api python -m pytest backend/tests -q` → **821 passed**
  (+13).
- `ruff check backend agent && ruff format --check backend agent` → clean (host).
- `make frontend-check` → vitest **106 passed / 1 skipped** (+3) + build green (echarts stays a
  separate lazy chunk).
- `backend/openapi.json` regenerated via `scripts/dump_openapi.py` and committed.

## Assumptions / deviations

- **Panel placed as a new top-level "Citation summary" tab** (not folded into the existing large
  Insights page), mirroring the Visualizations tab pattern — cleaner and independently testable. The
  prompt allowed either.
- **Most-cited-local includes in-library works cited from the scope even if outside the scope**
  (e.g. a shelf paper citing a library paper not on the shelf) — this matches `build_citation_graph`'s
  `include_external` local-resolution behavior and the "in-library works ranked by … how many scope
  works cite them" wording. The cache signature is over the scope's own works/refs, so a citation-
  count change on such an out-of-scope target is not reflected until a scope work/ref changes (noted
  in `_scope_signature`).
- **Missing aggregation reuses the graph resolution helpers** (`_local_work_index` /
  `_resolve_reference`) rather than the graph's external nodes, because the aggregation key must span
  title-only references (the graph gives those unique per-reference node ids) and must carry a
  `reference_id` for the import path.
- Did not touch `AUDIT.md` / `DISCUSSIONS.md` / `WORKPLAN_2026-07.md` / `VISUALIZATION_DESIGN.md` /
  `DECISIONS.md`.

## Next recommended task

**Track C P5** — co-citation / bibliographic-coupling network, topic river, similarity heatmap, §8.9
network depth (PageRank/centrality node sizing, neighborhood endpoint), and the UMAP opt-in layout.

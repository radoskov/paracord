# Handoff — Insights/Viz/Summaries audit, chunk C (2026-07-14)

Implements chunk C of the Insights audit (owner decisions recorded in
`docs/WORKPLAN_2026-07-12_structure-audit-discussion.md`, "Insights/Viz/Summaries audit — owner
decisions (2026-07-13)"): merge graph engines on ECharts, shared picker + vocabulary
unification, and the medium-effort duplication/UX items. On `main`, **not pushed**.

## What landed

- **C1+C2** (`7d25815`) — ONE charting stack. `cytoscape` + `cytoscape-fcose` removed from
  `package.json`; `CitationGraph.svelte` rewritten on an ECharts force-directed `graph` series
  (same props/behavior; layouts reduced to Force/Circle; hide-singletons/hide-external-leaves,
  node sizing and theme are client-side option rebuilds — only color-by still refetches). New
  shared `components/ChartHost.svelte` owns the ECharts lifecycle (lazy `import('echarts')`,
  init, ResizeObserver, resize-on-tab-show, theme repaint via `$activeVizTheme`, dispose);
  CitationGraph, ReferenceGraphModal, CitationSummaryPage and VisualizationsPage all sit on it.
- **C3** (`f5aa83f`) — shared `components/ScopePicker.svelte` + `lib/scope.ts`
  (`scopeSelectionReady`, `resolveScopeRequest`). All three analysis tabs now offer all SEVEN
  scope types — Visualizations and Citation summary were missing import_batch + saved_filter
  even though their backends already accepted them. Vocabulary unified: same scope option
  labels/hints on every tab; user-visible "Summarise/Modelled/summariser" → American spelling.
- **C4** (this commit) —
  - `services/vector_math.py`: the four duplicated cosine implementations
    (`topic_modeling._cosine`, `visualization._sparse_cosine`/`_cosine_matrix`,
    `embeddings.cosine_similarity`, `topic_graph._knn_edges`'s inline normalize+matmul) are now
    one module (`dense_cosine`, `sparse_cosine`, `cosine_matrix`);
    `embeddings.cosine_similarity` stays as a thin alias for existing call sites.
  - Temporal map: size-by/color-by changes are a client-side restyle
    (`restyleTemporalMap` in `lib/viz/temporalMap.ts`, mirrors the server's
    `_size_value`/`_color_group`; nodes now ship `venue` in meta) — no refetch. Other views
    keep the refetch (their encodings change server-side computation).
  - Citation summary: the venue/author aggregation loads lazily on first Venues/Authors
    sub-tab open instead of with every summary build.
  - Insights topics: coherence score (as "N% coherent"), representative papers (lazy title
    lookup, click opens the paper) and no-topic outliers are surfaced — the backend always
    returned them; the frontend dropped them.

## Verification

- Full battery green after C4 — `make ready-full` (backend full suite, migrations, frontend
  vitest + build), `make test-safety`, `make e2e` (see PROGRESS.md for counts).
- Remember the local gotchas: worker container caches imports (restart after backend changes);
  never run npm build/test inside the live frontend dev-server container without clearing
  `node_modules/.vite` before e2e (504 Outdated Optimize Dep).

## Open / deferred

- Citation-graph color-by still refetches (needs status/type/topic per node in every payload —
  cheap, but left out of C4 to keep the payload contract stable).
- `_scope_works` in `citation_graph.py` remains a dict-shaped shim over `scope_resolution`
  (candidate for direct migration later).
- Node caps: per-surface admin settings landed in chunk B; consider surfacing the
  `nodes_hidden` note as a clickable "raise cap" affordance someday.

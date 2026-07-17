# Handoff: OR-aware color legend + encoding info row for reference & visualization graphs

## Task

Bring two graph features the Insights citation/topic graphs already have to the paper-view reference
graph and the Visualizations-tab graphs:

1. A per-node **encoding info row** in the tooltip: `size = degree: 27 · color = shelf: kg, embeddings`.
2. **Multi-color OR semantics** for the color legend: a node on several shelves/racks (or with several
   tags) stays visible while ANY of its colors is shown (hidden only when ALL are hidden), and
   hover-highlights while ANY of its colors is hovered.

## Root cause of the broken surfaces

The citation/topic graph (`CitationGraph.svelte`) renders ONE series with per-node categories + a
custom chip legend, and filters/highlights itself with an OR predicate (`groups.every(hidden)` /
per-group index map). The reference graph and the viz renderers instead used the **native ECharts
legend with one series per group**, and assigned a multi-membership node to only its FIRST group's
series — so the native legend (which toggles a whole series) dropped the node when one color was
hidden, and `emphasis: focus 'series'` never highlighted it via its other colors.

## Files changed

- `frontend/src/lib/viz/colorGroups.ts` (new) — shared, unit-tested core: `groupsOfViz`,
  `distinctGroups`, `orHiddenIds` (OR predicate), `isHighlighted`, `encodingRow`, `nextChipState`.
- `frontend/src/lib/viz/ColorGroupChips.svelte` (new) — the chip-row legend (click/shift/ctrl/hover),
  markup + CSS mirroring CitationGraph's inline chips. Emits `toggle {group, shiftKey, ctrlKey}` and
  `hover (group|null)`.
- `frontend/src/lib/viz/registry.ts` — `RenderOpts { sizeLabel?, highlightGroups? }` added to
  `VizRenderer.buildOption(payload, theme, opts?)`.
- `frontend/src/lib/viz/temporalMap.ts`, `coCitation.ts`, `embeddingCluster.ts` — tooltip info row
  (`encodingRow`), chip-hover dimming (`itemStyle.opacity` via `isHighlighted`), native `legend`
  dropped (host owns the chips), `buildOption` takes `opts`.
- `frontend/src/lib/viz/referenceGraph.ts` — exported `referenceNodeGroups` /
  `referenceColorGroups` (single source of truth for group→color so the modal chips match the
  markers); info-row color half now spells out the memberships list for shelf/rack/tag (was hardcoded
  `kind:`/`venue:`); chip-hover dimming; native legend restricted to the edge-class color key;
  `highlightGroups` opt added.
- `frontend/src/components/ReferenceGraphModal.svelte` — renders `ColorGroupChips`; OR filter +
  ctrl-focus + hover-highlight over `referenceNodeGroups`; removed `enableLegendSolo` and the
  legend-ctrl handler (colors are chips now; edges keep a plain legend); resets chip state on a
  colour-scheme switch and on Reset view.
- `frontend/src/pages/VisualizationsPage.svelte` — renders `ColorGroupChips` for the node-scatter/
  network views; OR filter in `renderChart` (combined with the existing ctrl-focus); `vizFocusOnGroup`
  made OR; passes `{sizeLabel, highlightGroups}`; resets chip state on build/restyle. `enableLegendSolo`
  kept for the chart views (topic river / heatmap), which still use the native legend.
- `frontend/src/lib/viz/colorGroups.test.ts` (new) — 12 unit tests for the shared helper.

## Assumptions / decisions

- **Hover-highlight = dim non-matching** (reduced marker opacity) rather than ECharts `emphasis`.
  Works uniformly for the scatter (multi-series) and network views and expresses OR cleanly; the
  visual effect (matching nodes stand out) matches the citation graph. It rebuilds the option on
  chip enter/leave — fine at these node counts, and only on chip hover (not node hover).
- Node-scatter/network views drop their **native** ECharts legend entirely; the chip row is the
  legend. The reference graph keeps a native legend ONLY for the edge-class color key (reference /
  citing / ref↔ref), which the checkboxes still control.
- `referenceColorGroups` replicates the renderer's existing color assignment exactly (venue =
  insertion order, shelf/rack/tag = sorted, kind = fixed palette slots) so chips and markers agree.
- CitationGraph.svelte (already correct) was left untouched to avoid regressing the working surface.

## Tests

- `colorGroups.test.ts`: 12 pass. All viz renderer tests + `CitationGraph.test.ts`: 50 pass (62
  total across the touched modules). No existing renderer test asserted a native legend was *present*
  when colored, so dropping it is safe; tooltip tests still match (info row is additive).
- Ran on the host with a redirected `cacheDir` (scratchpad) so `node_modules/.vite` — container-owned,
  and shared with the live dev server — is never touched (the vite-cache gotcha).
- The 24 unrelated test-file failures under the local config are a pre-existing `katex/dist/
  katex.min.css` import-resolution artifact of the stripped local config (confirmed identical with my
  changes stashed) — NOT caused by this work, and NOT present under the real `vitest.config.ts` / CI.
- **Not done**: the two host `.svelte` components have no unit tests and there is no `svelte-check`
  in this repo, so their wiring is verified by construction + `tsc` on the `.ts` modules only.
  **Needs a visual pass in the app** (HMR should pick it up): confirm chips filter/highlight with OR
  on the reference graph (colour = shelf/rack/tag) and on the temporal map / co-citation / embedding
  cluster, and that the tooltip info row reads correctly.

## Security implications

- None (pure frontend rendering; color-group names are already the access-filtered ones the server
  returns — see the earlier rack-coloring access-aware fix).

## Next recommended task

- Consider converging CitationGraph.svelte onto the shared `colorGroups.ts` + `ColorGroupChips`
  (it predates them) so all four surfaces share one implementation. Low priority — it already works.

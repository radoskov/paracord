# Handoff: Insights citation graph — import DOI-less external/citing nodes

## Task

In the Insights citation graph, clicking an external reference or citing paper did nothing when it
had no DOI (with a DOI it correctly jumps to Import → Identifier). DOI-less nodes should still be
importable: jump to Import → Citations and prefill the batch-citations box with the data available.

## What a citation-graph node carries

`GraphNode` (backend `citation_graph.py`) has only `label` (title), `year`, `doi` for external /
citing nodes (`_external_node`, and the citing-node builder) — no authors/venue. So the richest
free-text line is `Title (year)`, matching the reference graph's `citationLine`.

## Files changed

- `frontend/src/components/CitationGraph.svelte` — new optional prop `onImportCitation(line)`; the
  node-click handler now branches: in-library → `onOpenWork`; external WITH doi → `onImportExternal`
  (unchanged); external WITHOUT doi → `onImportCitation(citationLine(node))` where `citationLine` is
  `Title (year)` (or just the title if no year). Added an explicit `if (node.workId) return;` guard.
- `frontend/src/pages/InsightsPage.svelte` — `importCitation(line)` sets the `pendingImportText`
  store and navigates to `#import`; passed as `onImportCitation`. Imported `pendingImportText`.

## Why this works (existing plumbing, unchanged)

`pendingImportText` is the same store the reference-graph modal uses. `ImportPage` subscribes and
switches to the Citations sub-tab when it's set; `BatchImport` (the citations box) consumes it on
mount, **appending** the line on a fresh line and clearing the store. So each DOI-less click adds a
line and lands the user on the citations box — and multiple clicks queue.

## Assumptions

- Plain `.set(line)` (not append) in `importCitation` is correct: BatchImport itself appends, so a
  set-per-click accumulates lines, matching `importExternal`'s `.set` and the reference-graph flow.
- "All available data" = title + year (a citation-graph node has nothing else). If richer metadata
  is wanted later, the backend `GraphNode` / `_external_node` would need to carry authors/venue.

## Tests

- No new automated test (the ECharts node-click handler isn't exercised in jsdom — the existing
  `CitationGraph.test.ts` doesn't cover it either). `CitationGraph.test.ts` still passes (5), `tsc`
  clean on the changed modules. **Needs a visual pass**: click a DOI-less external/citing node → it
  should open Import → Citations with a `Title (year)` line; a DOI-bearing node still opens Import →
  Identifier.

## Security implications

- None (frontend only; the titles/years shown are already the access-filtered graph payload).

## Next recommended task

- If desired, enrich DOI-less citation lines with authors/venue by adding those fields to the
  citation-graph `GraphNode` + `_external_node` (they exist on the reference-graph node already).

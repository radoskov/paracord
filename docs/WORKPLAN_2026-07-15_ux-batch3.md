# Workplan — 2026-07-15, UX batch 3

Owner request: reader zen mode; reference-graph 500 limit (persisted); refs/citations must reflect
imported papers' metadata; overhaul/automate file download (PROPOSE first); graph UX improvements
(standard buttons, hover encodings, ctrl-click neighborhood focus, edge-snapped zoom); citation/ref
badges in the Library badges column; move Insights "Export this library" out of the way.

## Items

1. **Zen mode for reading** — reader fills (nearly) the whole viewport over a dark backdrop; only
   the scroll/size controls, the reading-mode (normal/dim/dark) control and an exit-zen button
   remain. Combinable with any reading mode. Frontend-only (PdfReader).
2. **Reference graph limit** — base/default citation limit raised to 500; the user's chosen limit
   persists (localStorage) across sessions. Check the backend cap allows 500.
3. **Ref/citation metadata after import** — a reference/citing entry resolved to a library work
   should display the WORK's metadata (year/title) when its own extracted metadata is missing —
   today an unknown-year reference stays unknown forever even after the paper is imported.
   Approach: surface resolved-work fields in the read models / graph nodes (display-side, no
   provenance-polluting backfill of Reference rows).
4. **Download automation** — PROPOSAL ONLY this round (see "Proposal: automatic PDF retrieval"
   below + discussion points). No code changes until the owner picks a direction.
5. **Graph UX** (shared helpers where possible):
   - Standard buttons on every graph: **Show all** (fit-to-content), **Reset view** (fit +
     clear every filter incl. legend solo/ctrl-click focus), **Refresh** (recompute data, then
     reset).
   - Tooltip: append the encoded channels, e.g. `size = <metric>: <value>` / `color = <group>`.
   - **Ctrl-click** a legend chip → show that category + direct neighbors only; ctrl-click a
     node → show that node + direct neighbors only. Ctrl-click again / Reset view to clear.
   - Edge-snapped cursor zoom — investigate feasibility in ECharts without bloating rendering;
     may land as a discussion point if it requires reimplementing roam.
6. **Badges column** — add per-paper reference/citation badges (e.g. "likely matches to review",
   "refs in library") to the Library badges column; requires cheap aggregate fields on the works
   list read model.
7. **Insights export widgets** — move to the right column / bottom of the tab.

## Sequencing

7 → 2 → 3 → 6 → 1 → 5 (buttons → tooltips → ctrl-click → snap-zoom last/optional) → 4 (proposal
text only). Each lands as its own commit with tests where the repo has precedent.

## Status

Filled in as items land — see PROGRESS.md for the commit log.

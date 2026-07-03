# Handoff — three GUI bug fixes: header overlap, dark-theme metadata text, squashed charts (2026-07-03)

Fixed three owner-reported GUI bugs. Frontend-only. Committed on `main` (NOT pushed). Verified in
the running app with Playwright screenshots at 1440x900 @2x (screenshots not committed; written to
`/home/zednik/paracord-theme-shots/`).

## Commits (on `main`)

- `ceccdcb` — `frontend: set form-control text ink so it reads on dark themes` (Bug 2)
- `25f9b76` — `frontend: size Library pane to the measured header height` (Bug 1)
- `f1752ea` — `frontend: resize charts/graphs via ResizeObserver so they fill their container` (Bug 3)
- (this docs commit updates `PROGRESS.md` + adds this note)

## Bug 1 — sticky nav overlaps the top of the page

**Cause.** The nav has many tabs and `flex-wrap: wrap`s to ~3 rows, so the sticky `<header>` is
~126px tall even at 1440px wide (not the assumed ~7rem). `LibraryPage`'s split-pane used
`height: calc(100dvh - 7rem)` (112px) — a fixed guess smaller than the real header. So the pane
started below the tall header but was sized as if the header were short, pushing its bottom ~62px
past the viewport, forcing the whole page to scroll and letting the sticky header cover content.

**Fix.** `App.svelte` measures the header (`bind:this` + a guarded `ResizeObserver`, one-off
`offsetHeight` fallback for the non-DOM test env) and publishes it as `--app-header-h` on the root.
`LibraryPage`'s `.layout` now uses `height: calc(100dvh - var(--app-header-h, 4rem) - 3rem)` (the
3rem covers the tab-hint + content padding). Verified: at 1440 and 1100, `--app-header-h` = 126px
and the pane bottom = 900px = viewport height exactly (was 962/overflowing).

## Bug 2 — dark-on-dark metadata text in the paper view under dark themes

**Cause.** The global `:global(input),:global(select),:global(textarea)` rule in `App.svelte` set a
dark `background: var(--surface-overlay)` but **no `color`**, and no `color-scheme` is declared, so
native form controls fell back to (near-)black text. In the dark themes that is black on
`#322c40` — fails WCAG AA. The visible offender is the paper Details form (Title/Year/Venue/DOI/
arXiv/Abstract inputs + the reading-status select). Confirmed via computed style: `color:
rgb(0,0,0)` on `bg: rgb(50,44,64)`.

**Offending selector:** `:global(input), :global(select), :global(textarea)` in `App.svelte`.

**Fix.** Added `color: var(--ink-strong)` to that rule (one place, fixes every input app-wide in
every theme). After: `color: rgb(205,214,244)` (`#cdd6f4`) on the dark surface — high contrast, AA
pass. Light themes still read (there `--ink-strong` is dark on the light overlay). The amber italic
placeholder styling is unchanged (intentional).

## Bug 3 — ECharts/Cytoscape squashed into ~50px, rest empty

**Cause.** The chart/graph libs `init` before their flex/tab container has its final width and only
resized on a fragile `visible && !wasVisible` toggle, so they measured a tiny/zero width and never
grew.

**Fix.** Attached a `ResizeObserver` to each container that calls `chart.resize()` (ECharts, in
`VisualizationsPage` + `CitationSummaryPage`) / `cy.resize()` + a debounced `relayout()` (Cytoscape,
in `CitationGraph` — a layout computed at tiny width leaves nodes clustered on the left, so resize
alone won't spread them). All observers are guarded (`typeof ResizeObserver !== 'undefined'`) and
disconnected on destroy; the existing visible-toggle resize is kept. Verified full-width: temporal
map + embedding cluster 1366/1366 container/canvas, citation graph 1366/1364, citation-summary chart
fills its grid cell — in both a light (`latte-warm`) and a dark (`mocha-cool`) theme.

Also added `optimizeDeps.include: ['echarts','cytoscape','cytoscape-fcose']` to
`frontend/vite.config.ts` so the dev server stops 504-ing ("outdated optimize dep") on the first
dynamic import of the graph/chart chunks.

## Verification

- `make frontend-check` green (npm ci + `vitest run` 153/1-skip + production build). Note: the
  header measurement was deliberately written with `bind:this` + a guarded RO instead of
  `bind:clientHeight`, because the latter makes Svelte emit an **unguarded** ResizeObserver that
  crashes App's vitest (jsdom has no `ResizeObserver`).
- `python scripts/check_secrets.py` clean. Backend untouched.
- Re-captured screenshots (overwritten, NOT committed) in `/home/zednik/paracord-theme-shots/`:
  `library_{latte-warm,mocha-cool}`, `detail_{mocha-warm,mocha-cool}`,
  `tempmap_/cluster_/summary_/graph_{latte-warm,mocha-cool}`, `reader_{latte-warm,mocha-cool}`
  (sample PDF from `e2e/fixtures/sample.pdf` attached to the "Demo: Foundations of Information
  Retrieval" paper, reader page renders "E2E sample paper about neural networks").

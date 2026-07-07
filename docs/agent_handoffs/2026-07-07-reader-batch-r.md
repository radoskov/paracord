# Handoff — Reader Batch R (Dim/Dark tuning + reference-box overhaul) 2026-07-07

Frontend-only. Committed on `main` (not pushed). Implements Batch R of
`docs/WORKPLAN_2026-07-06.md` (R1 Dim, R2 Dark, R3 reference anchor boxes).

## R1 — Dim: lighter + more yellowish

`frontend/src/lib/reader/readingMode.ts`. New tunable constant:

    DIM_FILTER = 'sepia(0.5) saturate(1.12) brightness(0.98) contrast(0.96)'

(was `sepia(0.35) brightness(0.93) contrast(0.95)`). Higher sepia + a faint saturate push the warmth
(yellow) up; brightness sits near 1 so the page barely dims. A blank white page lands on a soft warm
cream (≈ #faf9f2). Verified by eye against the demo PDF — see `reader_mode-dim.png`.

## R2 — Dark: yellowish dark grey, not near-black

New tunable constant:

    DARK_FILTER = 'invert(0.82) hue-rotate(180deg) sepia(0.28) brightness(1.02)'

(was `invert(1) hue-rotate(180deg) brightness(0.92) contrast(0.95)`). **Key idea:** a full `invert(1)`
maps white→black. CSS `invert(a)` maps white→`1-a`, so `invert(0.82)` lands white on a dark grey
(≈0.18) and lifts black to a warm-light text (≈0.82). `hue-rotate(180deg)` keeps colours roughly
correct through the invert; `sepia(0.28)` warms the grey field; a small `brightness(1.02)` nudges it
into the target band. A blank white page renders as a warm dark grey ≈ #333128 (in the requested
#2a2632–#332f3a range), with light warm text that stays clearly AA-readable. This is achieved with a
CSS filter alone — **no canvas backing colour was needed** (a backing colour behind an opaque, fully
painted page canvas would not show). Verified by eye against the mocha-warm surface — see
`reader_mode-dark.png`.

## R3 — Reference anchor boxes: robust + scroll-mode-capable

Extracted the overlay-box geometry into a tested pure helper
`frontend/src/lib/reader/overlayBoxes.ts`:

- `overlayBoxStyle(box, scale)` — absolute-position CSS from a top-left PDF-space box × the render
  scale. Because it multiplies by the **same** `scale` the page canvas is rendered at
  (`page.getViewport({ scale })`), the box tracks the text across **zoom, resize and re-render**
  (the `{#each}` style expressions read the reactive `scale`, so overlays re-lay-out on every scale
  change).
- `citationBoxesForPage(contexts, page)` and `annotationBoxesForPage(annotations, page)` — per-page
  selection, decoupled from `currentPage`.

`PdfReader.svelte`:

- Paged view now renders overlays via `citationBoxesForPage(contexts, currentPage)` /
  `annotationBoxesForPage(annotations, currentPage)`.
- **Scroll (continuous) view now renders the same overlays per page** — each `.scroll-page`
  (`.canvas-stage`) gets its own citation `.overlay` buttons + annotation `.annotation-overlay` boxes
  for page `i+1`, siblings of that page's canvas. Previously scroll mode drew no overlays at all.
- The reading-mode filter still targets `.canvas-stage canvas` only (via `--page-filter`); overlays
  are siblings, so R1/R2 do **not** tint them. Click behaviour (`onOverlayClick`) is unchanged.

## Verification

- `make frontend-check` green — build OK; **165 tests pass** (1 skipped). New: `overlayBoxes.test.ts`
  (7 tests: scale/zoom tracking + per-page selection); updated `readingMode.test.ts` for the new
  Dim/Dark strings.
- Re-captured (Playwright, admin/paperracks, 1440×900 @2x, NOT committed) into
  `/home/zednik/paracord-theme-shots/`: `reader_mode-dim.png`, `reader_mode-dark.png` (overwritten).
- `check_secrets` clean.

## Deviation — no reference-box screenshots

`reader_refbox-paged.png` / `reader_refbox-scroll.png` were **not** captured: the demo PDF (and every
other paper in the running stack) has **zero** citation contexts with `pdf_coordinates`, so no real
`.overlay` boxes exist to photograph. Seeding coordinates into the shared Postgres DB was declined by
the environment's write-protection (task is frontend-only / backend untouched), and search-hit
highlights are paged-only (they target the single paged `textLayerEl`), so they are not a valid
cross-mode proxy. R3's per-page + scroll-mode overlay rendering is instead covered by the
`overlayBoxes` unit tests and the identical annotation-overlay code path. If a paper with GROBID
citation coordinates is added later, re-run the capture to produce the two refbox shots.

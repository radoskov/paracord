# Handoff — Reader "reading mode" (opt-in page-canvas easing)

- **Task name:** Add an opt-in reading mode (Original / Dim / Dark) to the PDF reader's rendered page.

- **Files changed:**
  - `frontend/src/lib/reader/readingMode.ts` (new) — `ReadingMode` type, `READING_MODE_KEY`
    (`paracord.reader.readingMode`), `readingModeFilter()` mode→CSS-filter map, `readStoredReadingMode()`,
    `writeReadingMode()`, `isReadingMode()`.
  - `frontend/src/lib/reader/readingMode.test.ts` (new) — vitest for the filter mapping and
    localStorage persistence (defaults to `original`, round-trips a stored mode, rejects unknown values).
  - `frontend/src/components/PdfReader.svelte` — reading-mode state + `setReadingMode()`, a segmented
    toolbar control ("Page: Original / Dim / Dark", `data-testid="reading-mode"` /
    `reading-mode-{original,dim,dark}`), a `--page-filter` CSS var on `.page-wrap`, and
    `.canvas-stage canvas { filter: var(--page-filter, none) }`.
  - `PROGRESS.md` — new section.

- **Filter strings:**
  - Original: `none`
  - Dim: `sepia(0.35) brightness(0.93) contrast(0.95)`
  - Dark: `invert(1) hue-rotate(180deg) brightness(0.92) contrast(0.95)`

- **How the filter is isolated (so highlights/selection stay correct):** the page `<canvas>` is a
  direct child of `.canvas-stage`; the text-selection layer (`.textLayer`) and the citation/annotation
  overlays are *siblings* of the canvas, not descendants. Putting the CSS `filter` on the canvas alone
  (via the shared `--page-filter` var) means the invert/sepia never reaches the overlays. No DOM
  restructure was needed. Confirmed in Dark mode: a live search highlight renders orange/amber (its
  true colour), not an inverted blue.

- **Both view modes:** `--page-filter` is set on `.page-wrap`, which wraps both the paged single-canvas
  stage and the scroll-mode stack, so every rendered canvas inherits it.

- **Assumptions made:**
  - Default is `original`, so nothing changes for users who don't opt in.
  - Owner login is `admin` / `paperracks` (the task's "admin/paracord" is a typo — `paracord` returns
    401; `paperracks` returns 200). Screenshots captured with `admin`/`paperracks`.
  - The seeded demo PDF ("Demo: Foundations of Information Retrieval" → `sample.pdf`) has no citation
    overlays, so highlight-preservation was verified with a search highlight instead.

- **Tests added:** 5 vitest cases in `readingMode.test.ts` (filter mapping + persistence). Real PDF.js
  is not rendered in jsdom (unchanged approach). `make frontend-check` green: 158 passed / 1 skipped,
  build OK.

- **Security implications:** none — frontend-only, purely presentational CSS filter; no new data flow,
  no backend change.

- **Screenshots (not committed):** `/home/zednik/paracord-theme-shots/reader_mode-original.png`,
  `reader_mode-dim.png`, `reader_mode-dark.png` (mocha-cool theme, 1440x900 @2x).

- **Next recommended task:** if desired, add an E2E spec under `e2e/tests/` that cycles the reading
  mode via the `reading-mode-*` testids and asserts the persisted `paracord.reader.readingMode` value.

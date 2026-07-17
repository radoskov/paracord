# Handoff: library selected-row color + identifier import preview layout + paper-detail header stacking

Three small UX-polish tweaks, frontend-only (CSS/markup).

## Files changed

- `frontend/src/components/PaperTable.svelte` — the selected row and the hover row shared
  `var(--status-success-bg)`, so a selected paper was indistinguishable from a hovered one.
  Split the rules: `tbody tr:hover` keeps the light tint; `tbody tr.selected` now uses
  `color-mix(in srgb, var(--status-success-bg) 78%, var(--status-success) 22%)` — same hue,
  slightly deeper. `.selected` is declared after `:hover` (equal specificity) so a selected
  row that is also hovered keeps the darker selected tint. (Committed by the owner as `7fc681c`.)
- `frontend/src/pages/ImportPage.svelte` — the Identifier sub-tab's card used `narrow-card`
  (`max-width: 44rem`), which capped the whole card including the `DraftReview` preview. That
  preview's `.draft-fields` grid needs ~40.5rem minimum for its 5 columns, so inside a 44rem
  card every field (incl. the title) collapsed to its minimum — the "shrunk to fit the DOI"
  cramping. Switched the card to `wide` (like the BibTeX tab, which uses the same `DraftReview`)
  and added a `.form-narrow` (44rem) cap on just the intro paragraph and the lookup form, so the
  lone DOI input + buttons stay compact while the preview gets full card width. (Commit `e53ebaa`.)
- `frontend/src/components/WorkDetail.svelte` — the detail header `.bar` was a wrapping flex with
  `justify-content: space-between` holding `<h2>` title, `.title-tags` chips and `.bar-actions`
  buttons on one line, so the action buttons shifted position per paper (more tags / longer title
  → different wrap). Reordered the markup to title → buttons → chips and made `.bar` a vertical
  stack (`flex-direction: column; align-items: stretch`); `.bar-actions` gained `flex-wrap: wrap`.
  Each part now gets its own row; the `{#if message}` status line, quick-read, keywords and topics
  already sat on their own rows below the bar, matching the requested order. (Commit `6516799`.)

## Assumptions made

- 22% mix for the selected tint reads as "slightly different / slightly darker" without becoming
  a jarringly distinct color, per the request. Single number to tune if needed.
- The BibTeX tab is the reference layout for a DraftReview-bearing import card; matching it keeps
  the two preview flows visually consistent.
- The close (✕) button now sits at the end of the buttons row (previously far-right via
  `space-between`); acceptable given the request that all buttons share one fresh row. The
  `not extracted` stub badge stays inline with the title.

## Tests added or skipped

- None. All three changes are pure presentational CSS/markup with no runtime/logic/API surface.
  No new user-facing strings (terminology rule N/A), no config or secrets. No test references the
  touched classes (`.bar`, `.bar-actions`, `.title-tags`, `narrow-card`). Verified by inspection;
  HMR picks them up in the live dev server without a restart.

## Security implications

- None.

## Next recommended task

- Audit the other single-form import tabs for the same crampedness. The **folder** tab also uses
  `narrow-card` but has no preview table, so it is fine; confirm nothing else wraps a wide preview
  in a narrow card.

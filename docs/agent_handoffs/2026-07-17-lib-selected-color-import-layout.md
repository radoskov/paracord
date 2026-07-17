# Handoff: library selected-row color + identifier import preview layout

Two small UX-polish tweaks, frontend-only (CSS/markup).

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

## Assumptions made

- 22% mix for the selected tint reads as "slightly different / slightly darker" without becoming
  a jarringly distinct color, per the request. Single number to tune if needed.
- The BibTeX tab is the reference layout for a DraftReview-bearing import card; matching it keeps
  the two preview flows visually consistent.

## Tests added or skipped

- None. Both changes are pure presentational CSS/markup with no runtime/logic/API surface. No
  new user-facing strings (terminology rule N/A), no config or secrets. Verified by inspection;
  HMR picks them up in the live dev server without a restart.

## Security implications

- None.

## Next recommended task

- Audit the other single-form import tabs for the same crampedness. The **folder** tab also uses
  `narrow-card` but has no preview table, so it is fine; confirm nothing else wraps a wide preview
  in a narrow card.

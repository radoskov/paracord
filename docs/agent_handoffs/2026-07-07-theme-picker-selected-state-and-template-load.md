# Handoff — Theming UX: distinct selected state + "load as template" (2026-07-07)

Frontend + a small read-only backend endpoint. Committed on `main` (not pushed). Two owner-reported
theming/UX fixes.

## T1 — Rack/shelf pickers: visible hover + distinct selected state

The owner saw the selected rack/shelf "vanish" in the dark themes. Two root causes:

- **Hover was invisible in the dark themes.** The rack/shelf rows are `button.secondary`, whose
  normal background is `--surface-overlay` and hover is `--surface-hover`. In `mocha-warm`/`mocha-cool`
  those two were nearly identical (`#322c40` vs `#302a3d`; `#2e3348` vs `#2b3044`), so hover read as
  no change. The two light themes were already fine (white overlay → tinted hover). Fixed by
  brightening the dark `--surface-hover` to sit clearly above the overlay.
- **Selected used a neutral tint too close to hover.** The active row used `--status-success-bg`
  (green tint). Replaced with a dedicated accent-tinted **`--surface-selected`** token so selected is
  obviously different from both the normal surface and the hover shade.

New tokens (added to the schema `ThemeTokens.surface`, all 4 YAMLs, and `themes.generated.ts`):
`--surface-selected`, `--surface-selected-border`, `--surface-selected-text`.

| Theme | base | hover (was → now) | selected | selected-border | selected-text | sel text contrast |
|---|---|---|---|---|---|---|
| latte-warm | #f3efe9 | #efe8e0 (unchanged) | #f1d4da | #a83250 | #4c4f69 | 5.77 |
| latte-cool | #edf0f5 | #e7ecf3 (unchanged) | #d6e2f2 | #245a8f | #4c4f69 | 6.09 |
| mocha-warm | #211e2a | #302a3d → **#423a54** | #4a3826 | #fab387 | #f2e3d5 | 8.87 |
| mocha-cool | #1c1e30 | #2b3044 → **#383e58** | #33436e | #89b4fa | #cdd6f4 | 6.70 |

All selected-text pairs pass WCAG AA (≥4.5:1); every `selected` differs from both `base` and
`hover` (dE ≫ perceptible threshold). Applied in `RacksPage.svelte` and `ShelvesPage.svelte`: the
`.item.active` rule (and an `.item.active:hover` twin, higher specificity than the global
`button.secondary:hover`) paints the selected row with the new tokens so it stays highlighted even
under the cursor.

Verified by eye in the captured shots (below): mocha-cool shows the selected row with a blue tint +
blue border, clearly distinct from a subtly-lighter hovered row and the normal rows.

## T2 — Custom-theme editor: "Load existing as template"

Admin → Themes tab now has a **"Load existing as template"** picker (bundled + custom themes,
grouped) with a "Load into editor" button that prefills the YAML textarea from an existing theme so
an admin can tweak rather than write from scratch.

- **Bundled YAML** is compiled into the app: `scripts/build-themes.mjs` now also emits
  `bundledThemeYaml: Record<string,string>` (verbatim per-theme YAML) into `themes.generated.ts`, so
  loading a bundled template is a pure in-app lookup (no network).
- **Custom YAML** is served by a new `GET /themes/{slug}/source` → `{ id, yaml }` (any authenticated
  user; 404 when missing), backed by the already-stored `CustomTheme.yaml_source`. Frontend calls it
  via `ApiClient.getThemeSource`. `backend/openapi.json` regenerated.

## Files touched

- `frontend/src/lib/theme/types.ts`, `frontend/themes/*.yaml`, `frontend/src/lib/theme/themes.generated.ts`
- `frontend/src/pages/RacksPage.svelte`, `frontend/src/pages/ShelvesPage.svelte`
- `frontend/scripts/build-themes.mjs`, `frontend/src/api/client.ts`, `frontend/src/pages/AdminPage.svelte`
- `backend/app/api/v1/endpoints/themes.py`, `backend/openapi.json`
- Tests: `frontend/src/lib/theme/theme.test.ts` (selected/hover token presence, distinctness, AA
  contrast), `frontend/src/pages/AdminPage.test.ts` (template-load: bundled no-fetch + custom fetch),
  `backend/tests/test_custom_themes.py` (source endpoint verbatim + 404).

## Verification

- `npm run test` (vitest): 171 passed / 1 skipped; `npm run build` clean; `npm run themes:build`
  regenerates cleanly. Backend `test_custom_themes.py`: 12 passed. `check_secrets`: clean.
- Screenshots (Playwright, owner admin/paperracks, 1440×900 @2x, NOT committed) in
  `/home/zednik/paracord-theme-shots/`: `picker_states_mocha-cool.png` (dark),
  `picker_states_latte-warm.png` (light), `theme_template_load.png`.

## Notes / deviations

- Both problems the owner described were real: hover WAS invisible in the two dark themes, and
  selected WAS too close to it. Fixed both (brightened dark hover + new accent-tinted selected).
- Backend change is minimal and read-only; write/validation paths untouched.

# Handoff — Theming P1: YAML→token pipeline + refactor (2026-07-03)

## Task
Implement Theming Phase P1 from `docs/THEMING_DESIGN.md`: build the YAML→token substrate for the
4-theme system and refactor the frontend behind role tokens, with **no visible change**.

## Files changed
Added:
- `frontend/themes/default.yaml` — current light look ported verbatim (palette + tokens + graph).
- `frontend/themes/default-dark.yaml` — preserves the pre-existing dark **chart** palette; GUI
  tokens are a provisional dark mapping, NOT wired to any `[data-theme]` (zero visual effect).
- `frontend/scripts/build-themes.mjs` — codegen: reads `themes/*.yaml`, resolves `palette.<key>`
  refs, writes the committed `themes.generated.ts`. Run via `npm run themes:build`.
- `frontend/src/lib/theme/types.ts` — `Theme` / `ThemeTokens` / `ThemeGraph` interfaces.
- `frontend/src/lib/theme/css.ts` — `tokenEntries` + `renderThemeCss` (pure, testable).
- `frontend/src/lib/theme/index.ts` — registry (`getTheme`, `themeForMode`) + `applyTheme` (injects
  the token `<style>` and sets `<html data-theme>`).
- `frontend/src/lib/theme/themes.generated.ts` — GENERATED, committed (do not hand-edit).
- `frontend/src/lib/theme/theme.test.ts` — token→value byte-identical snapshot + registry tests.
- `frontend/src/lib/viz/theme.test.ts` — `VizTheme` light/dark equals the old hardcoded constants.

Modified:
- `frontend/src/lib/viz/theme.ts` — `resolveTheme` now derives `VizTheme` from the theme objects'
  `graph` section (`bundledThemes`) instead of a hardcoded palette. Interface + `colorForGroup`
  unchanged.
- `frontend/src/main.ts` — `applyTheme()` before `mount`.
- `frontend/src/App.svelte` — removed the `:global(:root){--pg-*}` block; global control styles now
  use role tokens (`--surface-*`, `--ink-*`, `--border-*`, `--accent-*`, `--radius-*`).
- `frontend/src/pages/{SearchPage,LibraryPage,AdminPage}.svelte` — `var(--pg-*)` → role tokens.
- `frontend/package.json` / `package-lock.json` — added `yaml` (devDependency) + `themes:build`
  script; lockfile regenerated in Docker (`make frontend-lock`), `npm ci` verified.

## YAML→CSS/JS approach (and why)
Build-time **codegen with committed output**, not a runtime YAML import or a Vite YAML plugin. The
only step that reads YAML is `build-themes.mjs` (Node + `yaml` dep); the app, `vite build` and
vitest import the committed `themes.generated.ts`, so nothing in the CI path depends on YAML. This
is the least-magic option that keeps YAML hand-editable and survives `npm ci`. CSS is emitted at
runtime by a pure `renderThemeCss(theme)` (also used by the snapshot test) and injected once by
`applyTheme`. To change a theme: edit the YAML, run `npm run themes:build`, commit both.

## How byte-identical appearance was proven (no eyeballing)
1. `data-theme` was **never set anywhere** before this change, so the GUI was always the light look
   and `resolveTheme` was always called in light mode (the DARK `VizTheme` was dead code). Setting
   `data-theme="default"` keeps the chart pages' `=== 'dark'` check false → light, as before.
2. `theme.test.ts` asserts the `default` theme's emitted token map **equals** the exact pre-refactor
   literals (the old `--pg-*` values + the App.svelte colours that were tokenised).
3. Every `--pg-*`/hardcoded value the refactor replaced was swapped for a role token whose value is
   identical (verified: 0 `--pg-*` refs remain; `background: white` → `--surface-overlay` = #ffffff).
4. `viz/theme.test.ts` asserts `resolveTheme('light'|'dark')` deep-equals the old `LIGHT`/`DARK`
   constants byte-for-byte.

## Assumptions
- The dark GUI does not exist yet; `default-dark`'s GUI tokens are a provisional stub (unwired) that
  P2 will design/validate. Only its `graph` (dark chart palette) is load-bearing in P1.
- `ShelfPicker`'s `var(--muted, #555/#777)` references an undefined `--muted`; left untouched so it
  keeps falling back (defining `--muted` would change its colour). Noted as an orphan var for P2.

## Tests
- Added `theme.test.ts` (6) and `viz/theme.test.ts` (5). `make frontend-check`: install + **135
  tests pass** (1 pre-existing skip) + `vite build` OK. Backend untouched → backend suite skipped.

## Security implications
None. Frontend-only, no auth/config/storage/network surface. `check_secrets` clean.

## Ad-hoc colours NOT mapped (deferred to P2)
~130 distinct per-component hardcoded status/neutral colours (green/amber/red/blue/purple badge
families, one-off neutrals like `#d8dee6` variants, `#e2e8f0`, `#526070`, etc.). They cannot collapse
into the role-token set without shifting values, which P1 forbids. They remain literal and are
P2's job (redesign + validation), where minor colour shifts are acceptable.

## Next recommended task
Theming P2: author `latte-warm/cool` + `mocha-warm/cool` YAML; design + validate each
`graph.categorical` with `dataviz/scripts/validate_palette.js`; map remaining component colours onto
the role tokens.

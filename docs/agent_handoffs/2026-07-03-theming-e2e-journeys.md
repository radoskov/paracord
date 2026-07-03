# Handoff — Theming Playwright E2E journeys (2026-07-03)

## Task
Close the browser-E2E gap for the theming feature (`docs/THEMING_DESIGN.md`, P3/P4). Theming shipped
with vitest + backend tests but no Playwright coverage; add browser journeys for the live switcher,
per-user persistence, follow-system, and runtime admin custom themes.

## Files changed
Frontend (testids only — no behavior change):
- `frontend/src/pages/ProfilePage.svelte` — `data-testid="theme-option-<id>"` on each picker button;
  `data-testid="follow-system"` on the follow-system checkbox.

E2E:
- `e2e/helpers.ts` — `apiSetUserTheme(request, token, id|null)` (PATCH `/auth/me { theme }`) and
  `apiDeleteCustomTheme(request, token, slug)` (DELETE `/admin/themes/{slug}`, idempotent) for setup/
  cleanup.
- `e2e/tests/29-theme-switch-persist.spec.ts` — pick a dark theme; assert `data-theme` flips and
  `--surface-base` actually changes; assert server (`/auth/me`) + localStorage cache; reload and
  assert persistence; switch back.
- `e2e/tests/30-theme-all-four.spec.ts` — iterate the 4 bundled themes, asserting `data-theme` + a
  key element stays visible each time; then under the dark theme confirm the Visualizations chart
  still builds without error.
- `e2e/tests/31-theme-follow-system.spec.ts` — `page.emulateMedia({ colorScheme })` drives OS
  appearance; enabling follow-system resolves the light/dark member of the current temperature pair
  (`latte-warm ↔ mocha-warm`) and re-picks on OS flip.
- `e2e/tests/32-theme-custom-upload.spec.ts` — admin uploads a minimal schema-complete custom theme
  YAML via the admin Themes tab; it appears in the Profile picker, applies live (`data-theme` = slug,
  `--surface-base` = its value), then is deleted from the admin tab.

## How the journeys stay stable
- Selection is driven by the new `theme-option-<id>` testids; the boot default is `latte-warm`, so
  each journey has a deterministic baseline (the captured `storageState` caches `latte-warm`).
- `emulateMedia` in Playwright also dispatches the matchMedia `change` event the store subscribes to,
  so follow-system's OS-flip path is exercised end-to-end (no simulated-only shortcut needed).
- Every journey resets the persisted user theme to NULL (and journey 32 deletes its custom theme) in
  `finally`, so the suite stays idempotent across reruns and the shared e2e admin user is left clean.
- The custom theme uses a unique per-run slug (`e2e-custom-<ts><rand>`), so parallel/rerun uploads
  never collide.

## Verification
- Full E2E suite: **31 passed / 2 skipped** (`cd e2e && npx playwright test`). The 2 skips are the
  pre-existing external-service journeys — 9 (GROBID extraction) and 19 (arXiv identifier import) —
  not new. All four theming journeys pass.
- `make frontend-check`: green — 153 tests pass (1 pre-existing skip), production build OK.
- `python scripts/check_secrets.py`: clean.

## Deviations / notes
- No backend change and no product-logic change — testids + tests only.
- "Live restyle of an already-open chart on theme change" is covered indirectly: the picker lives on
  Profile, so journey 30 asserts the Visualizations chart renders correctly *under* a switched theme
  (restyle-on-load) rather than repainting a chart open on the same page. No E2E flakiness was needed
  to guard; the whole feature is local (no external services), so no journey required a skip.
- No real theming bug surfaced — switch, persist, follow-system, and custom upload/apply/delete all
  behaved as designed.

## Next recommended task
Optional: an E2E assertion that an *open* ECharts/Cytoscape view repaints on a same-page theme change
would need a theme control reachable from a viz page (currently picker is Profile-only) — either add
a compact theme toggle to the app chrome or drive the store directly in a test hook.

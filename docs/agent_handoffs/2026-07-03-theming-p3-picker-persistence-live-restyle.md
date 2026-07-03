# Handoff — Theming P3: picker + per-user persistence + live restyle (2026-07-03)

## Task
Implement Theming Phase P3 from `docs/THEMING_DESIGN.md`: a theme picker, per-user server-side
persistence (mirroring `papers_per_page`), a localStorage no-flash cache, and **live** restyling of
the whole running app (GUI tokens + open ECharts charts + the Cytoscape network) on theme change,
with an optional "follow system appearance" toggle. Builds on P2's 4 validated themes.

## Files changed
Backend:
- `backend/app/models/user.py` — new nullable `theme: String(32)` column (NULL = boot default).
- `backend/app/core/themes.py` (new) — `KNOWN_THEME_IDS` + `DEFAULT_THEME_ID` + `is_known_theme`;
  the backend's source of truth for valid ids (mirror when a bundled theme is added/removed).
- `backend/app/schemas/auth.py` — `ProfileUpdateRequest.theme` with a `field_validator` that rejects
  an unknown id (raises → **422**); empty/None resets to default.
- `backend/app/services/users.py` — `theme` added to `_PROFILE_FIELDS`.
- `backend/app/api/v1/endpoints/auth.py` — `theme` included in the `/auth/me` payload.
- `backend/alembic/versions/0050_user_theme.py` (new) — add/drop `users.theme` (real downgrade).
- `backend/openapi.json` — regenerated.
- `backend/tests/test_account_profile.py` — theme round-trip, 422 on unknown id, NULL default.

Frontend:
- `frontend/src/lib/theme/store.ts` (new) — reactive theme store: `activeThemeId`, `activeTheme`,
  `activeVizTheme` (derived), `followSystem`; `setTheme`, `initTheme`, `reconcileTheme`,
  `setFollowSystem`, `readCachedThemeId`; `themeOptions` (data-driven from `bundledThemes`, incl. a
  small swatch). localStorage keys `paracord-theme` / `paracord-theme-bg` / `paracord-theme-follow`.
- `frontend/src/main.ts` — boots via `initTheme()` before mount (was `applyTheme()`).
- `frontend/index.html` — inline `<head>` script applies cached `data-theme` + background pre-paint.
- `frontend/src/App.svelte` — `reconcileTheme(me.theme)` after `/auth/me`.
- `frontend/src/api/client.ts` — `theme` on `CurrentUser` + `updateProfile` payload.
- `frontend/src/pages/ProfilePage.svelte` — the Appearance card (picker + follow-system).
- `frontend/src/pages/VisualizationsPage.svelte`, `CitationSummaryPage.svelte` — read `$activeVizTheme`
  and re-run `setOption` reactively on theme change.
- `frontend/src/components/CitationGraph.svelte` — `buildStyle()` extracted; `restyle()` re-reads the
  palette + swaps the Cytoscape stylesheet on the live instance (no rebuild/relayout), reactive to
  `$activeThemeId`.
- Tests: `frontend/src/lib/theme/store.test.ts`, `frontend/src/pages/ProfilePage.test.ts`; `theme`
  added to the `CurrentUser` fixtures in `session.test.ts` + `PdfReader.test.ts`.

## How it works
- **Persistence priority (boot):** localStorage cache → server `theme` (from `/auth/me`) → default.
  `initTheme()` applies the cache (or default) before mount; `reconcileTheme` adopts the server value
  only when there's no local cache (the device choice wins), then caches it.
- **No-flash:** the inline head script sets `data-theme` + a cached surface background synchronously
  before the app module loads/paints; `main.ts` then injects the full token stylesheet before mount.
- **Live restyle (no reload):** GUI tokens flip via `data-theme` + injected CSS vars (already
  reactive). ECharts pages depend on the `$activeVizTheme` store in their render reactive block, so a
  theme change re-runs `setOption(..., true)` with the new `VizTheme`. Cytoscape's `restyle()` re-runs
  the per-node categorical colour assignment and calls `cy.style(buildStyle(...)).update()` on the
  live instance — positions kept, no relayout.
- **Follow system:** device-local toggle; when on, `matchMedia('(prefers-color-scheme: dark)')`
  chooses the light/dark member of the current temperature pair and stays in sync with OS changes. A
  manual pick disables it (`setFollowSystem(false)` + `setTheme(id)`).

## Assumptions / decisions
- Backend stores the theme id free-form (`String(32)`) and validates against `KNOWN_THEME_IDS` in the
  API → **422** for an unknown id (Pydantic validator, not the 400 the service path raises).
- Follow-system is **device-local** (localStorage only), not server-persisted — the correct semantics
  for "follow THIS device's appearance"; the server keeps a single explicit theme id.
- The localStorage cache intentionally wins over the server value per the design's stated priority.

## Tests
- Backend: `test_theme_defaults_null_and_round_trips`, `test_theme_rejects_unknown_id` (422).
- Frontend: store tests (options are data-driven; `setTheme` maps `$activeVizTheme` to exactly what
  the renderers resolve; unknown-id fallback; `reconcileTheme` priority) + a picker render/click test
  (data-driven options, live `data-theme` flip, `updateProfile({theme})`, localStorage cache).

## Verification
- Full backend suite: **866 passed**. `make test-migrations`: green (parity + autogenerate-clean).
- `ruff check` + `ruff format --check` (backend, agent): clean. `backend/openapi.json` regenerated.
- `make frontend-check`: green — **149 tests** pass (1 pre-existing skip), build OK.

## Security implications
- New per-user preference only; no new auth surface. Unknown theme ids are rejected server-side
  (422), so nothing arbitrary is persisted. `data-theme` values are validated theme ids; the inline
  boot script only reads its own localStorage keys and sets an attribute/background (no injection).

## Next recommended task
Theming P4: custom / hand-edited YAML themes (a `themes/` drop-in and/or admin upload), validated on
load (schema + categorical-palette check, warn-not-fail), surfaced in the same data-driven picker.

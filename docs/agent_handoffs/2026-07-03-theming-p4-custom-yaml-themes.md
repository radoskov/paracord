# Handoff — Theming P4: custom / hand-edited YAML themes (2026-07-03)

## Task
Implement `docs/THEMING_DESIGN.md` P4, the final theming phase: (A) document the hand-edit workflow
that already works for bundled themes, and (B) a **runtime** custom-theme path so an owner/admin can
add a theme from YAML **without a rebuild**, appearing in everyone's picker and restyling the running
app (GUI + charts + Cytoscape network) live. Builds on P1–P3 (bundled YAML → `themes.generated.ts`,
theme registry/`applyTheme`/`renderThemeCss`, reactive store, picker, `User.theme` persistence).

## Storage choice — DB table (not a directory)
`custom_themes` table (migration `0051_custom_themes`): `id`, `slug` (unique; the `data-theme` id +
picker id), `name`, `mode`, `temperature`, `yaml_source` (Text; canonical), `created_by` (FK
`users.id` ON DELETE SET NULL), `created_at`/`updated_at`. Chosen over a directory on the storage
volume because it backs up with the DB, keeps one canonical copy of the YAML, and needs no
filesystem-availability handling. `slug`/`name`/`mode`/`temperature` are denormalised out of the
YAML at upload so the picker list serves without re-parsing; the resolved token/graph object is
re-derived from the YAML on read.

## Files changed
Backend:
- `backend/app/models/custom_theme.py` (new) — `CustomTheme` model.
- `backend/app/core/theme_schema.py` (new) — `validate_and_resolve(yaml)` → `ResolvedTheme` (the
  frontend `Theme` shape). Rejects (`ThemeValidationError`→400) malformed YAML / non-mapping /
  bad `id` / `mode ∉ {light,dark}` / missing required token role / slug colliding with a bundled id.
  Resolves `palette.<key>` refs (mirrors `build-themes.mjs`). Requires the full token role set;
  requires `graph.categorical`; **defaults** omitted presentational `graph` keys from the tokens.
- `backend/app/core/palette_check.py` (new) — Python port of the frontend `paletteCheck.ts`
  (OKLCH band/chroma, WCAG contrast, Machado-2009 CVD ΔE) → **advisory warnings** (best-effort;
  wrapped so it never raises, never rejects).
- `backend/app/services/custom_themes.py` (new) — create/replace-by-slug, list, get, delete,
  `custom_theme_slugs`, `resolve_row`, `list_item` (+ swatch).
- `backend/app/api/v1/endpoints/themes.py` (new) — read `router` (`GET /themes`, `GET /themes/{slug}`,
  any authenticated user) + `admin_router` (`POST /admin/themes`, `DELETE /admin/themes/{slug}`,
  `require_admin`). Create/delete audit-evented `theme.uploaded` / `theme.deleted`.
- `backend/app/api/v1/router.py` — register both routers.
- `backend/app/models/__init__.py` — export `CustomTheme`.
- `backend/app/schemas/auth.py` — `ProfileUpdateRequest.theme` validator relaxed to slug **format**
  only (malformed → 422).
- `backend/app/services/users.py` — `update_profile` now validates `theme` DB-side against bundled ∪
  custom slugs (unknown → 400), so a **custom slug is a valid per-user preference**.
- `backend/alembic/versions/0051_custom_themes.py` (new) — create/drop `custom_themes` (real down).
- `backend/openapi.json` — regenerated.
- `backend/tests/test_custom_themes.py` (new); `backend/tests/test_account_profile.py` — the
  unknown-theme case is now **400** (service check) with a new **422** malformed-slug case.

Frontend:
- `frontend/src/lib/theme/index.ts` — runtime custom-theme registry: `registerCustomTheme`,
  `allThemes`; `getTheme` now checks bundled ∪ custom (so `applyTheme`/`renderThemeCss` work for
  custom themes unchanged).
- `frontend/src/lib/viz/theme.ts` — `resolveThemeById` uses `getTheme` (custom themes restyle charts).
- `frontend/src/lib/theme/store.ts` — `customThemeOptions` + `allThemeOptions` (merged); `ThemeApi`
  interface; `ensureThemeLoaded(api,id)` (fetch+register an unresolved custom theme); `loadCustomThemes(api,persistedId)`
  (fetch list, merge, and resolve+apply the wanted theme when it's custom). Best-effort.
- `frontend/src/api/client.ts` — `listThemes`/`getTheme`/`uploadTheme`/`deleteTheme` + types.
- `frontend/src/App.svelte` — `loadCustomThemes(client, me.theme)` after `/auth/me` + reconcile.
- `frontend/src/pages/ProfilePage.svelte` — picker driven by `$allThemeOptions`; `ensureThemeLoaded`
  before `setTheme` so a custom pick applies live.
- `frontend/src/pages/AdminPage.svelte` — new **Themes** admin tab: paste YAML → upload/replace,
  list with swatches + delete, shows readability warnings; re-merges into the live picker.
- `frontend/src/lib/theme/store.test.ts` — merge + live-apply + boot-persisted + best-effort tests.

Docs: `docs/runbooks/theming.md` (new); `PROGRESS.md` (P4 entry); this handoff.

## Validation semantics (what rejects vs warns)
- **400 reject**: malformed YAML; top-level not a mapping; missing/empty `id`/`name`/`mode`; `mode`
  not light|dark; `id` not a slug or colliding with a bundled id; missing required token role;
  missing/empty `graph.categorical`; an unresolvable `palette.<key>` ref.
- **Accepted + warnings**: a `graph.categorical` palette that fails the lightness/chroma/contrast/CVD
  readability checks. Warnings are returned in the `POST` response and shown in the admin UI.
- **403**: non-admin write. **404**: get/delete a missing slug.

## How a custom theme reaches the live picker + restyle
Boot: after `/auth/me`, `App.svelte` calls `loadCustomThemes(client, me.theme)` → `GET /themes`
populates `customThemeOptions` (merged into `allThemeOptions` the picker reads); if the wanted theme
(localStorage cache → server `theme`) is custom, its resolved object is fetched (`GET /themes/{slug}`),
`registerCustomTheme`d, and applied. Picking one in Profile calls `ensureThemeLoaded` then `setTheme`
— identical `renderThemeCss` (GUI tokens) + `activeVizTheme`/`resolveThemeById` (ECharts + Cytoscape)
path as a bundled theme, so charts/network restyle live with no reload. Admin upload/delete
re-runs `loadCustomThemes` so the change shows immediately.

## Verification
- Full backend suite: **877 passed** (`docker compose exec -T api python -m pytest backend/tests`).
- Migration parity: **green** (`make test-migrations`, 4 passed, autogenerate clean).
- `ruff check` + `ruff format --check backend agent`: **clean**. `backend/openapi.json` regenerated.
- `make frontend-check`: **green** — 153 tests pass (1 skip), production build OK.

## Deviations / notes
- **Profile theme validation moved from pydantic (422) to the service layer (400)** so a custom-theme
  slug is a valid preference (pydantic can't reach the DB). A malformed slug is still 422. The
  existing test was updated accordingly.
- **Python CVD/readability check is a best-effort port** (advisory warnings only). The bundled
  build-time validator (`dataviz/scripts/validate_palette.js`, gated by the theme tests) remains
  authoritative for shipped themes, as the brief allows.
- Custom themes require the **full token role set** (a partial theme is rejected on a missing role,
  per the brief); only presentational `graph` keys are defaulted. Copying a bundled YAML is the
  intended starting point (documented in the runbook).
- No in-app visual editor (explicitly out of scope / a future item) — upload-YAML + pick only.

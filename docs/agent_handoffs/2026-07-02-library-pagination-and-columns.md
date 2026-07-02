# Handoff: D18 library pagination + D32 shelves/racks columns (2026-07-02)

## Task name
D18 (server-controlled Library pagination with per-user + global page limits) and D32
(Shelves/Racks columns in the Library list).

## Files changed

**Backend — pagination infra (D18):**
- `backend/app/core/config.py` — new `default_papers_per_page` (100) + `max_papers_per_page` (500)
  Settings defaults.
- `backend/app/models/user.py` — new nullable `papers_per_page` column.
- `backend/app/models/app_config.py` — NEW `AppConfig` settings singleton
  (`max_papers_per_page`, server default 500), mirrors the `AIConfig` pattern.
- `backend/app/models/__init__.py` — register `AppConfig`.
- `backend/app/services/app_config.py` — NEW; `effective_max_papers_per_page(db)` +
  `update_max_papers_per_page(...)`, with the same table-presence guard as `ai_config`.
- `backend/alembic/versions/0041_papers_per_page.py` — NEW migration (users.papers_per_page +
  app_config table) with a real downgrade.
- `backend/app/schemas/auth.py` — `ProfileUpdateRequest.papers_per_page` (ge=1, nullable).
- `backend/app/services/users.py` — `papers_per_page` added to `_PROFILE_FIELDS`.
- `backend/app/api/v1/endpoints/auth.py` — `/auth/me` profile payload includes `papers_per_page`.
- `backend/app/api/v1/endpoints/admin.py` — NEW `GET/PATCH /admin/app-config` (owner/admin).

**Backend — works endpoint (D18 + D32):**
- `backend/app/api/v1/endpoints/works.py` —
  - `PaginatedWorks` envelope; `list_works` now `response_model=PaginatedWorks`, `page`/`per_page`
    params replace `limit`, effective size = override|user-pref|default clamped to the global max,
    `total` via `COUNT(*)` over `stmt.subquery()` (respects the query's DISTINCT), page clamped.
  - `WorkShelfRef`/`WorkRackRef` nested models + `shelves`/`racks` on `WorkRead`.
  - `_batch_shelf_rack_refs(...)` — 2 grouped, SEE-filtered queries per page (O(1), not O(n)).

**Frontend:**
- `frontend/src/api/client.ts` — `listWorks` returns `PaginatedWorks`; `WorkQuery.page/perPage`;
  `Work.shelves/racks` (+ `WorkRef`); `CurrentUser.papers_per_page`; `AppConfig`;
  `updateProfile` carries `papers_per_page`; new `getAppConfig`/`updateAppConfig`.
- `frontend/src/pages/LibraryPage.svelte` — consumes the envelope, page state + prev/next +
  page dropdown + go-to-page input; filter/sort/search reset to page 1 (`reload()`); semantic +
  graph-scope fetches request a full page (`perPage: 500`).
- `frontend/src/components/PaperTable.svelte` — render shelves/racks columns (comma-joined).
- `frontend/src/lib/columns.ts` — `'shelves'`/`'racks'` ColumnIds, default off (opt-in).
- `frontend/src/pages/ProfilePage.svelte` — "Papers per page" numeric field (blank = reset).
- `frontend/src/pages/AdminPage.svelte` — new "Settings" tab with "Global max papers per page".
- `frontend/src/pages/InsightsPage.svelte`, `ShelvesPage.svelte` — read `.items` from the envelope.

**Tests:**
- Backend: NEW `backend/tests/test_library_pagination.py` (15 tests). Updated ~10 existing files
  that read `GET /works` as a list to read `.json()["items"]`; `test_m1_core_library.py` direct
  `list_works(...)` callers now pass `page=1`/`per_page=` and read `.items`.
- Frontend: extended `client.additional.test.ts` (page/per_page mapping + app-config calls),
  `AdminPage.test.ts` (Settings-tab save), and fixed `CurrentUser` literals + `listWorks` mocks in
  `session.test.ts`, `PdfReader.test.ts`, `LibraryPage.savedfilters.test.ts`,
  `InsightsPage.scopes.test.ts`, `AdminPage.test.ts`.

## Assumptions made
- Backend D18 + D32 are one commit (the D32 code lives inside `works.py`/`list_works`, which is
  also the pagination reshape — not cleanly separable at the file level).
- Semantic-search and graph-scope `listWorks` fetches request `perPage: 500` so a user's small
  page-size preference doesn't silently truncate those non-paginated flows (old behaviour was a
  fixed 100 cap).
- `AppConfig.max_papers_per_page` server default hardcoded to "500" (matches the spec) rather than
  interpolating the Settings value, to keep migration parity trivial.

## Tests added or skipped
- Added: backend pagination/profile/admin/D32-SEE-filter suite; frontend client + AdminPage tests.
- Fast backend tier: 508 passed / 167 deselected (slow). Migration parity: 4 passed (Postgres).
- Frontend: 78 passed / 1 skipped; vite build green. Did NOT run the slow suite (per test tiers).

## Security implications
- Shelves/racks columns are SEE-filtered via `access.visible_shelves_query`/`visible_racks_query`
  (batched), so a caller never sees the name of a shelf/rack they cannot access — covered by
  `test_shelves_racks_are_see_filtered`.
- `papers_per_page` is a self-service profile field (ge=1); the global max is owner/admin-only
  (`require_admin`), audited as `app.config_changed`.

## Next recommended task
Consider a per-page-size selector in the Library UI (currently only via the profile preference),
and a Postgres-backed query-count assertion for the D32 batching if a query-counter helper is added.

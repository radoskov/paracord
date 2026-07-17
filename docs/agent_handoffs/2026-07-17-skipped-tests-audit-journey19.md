# Handoff: skipped-test audit, journey 19 fix, e2e shared-state serialization

## Files changed

- `e2e/tests/19-identifier-import.spec.ts` — the Import page gained method tabs and the
  identifier form only renders on the "Identifier" tab; the submit button is now "Import
  directly" (the form's default submit is "Preview & choose"). The spec clicks the tab first.
  Verified live against arXiv with `E2E_ONLINE=1`; the gate stays (offline CI determinism).
- `e2e/tests/13-pagination.spec.ts` — now hosts journeys 13 AND 23 under
  `test.describe.configure({ mode: 'serial' })`; both mutate the shared e2e account's
  `papers_per_page`. `23-profile-page-size.spec.ts` deleted (moved verbatim).
- `e2e/tests/29-themes.spec.ts` (new) — hosts journeys 29/30/31/32 serially; all four mutate the
  shared account's server-persisted theme and assert the `latte-warm` boot default. The four
  original single-journey files deleted (moved verbatim).
- `e2e/tests/future/`, `frontend/src/future/` — placeholder specs deleted; every acceptance
  target has shipped and is covered by real tests. Mapping documented in
  `docs/testing/EXT_TEST_BATTERY.md` (+ status note in `ADDITIONAL_TEST_BATTERY.md`).
- `docker-compose.yml` — `./frontend/nginx.conf:/app/frontend/nginx.conf:ro` on the api service
  so the safety CSP/header test runs locally instead of skipping. NOTE: the next
  `docker compose up -d` recreates the api container to pick this up (entrypoint migrates —
  DB is at head, harmless).
- `frontend/src/pages/VisualizationsPage.svelte` — dead `.range` CSS removed (markup replaced by
  dataZoom sliders in UX batch 3; every build warned "Unused CSS selector").
- `frontend/svelte.config.js` (new) — empty explicit config; silences the per-run
  "no Svelte config found … using default configuration" info line without changing behavior.

## Assumptions made

- Remaining intentional skip: Journey 19 skips without `E2E_ONLINE=1` (external network). The
  lenient in-test skips in journeys 9/10 (GROBID slow / pdf.js text layer) stay — they pass in a
  healthy stack and only skip-with-reason instead of flaking when the environment degrades.
- Serial mode per family is preferred over per-test user accounts: the browser session
  (storageState) is the single `e2e_admin`, so distinct users would need sign-in plumbing per
  spec for little gain at this scale.

## Tests added or skipped

- No coverage lost: the merged files carry the journeys verbatim; the deleted placeholders never
  executed. Verified green: `make ready-full` (1227+73+4 backend, 300/300 frontend, 0 skips),
  `make test-safety` (161 passed, 0 skips), `E2E_ONLINE=1 make e2e` twice consecutively:
  36/36 passed, 0 skipped, 0 flaky, ~18 s.

## Security implications

- The nginx.conf mount is read-only and only exposes an already-committed config file to the
  api container; it ENABLES the CSP/security-header assertions locally.

## Next recommended task

- If new journeys ever mutate shared per-user or global state (preferences, app-config, theme),
  either add them to the owning serial family file or give them dedicated state — journey 17
  owns the global app-config clamp today and is the only writer.
- Do not run `make ready-full`/`frontend-*` while `make e2e` is running: the container npm build
  rewrites `themes.generated.ts` (prebuild) on the bind mount and invalidates the live dev
  server's optimize-dep cache — the suite then fails from global-setup sign-in onward. Clear
  `frontend/node_modules/.vite` + restart the frontend container after any such run.

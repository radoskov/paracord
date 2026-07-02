# Handoff: D36 — expand E2E journeys + wire Playwright into CI (2026-07-02)

## Task name
D36 — add a Playwright CI job that actually runs the browser journeys, and expand the `e2e/` suite
to cover the audit gaps + recent UI (pagination, annotate, export, duplicates review, admin
settings, jobs-health, identifier import).

## Commits (all on `main`, not pushed)
1. `e2e: fix helpers for paginated works envelope; raise rate limits in provisioning`
2. `e2e: annotate, export, duplicates-review and identifier-import journeys`
3. `e2e: pagination, admin-settings and jobs-health journeys`
4. `ci: run Playwright E2E journeys with report artifacts`
5. (docs) this handoff + PROGRESS

## Files changed
- `scripts/ensure_e2e_user.py` — after minting `e2e_admin`, now also raises the D1 request
  rate-limit ceilings (`update_rate_limits`, 100k/min per-client + global) so the browser suite
  isn't 429'd mid-run. The out-of-the-box ceilings (60/client, 300/global per minute) throttled the
  serial suite (the sign-in POST is bucketed per client IP). Idempotent.
- `e2e/helpers.ts` — the `/works` list endpoint now returns a **paginated envelope**
  (`{ items, total, page, pages, per_page }`, D18), not a bare array. Added `fetchWorkRows()`
  (reads `.items`, `per_page=500`) and routed `apiDeleteWorksByTitle` /
  `apiDeleteWorksByTitleContains` / `apiFindWorkByTitleContains` through it — this was silently
  breaking every mutating journey's cleanup. Added `apiSetPapersPerPage()` (PATCH `/auth/me`).
- `e2e/tests/13-pagination.spec.ts` — NEW. Seeds 6 papers, sets per-page=2 (via API), drives the
  Library pager: page indicator (3 pages), prev/next disabled states, page dropdown, go-to-page
  number input; then per-page=10 collapses to a single page (pager gone).
- `e2e/tests/14-annotate.spec.ts` — NEW. Attach PDF (reuses journey-8 setup) → reader Notes tab →
  add a note → assert it renders → reload + reopen reader → assert it persisted.
- `e2e/tests/15-export.spec.ts` — NEW. Select a paper → batch export dialog → Preview BibTeX
  (asserts `@` + title) and a Styled/CSL citation (asserts title). Preview is a `<textarea>`, so it
  asserts on `.toHaveValue`, not text content.
- `e2e/tests/16-duplicates-review.spec.ts` — NEW. Creates two near-duplicate papers (shared unique
  first token = fuzzy-title blocking key, differ by one trailing char), runs the UI scan, sees the
  candidate, resolves it with "Keep separate" (mark-not-duplicate) and asserts it leaves the open
  list. NB the UI "Scan now" runs an **inline** full-library scan (the client sends no `background`
  flag), so the candidate is available synchronously.
- `e2e/tests/17-admin-settings.spec.ts` — NEW. Admin → Settings → change "Global max papers per
  page", save, reload, assert it persisted; restores the original. Waits for the async app-config
  load before typing (else the late load clobbers the input) and types char-by-char (see bug below).
- `e2e/tests/18-jobs-health.spec.ts` — NEW. Jobs tab renders the queue-health semaphore
  (`data-testid="queue-health"`, D7) reporting reachable/healthy.
- `e2e/tests/19-identifier-import.spec.ts` — NEW. Import by arXiv id from the Import tab. **Gated
  behind `E2E_ONLINE`** (needs external network to arXiv); self-skips otherwise so CI stays
  deterministic. Verified passing locally with `E2E_ONLINE=1`.
- `.github/workflows/ci.yml` — NEW `e2e` job: `cp .env.example .env` → `docker compose up -d --build
  postgres redis api worker frontend` (GROBID/Ollama stay down, profile-gated) → wait for API +
  frontend → bootstrap owner (`admin`/`paperracks`, piped, tolerates already-exists) → provision
  `e2e_admin` → `npm ci` + `npx playwright install --with-deps chromium` → `npx playwright test` →
  upload `playwright-report/` + `test-results/` on failure.

## Local run result (dev stack up; GROBID/Ollama down)
`cd e2e && npx playwright test`: **17 passed, 2 skipped, 0 failed**, stable across repeated full
parallel runs. Skips: journey 9 (GROBID extraction — profile-gated) and journey 19 (needs
`E2E_ONLINE`). No `data-testid`s needed adding (queue-health already had one; everything else uses
existing roles/labels).

## Assumptions made
- Fresh CI DB has no owner, so the CI job bootstraps `admin`/`paperracks` before provisioning the
  test admin. `getpass` reads piped stdin non-interactively (standard behaviour).
- Bumping the rate limits in E2E provisioning is acceptable for the test environment; it only
  touches the app-config singleton in the dev/test DB.

## Bug found (NOT fixed — out of scope for this task; flagged for follow-up)
The **"Papers per page"** field on `ProfilePage.svelte` (and the identical `type="number"`
`bind:value` pattern on Admin → Settings "Global max papers per page") is broken: the number input
coerces the bound value to a **number**, but `parsedPerPage` calls `.trim()` on it
(`papersPerPage.trim()`), throwing `TypeError: …trim is not a function` on every keystroke. For a
real user, editing the field errors out and Save never enables / saves the stale value. Journey 13
therefore sets the per-page preference via the API and drives only the Library pager in the UI; the
Profile form itself is left uncovered pending a fix. Suggested fix: `String(papersPerPage ?? '')`
before `.trim()` in ProfilePage; the Admin field survives because its save path uses
`Math.trunc(Number(...))` rather than `.trim()`, but it is fragile to the same coercion.

## Tests added or skipped
7 new journeys (13–19). 19 skips unless `E2E_ONLINE`; 9 skips without the GROBID profile.

## Security implications
None new. The rate-limit ceilings are raised only in the E2E/dev provisioning path. The SEC-B5 guard
on `ensure_e2e_user` is still honoured (satisfied by `PARACORD_ENV=development` in the api container).

## Next recommended task
Fix the ProfilePage/Admin number-field `.trim()` coercion bug above, then extend journey 13 to drive
the Profile "Papers per page" form directly.

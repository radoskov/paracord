# Handoff — E2E user-journey hardening + async-UX bug fixes (2026-07-03)

Expanded the Playwright suite to cover the everyday organisation/visualisation workflows the owner
named, and fixed two real bugs the journeys exposed. Committed on `main` (NOT pushed).

## Commits (on `main`)

- `7d8c97e` — `frontend: refresh duplicate candidates after the async full-library scan`
- `c3f01fa` — `e2e: re-run lexical search until the eventually-consistent BM25 index catches up`
- `1407232` — `e2e: add rack lifecycle, shelf organisation and tag journeys`
- `b87e802` — `e2e: add profile page-size and visualizations/citation-summary journeys`
- `48bebfd` — `test: cover reader SEE filter on the citation-neighborhood endpoint`
- (this docs commit updates `PROGRESS.md` + adds this note)

## Baseline triage (before adding anything)

The starting suite was **not** green: 4 journeys failed. Root causes:

- **J8 attach-reader / J10 reader-search** — environmental, not a code bug. The frontend dev server
  served `504 (Outdated Optimize Dep)` for `pdfjs-dist`; the reader never loaded the PDF so
  `numPages` stayed 0 and the pager showed `1 / ?`. Cleared by restarting the `frontend` container
  (stale Vite optimize cache). No code change.
- **J4 lexical search** — the BM25 index is eventually consistent on Postgres (D13a: a search right
  after creating a paper serves the pre-edit index and enqueues a background rebuild; the new paper
  becomes searchable ~2 s later). The journey searched once and never re-queried. Fixed the journey
  (see below); product behaviour left as designed.
- **J16 duplicates-review** — a real UX regression (fixed, see below).

## Bugs found

1. **Duplicate scan UI never showed results (FIXED).** A full-library scan is always queued to the
   worker (`D15`, `duplicates.py` returns `queued=True, job_id=...`, `scanned_works=0`). The
   `DuplicatesPage.scan()` handler fetched the candidate list *immediately* after enqueue — before
   the worker ran — so "Scan now" reported `0 candidates` and showed nothing even when duplicates
   existed. The TS `DuplicateScanResult` type didn't even expose `queued`/`job_id`.
   - Fix (`frontend/src/pages/DuplicatesPage.svelte`, `frontend/src/api/client.ts`): added
     `queued`/`job_id` to the type; `scan()` now polls the jobs list for the scan job's terminal
     state (bounded, ~60 s ceiling, degrades if queue introspection is unavailable) before reloading
     candidates. Message shows "Scan running…" then the final count.
   - Regression coverage: journey 16 (was failing, now passes).

2. **Lexical search journey was racy against the async BM25 rebuild (FIXED in the test).** Not a
   product bug — D13a is intentional. `04-search.spec.ts` now re-runs the search via Playwright
   `expect(...).toPass()` (a natural user action) until the freshly created paper is indexed. No
   fixed sleep.

## Filed (not fixed — feature gaps, out of scope for this task)

- **No rename UI for shelves, racks, or tags.** The task asked to cover "rename" for all three, but
  there is no rename control in `ShelvesPage`/`RacksPage`/`TagsPage` and no `PATCH name` path exposed
  in the UI. Journeys cover create/organise/delete instead. Adding rename is a small feature, not a
  bug — recommend a follow-up (`updateShelf/updateRack` already accept a name server-side; tags have
  no update or delete endpoint at all).
- **Tags cannot be deleted (no API or UI).** Journey 22 leaves its (uniquely-named) tag behind;
  cleanup isn't possible. A `DELETE /tags/{id}` + UI would let the suite stay fully idempotent.
- **Applied tags aren't listed on the paper** ("Currently-applied tags aren't listed yet." hint in
  `WorkDetail`). Journey 22 asserts via the apply/remove toasts instead.

## New E2E journeys

- **20 rack lifecycle** — create a rack, add two shelves, remove one, delete the rack while KEEPING
  its shelves (dismiss the "also delete shelves" confirm); asserts both shelves survive.
- **21 shelf organisation** — one paper on two shelves, remove it from one, confirm it stays on the
  other (never loose); verified from the paper's own "Organization" panel in the Library.
- **22 tags** — create a tag, apply it to a paper, remove it (from the paper detail).
- **23 profile page-size** — set "Papers per page" via the Profile *form* (the field the d36 handoff
  flagged as buggy — it now saves), confirm it persists across reload AND resizes the Library pager.
- **24 visualizations + citation summary** — two tests: every registered viz view builds a payload
  for a seeded library without an error (temporal map also exercises both axis dropdowns); the
  Citation summary tab renders all six analytics blocks. Uses the existing `data-testid`s.

Helpers added to `e2e/helpers.ts`: `apiCreateShelf`, `apiCreateRack`, `apiAddWorkToShelf`.

## Backend test

- `test_endpoint_neighborhood_enforces_reader_see_filter` (`test_citation_graph.py`) — endpoint-level
  companion to the existing service tests: a reader gets 404 for a focus paper on a private shelf,
  and a hidden neighbor (resolved by shared DOI) is clamped out of the reader's payload while the
  owner still sees it. The viz `/viz/*` and `/citations/summary` SEE filters were already covered at
  both service and endpoint level; the neighborhood endpoint only had an "owner" test.

## Assumptions

- The mostly-single-user / few-LAN-users scale assumption holds: the D13a lexical staleness and the
  queued-scan latency are acceptable product behaviour; the fixes target UX/robustness, not the
  async design.
- `data-testid`s on the viz/summary pages were already present; no new ones were needed.

## Verification (docker stack up; GROBID/Ollama profile-gated)

- E2E: **23 passed, 2 skipped (J9 GROBID, J19 online arXiv), 0 failed**
  (`cd e2e && npx playwright test`).
- Backend full suite: `docker compose exec -T api python -m pytest backend/tests -q` — green.
- `make frontend-check` — green.
- `ruff check backend agent && ruff format --check backend agent` — clean.
- No API surface changed, so `backend/openapi.json` was not regenerated.

## Next recommended task

Add shelf/rack/tag rename UI (+ tag delete) so the "rename/remove" journeys can be completed, then
extend journeys 20–22 to cover them.

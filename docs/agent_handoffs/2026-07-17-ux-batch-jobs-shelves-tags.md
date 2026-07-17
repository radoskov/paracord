# Handoff: UX batch — jobs visibility, per-item shelves, descriptions, title tags

## Files changed

- `frontend/src/lib/selection.ts` + `InsightsPage.svelte` + `ImportPage.svelte` —
  `pendingIdentifierImport`: an Insights external-node click pushes its DOI; ImportPage consumes
  it once (Identifier sub-tab + prefilled input). Replaces the old jump-to-Library-search.
- `backend/app/workers/supervisor.py` — `rq worker --results-ttl 86400` (finished jobs stay a
  day; per-enqueue result_ttl values still override). `jobs.py` limit cap 100→500;
  `JobsPage.svelte` requests 200.
- `backend/app/services/batch_import.py` / `import_staging.py` / `endpoints/imports.py` —
  per-item `target_shelf_id` on staging decisions and citation drafts; falls back to the global
  shelf, else default placement. ACL unchanged (librarian+ via add_work_to_shelf_checked).
- `frontend/src/pages/ImportPage.svelte` — per-row shelf select (visible when a row is set to
  create), green "extracted ✓" / muted busy statuses, "N / M processed" extraction progress bar,
  "Creating…" busy label. `DraftReview.svelte` — per-row Shelf select in the fields grid.
- `frontend/src/pages/ShelvesPage.svelte` / `RacksPage.svelte` — description input on create,
  "Save description" editor on the selected item, description shown under the detail heading.
  (Tags already had description UI; the backend supported all three all along.)
- `frontend/src/components/WorkDetail.svelte` — applied-tag chips under the title;
  `WorkTagRef`/`WorkTagRead` (works.py) + `AppliedTag` (client.ts) carry `description` for the
  chip tooltip. `backend/openapi.json` regenerated.

## Assumptions made

- Import-to-shelf keeps its librarian+ ACL; a contributor's per-item shelf pick fails that item
  with the existing permission warning rather than a new rule.
- The "progress bar on commit" ask is interpreted as extraction progress (the commit POST itself
  is a single fast call; extraction is the long, job-backed phase) + a busy commit button.

## Tests added or skipped

- +1 backend (per-item shelf override vs. batch shelf), +1 frontend (identifier prefill),
  ImportPage suite updated. Battery green: backend 1265, frontend 325, safety 161,
  `E2E_ONLINE=1` e2e 37/37, 0 flaky. Live-verified: title tag chips, green/pending statuses +
  progress bar mid-extraction (screenshots in session).

## Security implications

- None new: per-item shelf reuses the existing checked helper; jobs TTL/limit only affect
  introspection volume.

## Next recommended task

- DraftReview's per-row shelf select duplicates the shelf list per row; if drafts grow to
  hundreds, a shared datalist or a row-expand pattern would be lighter.

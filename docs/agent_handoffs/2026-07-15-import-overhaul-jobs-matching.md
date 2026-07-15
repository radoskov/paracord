# Handoff — 2026-07-15: Import tab overhaul, jobs history, matching auto-accept

Owner request (verbatim themes): split the Import tab into sub-tabs with remembered selection;
readable batch-citation preview; progress + gradual + failure-resistant citation import with a
cancel button and a bigger "time budget"; BibTeX preview-&-import; job re-runs vanishing from the
Jobs tab + inconsistent counts; the restart-looping failing chunk job (sqlalche.me/e/20/9h9h);
two-level fuzzy-match acceptance + dead "Confirm match" button; two-column admin settings;
"frequently cited but missing" listing papers already in the library.

## What changed (one commit per item)

| Commit | Area |
| --- | --- |
| `9744046` | Import page sub-tabs (PDF / Citations / Identifier / Folder / External data), sessionStorage-remembered; pendingImportText jumps to Citations |
| `65901b7` | Two-row draft preview in batch citation import (title = full first row) |
| `838e4e2` | Chunked lookup (4 lines/request) + progress bar + Cancel search + per-chunk failure fallback + mid-search commit; `web_find.total_budget` 25→120 s |
| `a60c457` | `POST /imports/bibtex/preview`; shared `frontend/src/components/DraftReview.svelte`; batch commit `engine="bibtex"` with arxiv/work_type/abstract passthrough; "in library" flag |
| `b615420` | Unique per-run RQ job ids; `paracord:latest-job:{key}` Redis pointer keeps live coalescing (legacy bare-key probe for pre-deploy jobs); JobsPage "all" = Σ state counts |
| `eae2239` | Chunking sanitizes NUL/control chars + clamps section labels to 255; chunk job failure sets the paper's processing_error badge |
| `0bcd4ef` | `reference_matching.auto_accept_threshold` (yaml, default 100): fuzzy score ≥ threshold auto-confirms without DOI/arXiv; inline confirm/reject feedback on reference rows |
| `0875075` | Citation summary: likely-local suggestion = held, not missing (rejected stays missing) |
| `897da47` | Admin masonry two-column for Settings / Find-on-web / Backup |

## Notes & caveats for the next agent

- **Chunk-job 9h9h could not be reproduced**: the failing work
  (`Taxonomy-Superimposed Graph Mining`, `e0d7544f-…`) was already deleted from the live DB when I
  investigated (12 works remain, no likely_match refs either). 9h9h = SQLAlchemy `DataError`; the
  fix covers the two DataError vectors of that INSERT (NUL bytes in text — `str.split()` does not
  remove `\x00` — and GROBID `<head>` labels longer than the `section` column's 255 chars). If it
  recurs, the paper now shows a `chunk: …` processing-error badge and the traceback is in the
  failed job entry.
- **"Confirm match" button**: backend + wiring verified correct and test-covered
  (`test_link_action_confirms_and_locks`). Could not reproduce a no-op; most plausible causes were
  a silently-disabled button (shared `loading` flag) or the error message rendering at the top of
  a long modal. Added per-row "Confirming…" + inline error so it can no longer look like a no-op.
  With the new auto-accept default, 100%-scoring matches confirm themselves anyway.
- **Job-id scheme**: anything that greps job ids must use the `{key}-{8-hex}` form now; fixed-name
  singletons (`bm25-rebuild`, `backup-export`, …) are also suffixed. All consumers were inside
  `backend/app/workers/queue.py`; target resolution uses `job.args`, not ids.
- **Old references data**: the live DB still has `resolution_status='resolved'` rows (pre-batch-12
  vocabulary). A library-wide rescan (Admin → Settings → "Rescan whole library now") rewrites them
  with current statuses and applies the new auto-accept threshold.
- **Deploys**: worker restarted (loads new jobs/chunking code); api hot-reloads; frontend `.vite`
  cache cleared + dev server restarted after the in-container `npm run build` (the known 504
  Outdated-Optimize-Dep gotcha). `backend/openapi.json` regenerated. No new migrations.
- Verified: fast backend tier 889 passed; frontend 293 passed + build; targeted suites for queue,
  chunking, reference matching, citation summary, bibtex/batch import.

---

# Addendum — same day, batch 2 (acceptance policy, unified imports, previews, summary columns)

| Commit | Area |
| --- | --- |
| `dfd397b` | DraftReview badge tooltips (what "matched" means + what commit does) |
| `6b6ac76` | Find & Import + Direct import/Create paper for refs AND citing papers; `POST /works/from-citing/{id}` (works without DOI, enriches when identifier present, sets `resolved_work_id`, default-shelf placement) |
| `3c1920b` | Panel-header badges (likely / in library / external) on References + Citing papers |
| `87b1811` | Identifier import Preview & choose (reuses `/citations/external-preview` + DraftReview; commit `engine="identifier"`) |
| `0911bb0` | Two-level acceptance policy — see below |
| `43691e9` | Citation summary: admin cap (default 100) + scrollable columns with to-top |

## Acceptance policy (migration 0070)

- yaml (`reference_matching`): `auto_accept_threshold` (now the DEFAULT for the admin threshold,
  90), `min_auto_accept_threshold` (floor, 90, yaml-only), `high_confidence_threshold` (100,
  yaml-only, shown read-only in Admin).
- AppConfig columns: `fuzzy_accept_threshold` (NULL → yaml default; clamped to floor on read,
  validated on write), `use_high_confidence_auto_accept` (NULL → True). The existing
  `use_fuzzy_match_as_confirmed` column now backs the "Use fuzzy auto-accept" checkbox.
- `AcceptPolicy` dataclass lives in `reference_matching.py`; `effective_accept_policy(db)` in
  `app_config.py`. `resolve_and_persist`/`run_matching_for_references`/
  `rescan_references_for_new_work` accept `accept_policy=`; the legacy `fuzzy_as_confirmed` bool
  still works (falls back to legacy-toggle + default high-confidence) for tests/back-compat.
- Defaults preserve batch-1 behaviour: fuzzy auto-accept OFF, high-confidence ON at 100.

## Notes

- Live DB migrated to **0071** via `docker compose exec api alembic -c backend/alembic.ini upgrade
  head` (exec, not run — the api hot-reloads code but never re-runs its migrate-on-start
  entrypoint, so model changes MUST be followed by a manual upgrade or the running API breaks).
- `citation_summary_item_cap` default is 100 (deliberately > the old 15) — the UI folds columns
  into ~15-item scroll windows (`.col-scroll`, `max-height: 26rem`), so "show everything" doesn't
  clutter the page. The `/citations/summary` `limit` param is now optional (admin cap when
  omitted, `le=500`).
- `CitingPaperRead`/`CitingPaper` now expose `arxiv_id`.
- Worker + frontend restarted after the batch; `.vite` cache cleared (504 gotcha).

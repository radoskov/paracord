# Handoff: degraded badge + plain-text body, scope-job timeout fix, import preview links

## Files changed

- `backend/app/services/grobid_client.py` — the degraded fallback now also injects a PyMuPDF
  plain-text body (`<body><div type="plain-text-fallback"><head>Full text</head><p>…per page…`)
  into the merged TEI (XML-escaped + control-chars stripped) and stamps `DEGRADED_TEI_MARKER`
  (an XML comment); `is_degraded_tei()` detects it.
- `backend/app/models/file.py` + `alembic/versions/0077_file_extraction_degraded.py` —
  `files.extraction_degraded` bool (server_default false). APPLIED to the live DB.
- `backend/app/services/extraction.py` — `store_parsed_extraction` sets/clears the flag from the
  marker (central: extract job, staging create, staging append all pass through).
- `backend/app/api/v1/endpoints/works.py` — badge loader selects the flag (`degraded` token);
  `WorkFileRead.extraction_degraded`.
- Frontend — Files-panel `degraded extraction` warning badge (tooltip explains what's missing);
  `BADGE_META.degraded` for the library Badges column; `WorkFile.extraction_degraded` type.
- `backend/app/workers/queue.py` — `_enqueue_scope_job` passes `job_timeout=6*3600` (the 900s
  queue default killed whole-library LLM summaries); `is_abort_exception()` helper.
- `backend/app/services/summarization.py` — all four degrade-and-continue `except Exception`
  handlers re-raise aborts (user cancel + RQ timeout); the scope map loop commits per paper when
  running as a job (progress_cb non-None) so finished per-paper summaries survive crash/timeout/
  stop and are reused on re-run.
- `backend/app/api/v1/endpoints/imports.py` — `StagingItemRead.file_id`.
- `frontend/src/pages/ImportPage.svelte` — collision warnings link matching papers (opens in
  Library via `pendingLibraryOpen`); per-row `preview ↗` streams the staged PDF (blob URL, auth
  carried by fetch; loose files are see-able by design).

## Root cause (scope-summary crash)

"Work-horse terminated unexpectedly; waitpid returned None" at 7/85, duration EXACTLY 16:00 =
900s default timeout + RQ kill grace. The timeout exception was swallowed by the LLM
degrade-handler, the loop kept going, RQ hard-killed the horse; the end-of-job commit design
rolled back the 7 finished per-paper summaries.

## Assumptions made

- 6h covers ~150 papers at the observed ~2.3 min/paper; coalescing prevents duplicate stacked
  runs. If libraries grow much larger, consider scaling the timeout by scope size.
- Existing degraded files from before the flag (only open_ease locally) were fixed by
  re-extracting; no data backfill migration (the marker isn't in pre-fix TEIs anyway).

## Tests added or skipped

- +2 grobid client (body injection/escaping; marker), +1 extraction (flag set→cleared, sections
  parse), +2 summarization (timeout aborts instead of degrading; is_abort_exception), ImportPage
  test updated for linked collisions. Full battery green: backend 1262, frontend 324, safety
  161, migrations 4, `E2E_ONLINE=1` e2e 37/37, 0 flaky. Live-verified: badge in Files panel +
  Badges column, "Full text" chunks for open_ease, collision link opens the paper in the
  Library, preview streams the staged PDF.

## Security implications

- Staged-PDF preview relies on the existing loose-file see-ability rule; no new endpoint.
  The 6h job timeout is bounded and coalesced; is_abort_exception adds no surface.

## Next recommended task

- The job list could render scope-job progress ("7/85") more prominently with an ETA, given
  these are now legitimately hours-long runs.

# Handoff — Feature batch: backup/restore, batch-import rework, jobs (2026-07-13)

On `main`, **not pushed**. Commits: `46910a6` (items 2–4), `c8e14a3` (item 1). Full backend suite
1163 passed / 4 skipped (`-m "not safety"`); frontend 275 + build green. Local api+worker
restarted (bind-mounted dev stack). **Real-data PC:** pull + restart both containers — the worker
must relaunch to get `--with-scheduler` and the new job modules.

## Item 1 — version-tolerant backup/export + owner-only restore (`c8e14a3`)

- `services/backup.py`: export = zip of `tables/<name>.jsonl` (self-describing `{column: value}`
  rows, UUID/datetime ISO-encoded, generic over `Base.metadata.sorted_tables`; excluded:
  `user_sessions`, import-staging tables) + `manifest.json` (format version 1, alembic revision,
  `hash_algorithm: sha256` — deterministic, no key needs to travel) + optional
  `pdfs/<sha256>.pdf`. Archives live in `<managed_library_root>/../backups`.
- Restore tolerance: column-name intersection vs the CURRENT schema; new columns backfilled by
  model/DB defaults; deleted columns dropped + counted; renames via the forward-maintained
  `_RENAMES` registry (extend it whenever a migration renames a column!); unknown tables ignored;
  per-row SAVEPOINTs + a same-table retry pass (self-referencing FKs like `works.merged_into_id`);
  merge mode skips existing PKs, replace mode wipes exported tables first (reverse FK order).
- Safety rails: restore is `require_owner`; replace needs `confirm="REPLACE"` (typed in the UI +
  a browser confirm); `GET /admin/backups/{name}/analyze` is the pre-restore dry-run report; on
  replace the pre-restore owner account is re-inserted when the backup brought no owner
  (server-console password reset remains the backstop). All steps audited
  (`backup.created/uploaded/deleted/restore_requested/restored`).
- PDF pairing: after rows land, every restored File row must resolve to a real PDF — from the
  archive or a scanned **import-root alias** (global rule 3: never an arbitrary path); files with
  no match are deleted with their links/locations/segments, papers survive.
- Endpoints: `GET/POST /admin/backups`, `GET .../download`, `DELETE`, `GET .../analyze` (owner),
  `POST .../upload` (owner), `POST .../restore` (owner). Export/restore run as worker jobs
  (`backup-export` / `backup-restore` fixed ids) with inline fallback when Redis is down.
- UI: Admin → Backup tab (create/list/download/delete for admins; upload + compatibility report +
  merge/replace restore controls for the owner).
- Known limits (documented deliberately): restore runs against a LIVE app (single-user scale —
  don't import papers mid-restore); per-user preference YAML files are not archived; pgvector
  columns are off-ORM and excluded (embeddings rebuild via reindex).

## Items 2–4 (`46910a6`)

- **Scheduler root cause:** `rq worker` ran without `--with-scheduler`, so every `Retry`-scheduled
  job sat in the scheduled registry forever (the reported 10+ min pending job; also neutered the
  S6/S7 retries). One flag in `supervisor._worker_command` + test.
- **Batch import:** `commit_staging` only finalizes when no item remains
  pending/extracting/extracted → "Import selected now" works repeatedly during extraction;
  committed items leave the preview (frontend filters them). `GET /staging/{id}` self-heals:
  re-enqueues dead items by deterministic id, extracts one item inline per poll when Redis is
  down, and finalizes wedged batches. Frontend polls live/unbounded (was a dead 60s cap), merges
  new checkbox defaults without clobbering user choices; auto-commit while extracting → 409.
- **Job cancel:** `queue.cancel_job` (queued/scheduled/deferred only; started jobs keep running)
  + `POST /jobs/{id}/cancel` (editor) + a Cancel button on pending rows.
- **Staging job titles:** `_target`/`_resolve_paper_targets` know `staging_item` targets; Jobs tab
  shows the staged paper's parsed title (or filename) + sha256.

## Tests

`test_backup.py` (new, 8: round-trip replace, merge-only-missing, schema-drift tolerance, rename
map, PDF pairing drop/pair, owner re-insert, owner-only + confirmation, upload rejection);
`test_multi_import_staging.py` +4 (partial commits, requeue, inline fallback, auto-409);
supervisor test updated for `--with-scheduler`.

## Next recommended task

Answer-tracking for AUDIT D3 (TLS + permanent agent token) once the owner picks a direction; the
`dedup_key` unique-index migration after the real-data consolidation scan runs clean.

# Handoff — D31 spec-conformance B1–B3 (2026-07-03)

Implemented the first D31 batch (Track B items 1–3). All work committed on `main` (not pushed).

## Commits (on `main`)

- `ccbecda` — `audit: wire missing §7.6 events, add JSONL file sink, paginate events UI`
- `c195bb8` — `ai: persist summary provenance columns (§8.14.2)`
- `a9dcd59` — `annotations: add JSON export format (§8.8.7)`

(A follow-up docs commit updates `PROGRESS.md` + this handoff.)

## B1 — audit-event wiring + file sink + UI pagination

Audit events wired (one `record_event(...)` per site):

| Event | Site |
| --- | --- |
| `shelf.created` / `shelf.modified` | `api/v1/endpoints/shelves.py` `create_shelf` / `update_shelf` |
| `rack.created` / `rack.modified` | `api/v1/endpoints/racks.py` `create_rack` / `update_rack` |
| `paper.metadata_edited` | `api/v1/endpoints/works.py` `update_work` (only when `updates` non-empty) |
| `annotation.created` | `api/v1/endpoints/works.py` `create_work_annotation` |
| `job.started` / `job.completed` / `job.failed` | `workers/jobs.py` `_audited_job` decorator on all 10 real jobs |
| `backup.created` / `restore.completed` | `scripts/record_backup_event.py`, called from Makefile `backup`/`restore` |

- **File sink:** `services/audit.py` `_append_to_file_sink` appends one JSON line per event to
  `Settings.audit_log_path` (new setting, alias `PARACORD_AUDIT_LOG_PATH`, default
  `./storage/audit/audit.jsonl`; also set explicitly on api + worker in `docker-compose.yml`).
  Best-effort/fail-open (never breaks the request or drops the DB row), append mode for concurrent
  writers. `record_event` now sets `id`/`created_at` explicitly so the file line matches the DB row.
- **Tests:** `_audit_file_sink_tmp` autouse fixture in `backend/tests/conftest.py` redirects the sink
  to `tmp_path` so the suite never writes the repo volume. New `backend/tests/test_d31_audit_events.py`
  covers each event + the sink + the backup CLI.
- **UI pagination:** `frontend/src/api/client.ts` `listAuditEvents(limit, offset)` now returns
  `{items, total}`; `frontend/src/pages/EventsPage.svelte` gained offset-based prev/next controls +
  a page indicator (mirrors the library pager). Endpoint already paginated — no backend change.
- **No count-assertion tests broke** — the existing audit tests all filter by `event_type`, so added
  events did not perturb them. Nothing forced.

### Deviation — `annotation.edited`
There is **no annotation edit endpoint** (only `POST` create and `DELETE`). `annotation.edited` has
no wiring site, so it was not emitted. Wiring it would require first adding a PATCH/PUT annotation
endpoint, which is out of scope for this additive task. Flagging as a real behavior question: if
annotation editing is desired, add the endpoint first, then emit `annotation.edited` there.

## B2 — summary provenance columns (§8.14.2)

- Model `backend/app/models/ai.py` `Summary` gained: `provider_requested`, `provider_used`,
  `fallback` (bool, not null, default false), `source_sections` (JSON), `content_hash` (sha256 hex of
  the text), `created_by_user_id` (Uuid), `params` (JSON).
- Migration `backend/alembic/versions/0048_summary_provenance.py` (id 23 chars ≤ 32; chains off head
  `0047_drop_full_ml_ocr_backend`).
- `services/summarization.py` `summarize_work` + `summarize_scope` set the columns on creation and
  accept a new `created_by_user_id` kwarg (passed from `works.py` create_summary and `ai.py`
  create_scope_summary via `actor.id`).
- `SummaryRead` (in `works.py`) surfaces `content_hash`, `created_by_user_id`, `params`; a before-
  validator coerces legacy NULL `source_sections` to `[]`.
- Migration parity + autogenerate-clean tests green on Postgres.

## B3 — annotation JSON export (§8.8.7)

- `api/v1/endpoints/works.py` `export_work_annotations`: format enum `^(markdown|text|json)$`; the
  `json` branch returns `application/json` with shape
  `{"work": {id, title}, "annotations": [{page, type, coordinates, selected_text, note, created_at, author}]}`.
- Tests in `backend/tests/test_annotation_search_export.py` (json export + unknown-format 422).

## Verification

- Full backend suite: green (`docker compose exec -T api python -m pytest backend/tests -q`).
- `make test-migrations` (Postgres): 4 passed.
- `ruff check backend agent` + `ruff format --check backend agent`: clean (run on host — the `agent`
  dir is not mounted in the api container).
- `backend/openapi.json` regenerated + committed (SummaryRead provenance fields + annotation export
  enum).
- Frontend: `npm run test` 88 passed / 1 skipped; production build compiles cleanly (validated with
  an alternate `--outDir` because the checked-out `frontend/dist` is root-owned from a prior build
  and can't be emptied here — a pre-existing environment artifact, unrelated to these changes, that
  makes `make frontend-check`'s build step fail on `EACCES`).

## Not done (later D31 items, untouched)
B4 (search operators §14.2) and B5 (export formats §8.13) remain. `AUDIT.md`, `DISCUSSIONS.md`,
`WORKPLAN_2026-07.md` intentionally not modified.

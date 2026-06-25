# Progress Report

## Current status

**Milestone 0 (foundation) and Milestone 1 (core library) are complete enough for the
single-machine loop; Milestone 2 extraction/enrichment is live; Milestone 4 duplicate review
has started in the backend.**

What works today (real, tested in-container on Python 3.12):

- Containerized build/test/run stack (`docker compose`), auth (bcrypt), revocable sessions,
  owner/editor/reader role authorization, owner-only admin user management, audit logging,
  server-console bootstrap/password-reset, and Alembic migrations for the auth tables.
- Initial M1 backend path: configured server-folder sources, folder PDF scanning, SHA-256
  file registration, File/Location/Work links, import batches, basic work/shelf/rack/tag
  endpoints, and focused service tests.
- Initial M1 frontend path: Dockerized Vite/Svelte service, login, library table, reading
  queue, server-folder import controls, shelf/rack/tag controls, and file preview panel.
- Works can now be filtered by shelf, rack, tag, reading status, and basic metadata in the
  backend and from the frontend toolbar.
- Authenticated PDF streaming exists for configured server-folder file locations, including
  root-escape protection; the frontend file panel can open streamed PDFs in a browser tab.
- Shelves and racks can be archived, work/shelf memberships can be removed, and tag links
  can be removed via backend endpoints and frontend controls.
- **M1 validated end-to-end** (real HTTP API + Postgres + frontend build): login → create
  server-folder source → import PDFs (hash/dedup/PyMuPDF preview) → list/search works →
  add to shelf/rack → stream PDF, with the editor/reader role gate enforced over HTTP and
  re-import dedup confirmed. The Svelte frontend compiles (`vite build`).
- **M2 GROBID extraction, validated end-to-end on real arXiv papers:** real TEI parser
  (`services/tei_parser.py`), provenance-aware persistence (`services/extraction.py`) that
  records MetadataAssertions + References and only promotes canonical title/abstract/DOI
  when the work is not user-confirmed, migration `0004`, a synchronous GROBID client, an
  RQ queue (`app/workers/queue.py`), a `worker` compose service, enqueue-on-import plus a
  `POST /files/{id}/extract` trigger. Verified live: imported the Transformer and ResNet
  PDFs from arXiv → the worker ran GROBID via the `extraction` profile → 90 references and
  abstracts/titles persisted asynchronously. Uses the lightweight `lfoppiano/grobid:0.8.0`
  CRF image (~0.5 GB) rather than the ~12 GB deep-learning image.
- **M2 metadata enrichment (arXiv + Crossref), validated live end-to-end:** identifier-based
  connectors (`services/metadata_enrichment.py`) that record provenance-aware
  MetadataAssertions and promote trusted external fields over GROBID when the work is not
  user-confirmed; arXiv-id-from-filename detection at import; an automatic chain
  (import → GROBID extract → arXiv/Crossref enrich) plus a `POST /works/{id}/enrich`
  trigger; and a review/conflict surface (`GET /works/{id}/metadata`,
  `POST /works/{id}/metadata/select`). Verified live: importing `1706.03762.pdf`
  auto-corrected GROBID's mis-detected title ("Provided proper attribution…") to
  "Attention Is All You Need" via arXiv, with both values kept as assertions and the
  conflict flagged.
- **M2 raw TEI + citation mention persistence:** raw TEI blobs are stored in
  `raw_tei_documents`; TEI body `ref type="bibr"` markers are parsed into
  `CitationMention` rows with section label and before/current/after sentence contexts,
  linked back to the extracted `Reference` and raw TEI source.
- **M2 citation context API:** `GET /works/{work_id}/citation-contexts` returns persisted
  in-text citation contexts with reference metadata.
- **M2 citation context frontend surface:** selecting a work in the Svelte library loads and
  displays extracted citation contexts with marker, section, sentence context, and reference
  metadata.
- **M4 duplicate/version scanner foundation:** `duplicate_candidates` now exists with an
  Alembic migration and ORM model; `services/duplicate_detection.py` generates idempotent
  review candidates for same DOI, same arXiv base ID/version mismatch, fuzzy normalized-title
  matches, exact file hash, and matching text fingerprints.
- **M4 duplicate review API foundation:** `/api/v1/duplicates` lists candidates, triggers a
  scan across all or selected work/file identities, and updates candidate review status
  (`open`/`accepted`/`rejected`/`ignored`) with resolver metadata.
- **M4 duplicate review frontend surface:** the Svelte workspace can list duplicate
  candidates, filter by review status, run a scan, inspect signals, and mark a candidate
  accepted/rejected/ignored.
- **M4 duplicate review backend actions:** review decisions can now merge work candidates
  without deleting source works, link a source work as a `WorkVersion`, mark a file candidate
  as a duplicate copy, keep candidates separate, or ignore them. Resolutions write audit events.
- **M4 duplicate review frontend actions:** the review panel now calls explicit merge,
  link-as-version, mark-duplicate-file, keep-separate, ignore, and reopen flows instead of only
  toggling generic status.
- **M4 multiwork candidate detection:** files with repeated abstract/reference markers or
  long proceedings-like previews are queued as `multiwork_file` candidates for review.
- **M4 multiwork split backend action:** `split_file` accepts user-provided segment ranges and
  creates `FileSegment`, `Work`, and `FileWorkLink` rows with `file_contains_multiple_works`
  warning state.
- **M4 multiwork split frontend controls:** `multiwork_file` candidates can submit line-based
  `Title | start page | end page` segment ranges to the backend split action.

What still does NOT exist yet:

- Citation context graph integration is not implemented yet; the lightweight library panel
  exists, but the full PDF.js reader/reference-panel integration is still pending.
- OpenAlex/Semantic Scholar connectors; Crossref/arXiv title-based (fuzzy) lookup — only
  exact-identifier enrichment is implemented so far.
- Hardened duplicate/version UX, embedded PDF.js reader, citation graph, export, AI summaries,
  topics.

Component note: **Redis has a live consumer** — the `worker` service runs the RQ
`paperracks` queue and processes both GROBID extraction and enrichment jobs.

### Testing

The suite has three layers (run with `make test`):

- **Service/unit tests** — `test_extraction.py`, `test_enrichment.py`, `test_duplicate_detection.py`,
  `test_m1_core_library.py`, `test_auth_service.py`, etc. (SQLite, direct calls).
- **High-level API/flow + security tests** — `test_api_flows.py` (import → organize → search →
  read; metadata review; citation contexts), `test_api_security.py` (RBAC matrix, no-guest,
  auth-required, account-enumeration, audit, path-escape), `test_api_smoke.py`. These run the
  real app via `TestClient` against in-memory SQLite (shared harness in `conftest.py`).
- **Forward-looking tests (skipped) — `test_future_milestones.py`.** These encode the intended
  M3+ contracts and are `@pytest.mark.skip`-ped. **When you implement a milestone, enabling its
  test is part of the Definition of Done:** search `test_future_milestones.py` for the matching
  `ENABLE WHEN` note, remove the skip, and make it green.

Current count: ~75 passing + 8 skipped (forward-looking) backend, 2 agent.

### Start here (next agent)

M1 done; M2 extraction + enrichment pipeline is live and validated. M4 duplicate detection has
the queue table, scanner, review API, backend action semantics, frontend action panel, multiwork
candidate detection, and split-file UI. Continue M2/M4:

1. **Reader context integration**: move the lightweight citation-context panel into the
   eventual PDF.js reader/reference tab.
2. **Duplicate/version hardening**: add better target-work selection for merge/version actions,
   richer candidate labels, and safeguards around repeated split actions.
3. Optional: OpenAlex/Semantic Scholar connectors and title-based Crossref lookup (needs the
   normalized-title similarity guard before promoting), and arXiv/DOI link *ingestion*.

The leftover M0 auth items (login rate limiting, in-app password change) remain
deliberately deferred — hardening, not the product.

## Completed

- Product requirements consolidated into a full implementation specification.
- Server/agent architecture selected.
- No-guest access-control requirement captured.
- Server-local credential recovery requirement captured.
- Teleport workflow captured.
- Work/version/file/file-segment model captured.
- Citation context and local citation graph requirements captured.
- Citation export requirements captured.
- Local AI summary and topic modeling requirements captured.
- Initial backend, agent, frontend, documentation, and operations folder structure created.
- Backend settings now load supported values from server YAML with environment overrides.
- Backend password hashing and verification helpers are implemented with bcrypt.
- Server-console owner bootstrap and password reset scripts now touch the database and write audit events.
- Initial Alembic migration creates `users` and `audit_events`.
- Second Alembic migration creates revocable `user_sessions`.
- `make migrate` applies backend migrations.
- Backend unit tests cover settings loading and security helpers.
- Server-console admin script tests cover first-owner creation, duplicate-owner refusal, and password reset audit logging.
- Minimal login/logout endpoints create and revoke server-side bearer sessions.
- Non-health, non-login API routers now require bearer-token authentication.
- Password reset now revokes active sessions for the target account.
- Auth service tests cover credential validation, token hashing, session revocation, and audit persistence.
- API dependency tests cover valid, missing, and invalid bearer tokens.
- Secrets-handling policy documented and enforced via a secret scanner, pre-commit hook, and CI workflow; hardcoded Postgres dev password removed from compose in favor of `.env`.
- Role-based authorization: `require_roles`/`require_owner` dependencies and owner-only admin endpoints for user management (list/create/role-change/disable) and audit-event access, with `user.created`/`user.role_changed`/`user.disabled` audit events and last-owner protection.
- Login account-enumeration mitigation (constant-time dummy verification on the no-user path) and a startup assertion that no guest role is configured.
- Containerized dev/eval stack (Python 3.12): `backend/Dockerfile` (api server) and `agent/Dockerfile` (client), `docker compose` services for postgres/redis/api/agent with healthchecks and GROBID/Ollama profiles, in-container test/lint, and a CI workflow.
- M1 backend persistence/import slice: `sources`, `import_batches`, M1 file/work/organization
  fields, `shelf_works`, `rack_shelves`, and `tag_links` models plus Alembic migration.
- Configured server-folder sources can be created by alias only; folder import scans a configured
  root, hashes PDFs, creates File/Location/Work/FileWorkLink rows, extracts a PyMuPDF first-page
  text preview when available, deduplicates by SHA-256, and audit-logs import activity.
- Basic backend endpoints exist for sources, folder imports, file metadata, manual work
  create/edit/list/search, shelves, racks, membership, and tags.
- Compose-managed frontend service (`frontend/Dockerfile`) keeps Node dependencies inside
  Docker, with `make frontend-dev` and `make frontend-build` targets.
- M1 frontend workspace renders login, library search/status filters, reading queue,
  server-folder source/import controls, manual work creation, shelves/racks/tags, and a
  file list with first-page preview text.
- Work search now supports shelf/rack/tag filters, and the frontend toolbar exposes them.
- `GET /api/v1/files/{file_id}/stream` streams PDFs from configured server-folder sources
  only, and rejects file locations outside the configured source root.
- M1 CRUD gaps narrowed: archive shelves/racks, remove work-from-shelf and shelf-from-rack
  memberships, and remove tag links.
- Raw TEI storage and citation mention persistence: migration `0005`, `RawTeiDocument`,
  parser support for body bibliography refs, and idempotent persistence of references and
  mentions from GROBID TEI.
- Work-scoped citation context API returns persisted `CitationMention` rows joined to their
  extracted references.
- The frontend library workspace displays citation contexts for the selected work.
- Duplicate candidate storage and scanner foundation: migration `0006`, `DuplicateCandidate`,
  and idempotent candidate generation for DOI/arXiv/fuzzy-title/exact-file/text-fingerprint
  signals.
- Duplicate review API foundation: list/scan/status endpoints under `/api/v1/duplicates`.
- Initial duplicate review frontend panel with scan, status filter, signal display, and
  accept/reject/ignore controls.
- Backend duplicate review actions for merge-work, link-as-version, duplicate-file,
  keep-separate, and ignore decisions.
- Frontend duplicate review actions wired to the backend action API.
- Conservative multiwork-file candidate detection in the duplicate scanner.
- Backend `split_file` action creates segments, works, and contains-links from reviewed ranges.
- Frontend split controls submit segment ranges for `multiwork_file` candidates.

## In progress

- M1 backend API/frontend implementation.
- Local agent protocol stubs.
- LaTeX implementation manual draft.
- Agent task partitioning.
- M0 developer skeleton hardening.

## Not started

- Login rate limiting / failed-login lockout (role-based authorization is now implemented).
- In-app password-change endpoint (server-console reset exists; web change-password + its session revocation still pending).
- Embedded PDF.js reader integration (a lightweight citation-context panel exists; the full reader/reference-tab does not).
- Agent registration and token rotation implementation.
- Reader/PDF.js reference-tab integration; duplicate UX hardening.
- Citation graph materialization implementation.
- Export renderer.
- BERTopic and embedding pipeline.
- Local LLM summarization pipeline.
- Audit-log admin views (and read/export audit *events* — see tech debt).
- End-to-end tests.

## Tech debt and cleanups

Low-severity items found during the audit, not tied to a feature milestone. Address opportunistically.

- Remove or fully wire the dead `guest_access_enabled` setting (`backend/app/core/config.py`). A startup `assert_no_guest_roles` check now enforces that no guest role is present in `security.allowed_roles`.
- Migrate deprecated `datetime.utcnow()` to timezone-aware `datetime.now(timezone.utc)` across `services/auth.py`, `models/*`, `services/users.py`, and `scripts/reset_admin_password.py`. Note: do this together with switching the model `DateTime` columns to `DateTime(timezone=True)` (plus a migration), otherwise naive/aware comparisons in session checks will break.
- Add symlink-escape and `../` traversal test cases to `agent/tests/test_security.py` (the primitive is correct but currently untested).
- Remove or wire the unused agent config flags `follow_symlinks` / `teleport_enabled`.
- Note in `docs/architecture/api_surface.md` and `data_model.md` that they reflect current stubs and defer to `SPECIFICATION.md` §10 / §9.
- Relabel the `SPECIFICATION.md` front-matter Contents as a thematic overview (its numbers do not match the section numbers).

### Data-model divergences from SPECIFICATION.md §9.3 (fix before they are built on)

These cost a migration + re-extraction if M4 (duplicates) / M6 (citation graph) / M7 (topics)
are built on the current shapes, so address the first two before the M4 review workflows harden:

- **Split `works.arxiv_id` into `arxiv_base_id`** (strip the `vN` suffix) and add a **UNIQUE**
  constraint on `doi` and the arXiv base id. §8.4 version-dedup and §6 version-collapsing key
  on the *base* id, and §9.2 expects uniqueness; today both are plain indexes. The current
  duplicate scanner derives arXiv bases at runtime, but the physical schema still needs cleanup
  before version-linking and collapse modes depend on it.
- **`Reference` is missing `resolution_status` / `resolution_confidence`** (and `parsed_authors`/
  `parsed_venue`). The M6 graph pipeline (§12.5) marks edges by `resolution_status`
  (unresolved / local_match / external_match); building the graph without it means another
  migration + reparse.
- **`CitationMention` is missing `extraction_confidence`** and stores coordinates as four float
  columns instead of §9.3's `pdf_coordinates` jsonb (the PDF.js reader contract for M3).
- **Topic modeling is collapsed** into a single `topic_assignments` table instead of §9.3's
  `topic_models` / `topics` / `work_topics` (loses model version/params/keywords needed by
  §8.15 model-freezing). M7.
- UUIDv7/ULID sortable PKs (§9.2) vs the current random UUID4 — minor, but migration cost grows.

### Behavioral / security gaps found in the alignment audit

- **`user_confirmed` is a global enrichment lock.** `create_work`/`update_work` set
  `user_confirmed=True` on any manual edit, and promotion keys off `not user_confirmed`, so a
  single edit permanently freezes *all* fields against future enrichment. §8.12 wants
  per-field user locks ("user edits highest priority, conflicts surfaced as warnings"). Move to
  per-field locking before users edit metadata heavily.
- **Read/export audit events are not emitted (§7.6).** `record_event` fires only for auth and
  service mutations; `file.viewed` / `file.downloaded` / `paper.exported` are never written on
  the stream/works/exports endpoints. Add them.
- **No SSRF guard in the enrichment HTTP clients.** Only fixed arXiv/Crossref hosts are hit
  today, but §7.7 requires the future `/sources/url` importer to block private IP ranges — it
  must not be built on the current unguarded `httpx` clients.
- **Duplicate citation-contexts surface.** `endpoints/citations.py` `/contexts` is still a stub
  while `works.py` `/works/{id}/citation-contexts` is the real one; remove/redirect the stub so
  a dead endpoint does not ship in the OpenAPI schema.

## Next milestone: M0 developer skeleton

Acceptance criteria:

1. `docker compose up -d --build` starts PostgreSQL, Redis, the api server, and the agent client (GROBID/Ollama are opt-in profiles).
2. Backend serves `GET /api/v1/health`.
3. Server can create the first admin account through a server-console command.
4. The project can run tests with `make test` (in the api container, Python 3.12).
5. LaTeX docs compile with `docs/compile_docs.sh` on a machine with TeX installed.

Progress notes:

- `GET /api/v1/health` exists and has a test.
- Server-console admin scripts are DB-backed for users/audit events and password reset revokes active sessions.
- Alembic is initialized for the first security tables and sessions; broader domain models still need migrations.
- Build/test now run in containers (Python 3.12). Validated end to end: `docker compose up -d --build` brings the api healthy after migrations, the full suite passes via `docker compose run --rm api pytest` (23 passed), and a live smoke test (bootstrap owner → login → owner `GET /admin/users` 200 → editor `GET /admin/users` 403 → bad login 401 → audit events) succeeds against real Postgres.
- Replaced unmaintained `passlib` with the `bcrypt` library directly (passlib was incompatible with modern bcrypt); fixed Alembic revision ids that exceeded the 32-char `alembic_version` column.

## Next milestone: M1 core library, organization, and files

See `ROADMAP.md` / `SPECIFICATION.md` §20 for the full plan. The local agent and teleport
moved to M5; M1 now delivers the single-machine value loop via server-folder import.

Acceptance criteria:

1. Admin user can log in.
2. A server-folder source can be added and scanned (single-machine mode, no agent required).
3. A folder of PDFs imports as file/work records with a PyMuPDF first-page preview.
4. Works can be created/edited and added to multiple shelves; shelves to multiple racks.
5. Works, shelves, and racks can be tagged.
6. Basic metadata search and filters work; library, shelf/rack, file, and reading-queue views render.
7. No arbitrary path endpoint exists.
8. Import activity is audit logged.

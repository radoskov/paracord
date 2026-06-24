# Progress Report

## Current status

**Milestone 0 (foundation) is essentially complete and validated; Milestone 1 (the core
library — the actual product) is in progress across the backend and frontend.**

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

What still does NOT exist yet:

- Citation context frontend/reader and graph integration are not implemented yet (the
  work-scoped API exists).
- OpenAlex/Semantic Scholar connectors; Crossref/arXiv title-based (fuzzy) lookup — only
  exact-identifier enrichment is implemented so far.
- Duplicate/version detection beyond exact-hash, arXiv/DOI/bibliography *imports* (ingest by
  link), embedded PDF.js reader, citation graph, export, AI summaries, topics.

Component note: **Redis has a live consumer** — the `worker` service runs the RQ
`paperracks` queue and processes both GROBID extraction and enrichment jobs.

### Start here (next agent)

M1 done; M2 extraction + enrichment pipeline is live and validated. Continue M2/M4:

1. **Citation contexts UI**: wire `GET /works/{id}/citation-contexts` into the frontend
   reader/reference panel so users can inspect contexts.
2. **Duplicate/version detection** (`services/duplicate_detection.py`) + a review queue
   (exact hash done at import; add DOI/arXiv/fuzzy-title candidates) — this is M4.
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

## In progress

- M1 backend API/frontend implementation.
- Local agent protocol stubs.
- LaTeX implementation manual draft.
- Agent task partitioning.
- M0 developer skeleton hardening.

## Not started

- Login rate limiting / failed-login lockout (role-based authorization is now implemented).
- In-app password-change endpoint (server-console reset exists; web change-password + its session revocation still pending).
- PDF.js reader integration.
- Agent registration and token rotation implementation.
- GROBID TEI parser implementation.
- Duplicate/version detection implementation.
- Citation graph materialization implementation.
- PDF.js integration.
- Export renderer.
- BERTopic and embedding pipeline.
- Local LLM summarization pipeline.
- Audit-log storage and admin views.
- End-to-end tests.

## Tech debt and cleanups

Low-severity items found during the audit, not tied to a feature milestone. Address opportunistically.

- Remove or fully wire the dead `guest_access_enabled` setting (`backend/app/core/config.py`). A startup `assert_no_guest_roles` check now enforces that no guest role is present in `security.allowed_roles`.
- Migrate deprecated `datetime.utcnow()` to timezone-aware `datetime.now(timezone.utc)` across `services/auth.py`, `models/*`, `services/users.py`, and `scripts/reset_admin_password.py`. Note: do this together with switching the model `DateTime` columns to `DateTime(timezone=True)` (plus a migration), otherwise naive/aware comparisons in session checks will break.
- Add symlink-escape and `../` traversal test cases to `agent/tests/test_security.py` (the primitive is correct but currently untested).
- Remove or wire the unused agent config flags `follow_symlinks` / `teleport_enabled`.
- Note in `docs/architecture/api_surface.md` and `data_model.md` that they reflect current stubs and defer to `SPECIFICATION.md` §10 / §9.
- Relabel the `SPECIFICATION.md` front-matter Contents as a thematic overview (its numbers do not match the section numbers).

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

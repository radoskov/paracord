# Changelog

All notable changes to PaRacORD should be documented in this file.

The format follows Keep a Changelog style conventions, but the project is currently pre-release.

## [Unreleased]

### Added

- Added a Postgres migration↔model parity test (`backend/tests/test_migration_parity.py`, AUDIT
  C2): it creates a throwaway database, runs `alembic upgrade head`, and asserts every model table
  and column exists in the migrated schema — the guard that would have caught the missing-migration
  bug above. It self-skips when no Postgres is reachable (so the SQLite-only run and current CI
  stay green), runs via `make test-migrations`, and the CI `backend` job now has a Postgres service
  + `DATABASE_URL` so it runs there. Also set `path_separator = os` in `alembic.ini` to clear an
  alembic deprecation warning.
- Added `docs/AUDIT.md` — a full functional + implementation audit (2026-06-25) covering
  spec-fidelity per capability, correctness/infra/security/data-model findings with severities, and
  a prioritized "Path to a fully functional app" backlog. Refreshed `docs/architecture/api_surface.md`
  and `data_model.md` (they were pre-M2 stubs) to match the real routes/tables, and added a top-of-file
  audit pointer + honest framing (semantic search / topics are lexical/TF-IDF stand-ins) to
  `PROGRESS.md`.

- Added M7 lightweight topic modeling (no ML dependency): `POST /api/v1/ai/topics` clusters a
  library/shelf/rack scope's works into keyword-labelled topics (`services/topic_modeling.py` —
  TF-IDF + a small deterministic k-means, fully local/no-egress, deterministic for a given input
  order) and persists `TopicAssignment` rows stamped with a `topic_model_id` (re-running a scope
  replaces them). Returns each topic's keyword label and work count. The default tier
  deliberately avoids BERTopic/sentence-transformers (a real embedding/BERTopic backend can
  replace `model_topics` later). The Svelte library gained a "Model topics" panel for the current
  scope. Covered by `test_topic_modeling.py` and the enabled forward-looking
  `test_topic_model_on_shelf_suggests_tags`. With this, all `test_future_milestones.py`
  acceptance contracts are enabled (no skipped tests remain).
- Added M5 local-agent enrollment (owner-gated, SPEC §11.2): an owner mints a single-use,
  expiring enrollment token (`POST /api/v1/admin/agents/enroll-token`); the agent presents it
  unauthenticated (`POST /api/v1/agents/enroll-request`, returns 202 with a pending agent); an
  owner approves it (`POST /api/v1/admin/agents/{id}/approve`), which mints the agent's scoped
  access token (returned once). New `agents` and `agent_enrollment_tokens` tables (migration
  `0009_agents`); all tokens are stored hashed (sha256) and every step writes an audit event
  (`agent.enroll_token_issued` / `agent.enroll_requested` / `agent.approved`). The `/agents`
  router is no longer behind the user-session dependency since agents authenticate with their
  own token; the legacy `/agents/register` stub now points at the new flow. Covered by
  `test_agents.py` and the enabled forward-looking `test_agent_enrollment_requires_owner_approval`.
- Added M3 BibTeX import: `POST /api/v1/imports/bibtex` ingests pasted/uploaded BibTeX into
  works (`services/bibtex.py` — a small dependency-free balanced-brace parser handling
  `{…}`/`"…"`/bare values, nested braces, and `@comment`/`@string`/`@preamble`). Authors are
  recorded as a `bibtex`-sourced MetadataAssertion, venue/year/DOI/arXiv (from
  `archiveprefix`+`eprint`) are mapped onto the work, and an `ImportBatch` + `import.bibtex`
  audit event capture the run. Entries are de-duplicated against the library by normalized DOI
  and title (re-import is a no-op); imported works are left `user_confirmed=False` so enrichment
  can still fill gaps. The Svelte library gained a paste-BibTeX import box. Covered by
  `test_bibtex_import.py` and the enabled forward-looking `test_import_bibtex_creates_works`.
- Added M7 semantic search: `POST /api/v1/search/semantic` ranks works by cosine similarity to
  a free-text query (`services/semantic_search.py`). The default embedder is a deterministic,
  dependency-free feature-hashing bag-of-words model (`services/embeddings.py`) — fully local
  (no network/egress) and stable across processes; a real local model
  (sentence-transformers / Ollama) can later be plugged in behind the same interface.
  Embeddings of each work's title + abstract are cached in a new `embeddings` table (vectors
  stored as JSON and ranked with Python cosine, so the same code path works on SQLite and
  Postgres — a pgvector index is a future scaling step) and computed lazily on the first search.
  Migration `0008_embeddings`. The Svelte library gained a semantic search box that opens the
  matched work. Covered by `test_semantic_search.py` and the enabled forward-looking
  `test_semantic_search_returns_neighbours`.
- Added M7 local paper summaries (tiers 0 and 1, no LLM, no network):
  `POST /api/v1/works/{id}/summaries` and `GET` (`services/summarization.py`). Tier 0
  (`abstract`) stores the work's abstract verbatim; Tier 1 (`extractive`) runs a dependency-free
  frequency-based extractive summarizer over the abstract plus extracted GROBID body text
  (`tei_parser.extract_body_text`). Summaries are stored with provenance (`model_name` +
  `prompt_version`) and replace any prior summary of the same type (idempotent re-runs). The
  Svelte library gained an Abstract/Extractive summary panel for the selected work. Covered by
  `test_summarization.py` and the enabled forward-looking `test_local_summary_records_provenance`.
  Tier 2 (local-LLM abstractive via Ollama) is intentionally left for later.
- Added the M6 scoped citation graph: `POST /api/v1/graphs/citation` builds a node/edge graph
  for a library/shelf/rack scope (`services/citation_graph.py`). Edges are derived from
  extracted `Reference` rows resolved to local works (persisted `resolved_work_id`, else an
  exact DOI/arXiv-base match); `node_mode=local_only` keeps in-scope edges while
  `include_external` also surfaces cited works not yet in the library, plus a summary
  (node/edge/external/unresolved counts). Self-citations are dropped and repeated citations
  raise the edge weight. The Svelte library gained a lightweight graph panel (summary + edge
  list, scoped to the selected shelf/rack or whole library), replacing the placeholder. The
  previously-stub `GET /graph` endpoint is now `POST /graphs/citation`. Covered by
  `test_citation_graph.py`, `CitationGraph.test.ts`, and the enabled forward-looking
  `test_shelf_citation_graph_is_scoped`.
- Added OpenAlex and Semantic Scholar metadata-enrichment connectors (identifier-based, like
  the existing arXiv/Crossref ones): OpenAlex is queried by DOI (reconstructing its
  inverted-index abstract) and Semantic Scholar by arXiv id or DOI. Both are wired into
  `enrich_work` behind new `enrichment_openalex` / `enrichment_semantic_scholar` settings (and
  `metadata_enrichment.sources.*` config keys, now read by the loader), default **off**. They
  record provenance assertions and promote trusted fields exactly like the existing sources,
  send only the bibliographic identifier (no titles/abstracts) so the data-egress policy is
  preserved, and are covered by parser + `enrich_work` tests.
- Hardened the M4 duplicate/version review: duplicate-candidate API responses now include
  human-readable entity labels, a summary string, and a `suggested_target_work_id`. When a
  merge/link action is applied without an explicit target, the surviving canonical work is now
  chosen by heuristic (user-confirmed → latest arXiv version → metadata completeness) instead
  of arbitrary id order. Actions are refused on already-resolved candidates, with an extra
  guard that prevents the same file from being split twice (which would create duplicate
  works). The Svelte review panel surfaces the labels/summary and uses the suggested target.
  Covered by new cases in `test_duplicates_api.py`.
- Expanded citation export to all planned formats: `/api/v1/exports` now renders BibTeX,
  BibLaTeX, RIS, CSL JSON, Markdown, HTML, and plain text (previously only BibTeX/text).
  Exports include authors (resolved from the best metadata assertion), use `authorYEAR`
  citation keys, and return a per-format filename + content type. A `paper.exported` audit
  event is now recorded for every export (SPEC §7.6/§8.13). The Svelte library gained a
  working export control (format picker + file download) for the selected shelf or rack,
  replacing the placeholder `ExportDialog`. Covered by `test_export_formats.py` (8 cases) and
  `ExportDialog.test.ts`.
- Added frontend component tests (Vitest + jsdom + Testing Library, `vitest.config.ts`,
  `make frontend-test`, and a CI `frontend` job): `main.test.ts` executes the entrypoint in
  a DOM and asserts the app mounts into `#app` (regression guard for the Svelte-5 mount bug),
  and `App.test.ts` checks the sign-in view renders. These run the real Svelte mount, so they
  catch client-render failures a raw-HTML fetch cannot.
- Expanded the test suite with high-level coverage: a shared `conftest.py` harness
  (FastAPI `TestClient` over in-memory SQLite) and three layers — service/unit tests,
  user-oriented API flow tests (`test_api_flows.py`: import → organize → search → read,
  metadata review, citation contexts), a security suite (`test_api_security.py`: RBAC matrix,
  no-guest, auth-required, account-enumeration, audit, PDF path-escape), and skipped
  forward-looking tests for M3+ (`test_future_milestones.py`, each with an `ENABLE WHEN`
  note to turn on as its milestone lands). ~75 passing + 8 skipped backend.
- Added the M4 duplicate/version review queue foundation: `duplicate_candidates` model and
  migration `0006_dupe_candidates`, plus a DB-backed scanner for same-DOI, same-arXiv-base,
  fuzzy-title, text-fingerprint, and exact-file candidates with idempotent candidate upserts.
- Added duplicate review API endpoints under `/api/v1/duplicates` to list candidates, trigger
  scans, and mark candidates `accepted`, `rejected`, `ignored`, or back to `open`.
- Added an initial Svelte duplicate-review panel: list/open-status filter, scan trigger, signal
  display, and accept/reject/ignore status controls backed by `/api/v1/duplicates`.
- Added backend duplicate-review actions: merge work candidates without deleting source works,
  link a candidate as a `WorkVersion`, mark file candidates as duplicate copies, keep separate,
  and ignore, with audit events and focused tests.
- Updated the Svelte duplicate-review panel to call explicit backend actions (merge, link
  version, mark duplicate, keep separate, ignore) and reopen resolved candidates.
- Added initial multiwork-file candidate detection: long/proceedings-like files or previews with
  repeated abstract/reference markers now enter the duplicate review queue as `multiwork_file`.
- Added the backend `split_file` review action for `multiwork_file` candidates: supplied
  segments create `FileSegment`, `Work`, and `FileWorkLink` rows with multiwork warning state.
- Added frontend split-file controls for `multiwork_file` candidates using line-based
  `Title | start page | end page` segment entry.
- Added an embedded reader surface that loads authenticated PDF streams as object URLs and
  includes a References tab backed by extracted citation contexts.
- Added separate reader annotation storage: `annotations` model/migration and
  `GET`/`POST /api/v1/works/{work_id}/annotations`; enabled the M3 annotation acceptance test.
- Added reader annotation UI: the embedded reader now has a Notes tab that lists annotations
  and creates note/highlight/page-anchor/citation-note rows through the work annotation API.
- Added initial bibliography export: `/api/v1/exports` resolves work/shelf/rack scopes and
  renders BibTeX (plus plain-text fallback); enabled the shelf BibTeX acceptance test.
- Added raw TEI storage and citation mention persistence for M2 extraction: migration
  `0005_raw_tei_mentions`, `RawTeiDocument`, source-TEI links on references/mentions, TEI
  body `ref type="bibr"` parsing with sentence contexts, and idempotent persistence of
  `CitationMention` rows from GROBID TEI.
- Added `GET /api/v1/works/{work_id}/citation-contexts` to expose persisted citation
  mentions with their extracted reference metadata.
- Added an initial frontend citation-context panel for the selected work in the Svelte
  library workspace.
- Added external metadata enrichment (arXiv + Crossref): identifier-based connectors in
  `services/metadata_enrichment.py` that record provenance-aware `MetadataAssertion`s and
  promote trusted external fields over GROBID when the work is not user-confirmed;
  arXiv-id-from-filename detection at import; an automatic import → extract → enrich chain
  plus a `POST /works/{id}/enrich` trigger and an `enrich_work_job` worker job; a
  review/conflict surface (`GET /works/{id}/metadata`, `POST /works/{id}/metadata/select`);
  and enrichment config loading. Validated live: arXiv auto-corrected GROBID's mis-detected
  title for 1706.03762 to "Attention Is All You Need", with the conflict surfaced.
- Wired the GROBID extraction pipeline into the running system: an RQ queue
  (`app/workers/queue.py`, best-effort enqueue), a `worker` compose service running
  `rq worker`, enqueue-on-import, and a `POST /files/{id}/extract` trigger. The
  `extraction` profile now uses the lightweight `lfoppiano/grobid:0.8.0` CRF image
  (~0.5 GB) instead of the ~12 GB deep-learning image. Validated end-to-end on real arXiv
  PDFs (Transformer + ResNet): HTTP import → worker → live GROBID → 90 references and
  abstracts persisted asynchronously.
- Started the M2 extraction layer: a real GROBID TEI parser (`services/tei_parser.py` —
  title/abstract/DOI/authors/references via lxml), a provenance-aware persistence service
  (`services/extraction.py`) that records `MetadataAssertion`s and `Reference`s and only
  promotes canonical title/abstract/DOI when the work is not user-confirmed, Alembic
  migration `0004` for `references`/`citation_mentions`/`metadata_assertions`, a synchronous
  GROBID client method, and the wired `extract_pdf_job`. Covered by a TEI fixture and tests,
  and validated against real Postgres (import → extract → assertions/references).
- Started the M1 core-library backend slice: added `sources`, `import_batches`,
  `shelf_works`, `rack_shelves`, and `tag_links` models plus an Alembic migration for the
  core file/work/source/organization tables.
- Added alias-only configured server-folder sources and folder imports. Imports scan a
  configured root, SHA-256 hash PDFs, create File/Location/Work/FileWorkLink rows, extract a
  PyMuPDF first-page text preview when available, deduplicate by file hash, and audit-log
  source creation/import completion.
- Added basic backend endpoints for sources, folder import batches, file metadata, manual
  work create/edit/search, shelves, racks, memberships, and tags.
- Added focused M1 service tests for configured-root alias handling, server-folder import
  persistence, deduplication, and audit logging.
- Added a Compose-managed frontend service (`frontend/Dockerfile`) with Docker-contained
  Node dependencies, `make frontend-dev`, `make frontend-build`, and runbook notes.
- Added the initial M1 Svelte workspace: login, library table, reading queue, source import
  controls, manual work creation, shelf/rack/tag controls, and file preview list.
- Added backend read endpoints for file listing, shelf works, and rack shelves to support
  the M1 frontend views.
- Added work search filters for shelf, rack, and tag membership and exposed them in the
  frontend library toolbar.
- Added authenticated PDF streaming for configured server-folder file locations, with
  root-escape protection and a frontend file-panel action that opens the streamed PDF.
- Added archive/unlink operations for shelves, racks, shelf-work memberships,
  rack-shelf memberships, and tag links, with matching frontend controls and tests.
- Added a containerized development & evaluation stack: `backend/Dockerfile` (api server) and `agent/Dockerfile` (client), `docker compose` services for `postgres`/`redis`/`api`/`agent` (with healthchecks, a smart entrypoint that runs migrations only for the server, and opt-in `extraction`/`ai` profiles for GROBID/Ollama), `backend/requirements-dev.txt`, a `ci` GitHub Actions workflow (lint + test on Python 3.12), `make` targets (`build`/`up`/`down`/`test`/`lint`), and `docs/runbooks/dev_containers.md`. The full test suite (23 tests) and a live auth/role smoke test now pass in-container against real Postgres.
- Added role-based authorization (`require_roles` / `require_owner` dependencies) and owner-only admin endpoints under `/api/v1/admin`: list/create users, change a user's role, disable a user (with last-active-owner protection), and paginated audit-event access. New `user.created` (admin API), `user.role_changed`, and `user.disabled` audit events.
- Added an account-enumeration mitigation to login (constant-time bcrypt verification on the unknown/disabled-user path) and a startup assertion that no guest role is present in `security.allowed_roles`.
- Added a reusable FastAPI current-user dependency for bearer-token authentication.
- Protected all non-health, non-login API routers with the authentication dependency.
- Added API dependency tests for valid, missing, and invalid bearer tokens.
- Added revocable server-side bearer sessions for login/logout.
- Added persisted audit events for login success, login failure, and logout.
- Added password-reset session revocation for server-console credential recovery.
- Added authentication service tests for credential validation, token hashing, revocation, and audit persistence.
- Added Alembic configuration and the initial `users`/`audit_events` migration.
- Added a `user_sessions` migration for revocable tokens.
- Added `make migrate` for applying backend database migrations.
- Added server-console admin script tests using an isolated SQLite database.
- Added backend YAML settings loading with environment-variable override precedence.
- Added bcrypt password hashing and verification helpers.
- Added DB-backed server-console owner bootstrap and password-reset script skeletons with audit events.
- Added backend tests for settings loading and security helper behavior.

### Changed

- Pinned frontend dependencies in `frontend/package.json` (svelte `^5.56.4`, vite `^8.1.0`,
  `@sveltejs/vite-plugin-svelte` `^7.1.2`, typescript `^6.0.3`, pdfjs-dist `^6.0.227`,
  cytoscape `^3.34.0`) instead of `latest`, so a future major bump can't silently reintroduce
  a framework mismatch (the cause of the blank-page bug).
- Restructured the Makefile and runbooks for clearer test/lint/format workflows: tests run per component (`test-api` in the api container, `test-agent` in the agent container, `test` runs both) rather than forcing agent code into the server image; lint/format are host-local (`lint`/`fix`) since Ruff is pure static analysis; added `up-extraction`/`up-ai` profile targets for GROBID/Ollama; fixed `make db-shell` to expand `$POSTGRES_USER`/`$POSTGRES_DB` inside the Postgres container; added `agent/pyproject.toml` so the agent's pytest is properly configured. Updated `README.md`, `docs/runbooks/development_setup.md`, and `docs/runbooks/dev_containers.md` to match.
- Bumped the target runtime to Python 3.12 (`pyproject.toml`, Dockerfiles, CI) and the Postgres image to `pgvector/pgvector:pg17`. `make test` now runs in the api container by default (`make test-local` runs on the host).
- Reconciled `SPECIFICATION.md` with the implemented scaffold: roles are `owner | editor | reader` (was `owner | member`), the repository-layout and per-agent work split now defer to `WORK_SPLIT.md` (A–J) and the actual `backend/ frontend/ agent/` layout, config examples use port 8000 and bcrypt, and the milestone plan was re-ordered to front-load the single-machine loop (the local agent moved to M5).
- Rewrote `ROADMAP.md` as a condensed mirror of the canonical `SPECIFICATION.md` §20 milestones and updated `PROGRESS.md`'s next-milestone section accordingly.
- Integrated supporting open-source tools into the spec: PyMuPDF (fast preview), YAKE/KeyBERT (keywords), OCRmyPDF/Tesseract (OCR fallback), anystyle/refextract (reference fallback), biblio-glutton (local consolidation), Nougat/Marker (optional ML extraction), and Zotero translation-server (URL metadata).
- Added usability features to the spec: reading queue, related-papers suggestions, live shelf/rack bibliography, and annotation/note full-text search.
- Made topic modeling and body summaries tiered (lightweight default, heavier opt-in): BERTopic is now optional and off by default with lightweight keyword extraction as the default; paper body summaries are Tier 0 abstract → Tier 1 extractive Method/Experiment/Results (sumy/TextRank, no LLM) → Tier 2 opt-in local-LLM abstractive (Ollama). Reflected in `config/server.example.yaml`.

- Made all timestamps timezone-aware end to end. Write/default sites use `datetime.now(UTC)`
  (replacing deprecated `datetime.utcnow()`), every model `DateTime` column is now
  `timezone=True`, and migration `6a310e33c3d6` converts the existing Postgres columns to
  `timestamptz` (interpreting stored naive values as UTC; introspects `information_schema`, so
  it covers every column and is a deterministic, perfectly reversible no-op on SQLite). The
  hand-written migration replaced an autogenerated draft that had bundled in destructive,
  unrelated changes (dropping every foreign key, a JSONB→JSON downgrade, table creates).
- Switched the outbound HTTP clients (`services/grobid_client.py`,
  `services/metadata_enrichment.py`) from `httpx` to its successor `httpx2`, updated in
  `backend/requirements.txt` and `agent/requirements.txt`.

### Fixed

- Fixed a prod-breaking schema gap: the `summaries` and `topic_assignments` model tables had no
  Alembic migration, so a fully migrated Postgres lacked them and the M7 summary/topic endpoints
  would raise `UndefinedTable` in production (tests missed it because they build the schema from
  `Base.metadata` on SQLite). Added migration `0010_summaries_topics`, verified on Postgres. Found
  during the full-project audit (`docs/AUDIT.md` C1).
- Fixed `auth.get_active_session` raising `TypeError: can't compare offset-naive and
  offset-aware datetimes`: session `expires_at` values are normalized with a new `_as_utc()`
  helper before comparison against `datetime.now(UTC)`, so the check is robust on backends that
  round-trip naive datetimes (SQLite, or a not-yet-migrated Postgres column). This had broken
  every authenticated request and ~15 tests after the timezone migration.
- Agent tests now actually run in Docker. They were silently skipped (the `agent/` tree isn't in the api container), and the explicit-path Make target failed outright; agent tests now run in the agent container, so `make test` exercises both backend (48) and agent (2) suites.
- Made the `citation`/`metadata`/`ai` models use the generic `sqlalchemy.Uuid` instead of `postgresql.UUID`, matching the rest of the models so their tables can be created under SQLite (tests) as well as Postgres.
- Fixed invalid `docker-compose.yml` YAML (the `${VAR:?…}` default messages contained an unquoted colon — "mapping value is not allowed in this context"); the guarded values are now quoted.
- Replaced unmaintained `passlib` with the maintained `bcrypt` library in `core/security.py` — `passlib` 1.7.x raised `AttributeError: module 'bcrypt' has no attribute '__about__'` against modern bcrypt, breaking all password hashing. Added an explicit 72-byte length guard.
- Fixed Alembic revision ids that exceeded the 32-char `alembic_version` column (migrations failed with `value too long for type character varying(32)` on a real Postgres).
- Made `test_config` hermetic (it now clears ambient settings env vars, so it passes inside the api container where `DATABASE_URL` is set).
- Registered the `app.models.ai` models (`Summary`, `TopicAssignment`) in `models/__init__.py` so the `summaries`/`topic_assignments` tables are no longer silently omitted from `Base.metadata` (Alembic autogenerate and `create_all`).
- Fixed `make test` collection: switched pytest to `--import-mode=importlib` and added the repo root to `pythonpath` so the two `test_security.py` modules (backend + agent) coexist and `scripts` is importable; added `scripts/__init__.py`.
- Made the `docker-compose.yml` Postgres credentials fail fast with a clear message (`${VAR:?…}`) when `.env` is missing, instead of silently breaking `make dev-up`.
- Refreshed `FILE_TREE.md` (secrets-policy files, CI workflow, new scripts) and annotated the not-yet-created owned paths in `WORK_SPLIT.md`.
- Improved `scripts/check_secrets.py`: in source files only quoted string literals are flagged (unquoted code references like `password=payload.password` no longer false-positive), config-style files still flag unquoted values, and prefixed key names (`DB_PASSWORD`, `access_token`, …) are now detected.

### Security

- Added a "Data egress and privacy" section to `SECURITY.md` and `SPECIFICATION.md` (§7.8): only opt-in, audit-logged bibliographic identifiers ever leave the machine; no PDF contents, collection structure, filesystem paths, or bulk exports are transmitted.
- Kept credential recovery as a server-console operation only.
- Owner bootstrap now refuses to create a second owner account.
- Added an authoritative secrets-and-credential-handling policy (`docs/runbooks/secrets_management.md`) and wired it into `SECURITY.md`, `AGENTS.md`, `HINTS_FOR_AGENTS.md`, `CONTRIBUTING.md`, the README, and the LaTeX security chapter.
- Added `scripts/check_secrets.py`, a dependency-free secret scanner, with `make check-secrets`, a pre-commit configuration, a plain git-hook installer (`scripts/install_git_hooks.sh`), and a `secret-scan` GitHub Actions workflow.
- Hardened `.gitignore` to exclude key material (`*.pem`, `*.key`, `secrets/`, token files) and any `.env.*` except `.env.example`.
- Removed the hardcoded Postgres dev password from `docker-compose.yml`; credentials now come from `.env`.

## [0.0.0] - 2026-06-23

### Added

- Created initial repository scaffold.
- Added FastAPI backend directory layout.
- Added local workstation agent directory layout.
- Added web frontend directory layout.
- Added Docker Compose development skeleton.
- Added configuration examples for server and agent.
- Added project progress report, agent guide, work split, and implementation hints.
- Added LaTeX documentation source tree and compile script.
- Added server-local credential recovery design and placeholder script.
- Added GROBID, PostgreSQL, Redis, pgvector, PDF.js, BERTopic, and local LLM integration placeholders.

### Security

- No guest role is defined.
- All filesystem access is routed through configured roots, managed library storage, or local agent file IDs.
- Credential recovery is specified as a server-console operation only.

### Known incomplete areas

- Database migrations are placeholders.
- API endpoints are skeletal.
- Frontend components are placeholders.
- GROBID parsing, citation graph construction, export rendering, topic modeling, and summarization are not implemented yet.

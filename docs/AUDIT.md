# PaRacORD — Project Audit (2026-06-25)

Full audit of the codebase at commit `0429b14` (M7 complete), covering **functional fidelity**
(does the app do what `SPECIFICATION.md` asks, from a user's perspective?) and **implementation
quality** (algorithms, packages, infra, data model, security, tests). It is the single reference
for prioritizing the remaining work toward a fully functional app.

**How to read this:** findings are tagged `CRITICAL / HIGH / MEDIUM / LOW`. Each cites
`file:line` where useful. The closing [Path to a fully functional app](#path-to-a-fully-functional-app)
section turns the findings into ordered work packages.

**Net assessment.** The backend service layer is well-structured, the provenance model is
genuinely good, API-layer security (token sessions, per-router auth, role gates, path-traversal
guard, identifier-only egress + audit) is solid, and the test suite is broad (146 backend / 2
agent / 4 frontend, all green). The two systemic problems are: (1) **the ORM models and the
Alembic migrations are independent schema definitions that have drifted**, and the test suite
never runs migrations or Postgres, so the drift is invisible; and (2) **much of the M3–M7
feature surface is backend-only or an approximation** — the single-page UI exposes a fraction of
it, and "semantic search" / "topic modeling" are lexical/TF-IDF stand-ins, not embeddings/BERTopic.

**Fixed during this audit:** finding **C1** (missing `summaries`/`topic_assignments` migration,
prod-breaking) — migration `0010_summaries_topics` added and verified on Postgres.

---

## 1. Critical / High — correctness & infra

### C1 — `summaries`/`topic_assignments` had no migration  ✅ FIXED
Model tables (`app/models/ai.py`) shipped without a migration; a migrated Postgres (head `0009`)
lacked both, so `POST/GET /works/{id}/summaries` and `POST /ai/topics` raised `UndefinedTable` in
production. Tests missed it because `conftest.py` builds the schema from `Base.metadata` on SQLite.
**Fixed** by migration `0010_summaries_topics`; verified head `0010` leaves no model table missing.

### C2 — Migrations are never run in tests; no model↔migration parity check  ✅ ADDRESSED
`backend/tests/conftest.py` and all service tests build the schema via `Base.metadata.create_all`
on in-memory SQLite; no test ran `alembic upgrade head` or touched Postgres, which is why C1
shipped. **Added** `backend/tests/test_migration_parity.py`: it creates a throwaway Postgres DB,
runs `alembic upgrade head`, and asserts every model table and column exists in the migrated
schema (self-skips when no Postgres is reachable). Wired via `make test-migrations` and a Postgres
service in the CI `backend` job. Verified it fails at the pre-fix head (`0009`) reporting exactly
`summaries`/`topic_assignments`. **Still open (follow-up):** extend it to also assert
autogenerate-clean once C3 (FKs) and C4 (JSONB) are resolved — today autogenerate is intentionally
dirty for those, so only table/column presence is asserted, not full type/constraint parity.

### C3 — Foreign keys exist in migrations but not in the ORM models  [HIGH]
Only `UserSession.user_id` declares `ForeignKey` (`models/session.py:20`). Every other relation is
a bare `Uuid` column, while the migrations declare extensive FKs (`0003_create_m1_core_library.py`:
`locations`, `work_versions`, `file_work_links`, `shelf_works`, `rack_shelves`, `tag_links`, …).
`embeddings`/`agents`/`agent_enrollment_tokens` have FK-shaped columns with no constraint in either
place. Consequences: autogenerate is permanently dirty (wants to add dozens of FKs); SQLite tests
don't enforce FKs (fixtures never set `PRAGMA foreign_keys=ON`), so cascade / `SET NULL` behavior
is unverified while Postgres enforces it. **Fix:** declare `ForeignKey(..., ondelete=...)` on the
model columns the migrations constrain; add FKs to `agents`/`embeddings`; then make autogenerate
clean and add cascade tests on Postgres.

### C4 — `JSONB` (migrations) vs generic `JSON` (models)  [HIGH]
`0001` creates `audit_events.details` as `postgresql.JSONB`; `models/audit.py:33` and every other
JSON-ish column (`config`, `signals`, `stats`, `coordinates`, metadata) use generic `JSON`. Generic
`JSON` emits `json` (not `jsonb`) on Postgres, so any `@>` / `->>` / GIN-index query the spec
implies won't work, and autogenerate flags the mismatch. **Fix:** use
`JSON().with_variant(JSONB, "postgresql")` (or `postgresql.JSONB`) consistently in the models.

### C5 - Trying to make production env and test broke the development environment  [HIGH] (started)
`make build`, which should build development images, was building production images.
There are also misleading comments in `Dockerfile` that claim `docker-compose` builds development
by default. This was not originally true! This was fixed to some extend by adding `target: development`
to the `docker-compose.yml` file. However, the pipeline should be checked if there aren't any
more issues related to this one and also change the missing comments in `Dockerfile`(s).

### H1 — `httpx2` dependency: unpinned niche fork on the egress path  [HIGH]
`backend/requirements.txt` and `agent/requirements.txt` list `httpx2` (unpinned); imported as
`import httpx2 as httpx` in `metadata_enrichment.py:14`, `grobid_client.py:5`, and the agent.
**Verified:** `httpx2 2.4.0` *is* installed in the built image and works (plain `httpx` is absent),
so the running stack is fine — but `httpx2` is an obscure fork, unpinned, and its public-index
availability is unconfirmed, so a fresh non-container `pip install -r requirements.txt` may fail
and the build is not reproducible. The code uses the standard httpx API. **Fix:** pin it
(`httpx2==2.4.0`) and document/vendor the install source. DO NOT switch back to mainline
`httpx>=0.27`!

### H2 — Semantic search writes on the read path and can race  [HIGH]
`semantic_search.py:semantic_search` calls `ensure_work_embeddings` (which `select(Work).all()` and
`INSERT`s) and the endpoint then `db.commit()`s — a `POST /search/semantic` (logically a read)
mutates the DB, embedding every un-indexed work inside the request. First search after a large
import = latency spike + long transaction; two concurrent searches race on the
`uq_embedding_entity_model` unique constraint (second commit → IntegrityError). **Fix:** index on
import/extraction or a background RQ job so search only reads; guard inserts with
`ON CONFLICT DO NOTHING`. Stored vectors are already L2-normalized, so similarity is a dot product —
skip recomputing `norm_b`; consider numpy for one vectorized pass.

### H3 — Duplicate scan and BibTeX import are O(n²) Python loops over full tables  [HIGH]
`duplicate_detection.py` `_same_doi_candidates`/`_same_arxiv_candidates`/`_fuzzy_title_candidates`
load **all** works into Python and compare in loops (DOI is indexed but compared in Python; fuzzy
title runs `SequenceMatcher` against every other work). A full scan (`endpoints/duplicates.py`
`scan_duplicates` with no `work_id` → `select(Work).all()`) is O(n²), and `_upsert_candidate` does a
SELECT + flush per candidate inside that loop. `bibtex._find_existing` similarly does
`select(Work).where(Work.doi.is_not(None)).all()` per imported entry (O(m·n)). All run synchronously
in the request thread. **Fix:** push DOI/arXiv/title equality into indexed SQL (persist a
`normalized_doi` / `arxiv_base_id` column — also resolves a spec divergence); pre-block fuzzy-title
candidates and use `rapidfuzz` (C-backed); batch-load existing candidates or use Postgres
`ON CONFLICT`; move full-library scans to the existing RQ queue. (The indexes already exist — these
are SQL-pushdown fixes, not schema changes.)

### H4 — Unauthenticated stub agent endpoints  [HIGH]
`router.py` mounts `agents.router` with no auth dependency (correct only for `/enroll-request`).
`endpoints/agents.py` `POST /agents/manifest` (raises `NotImplementedError` → 500) and
`POST /agents/teleport/{id}` (returns a **200** `{"status":"todo"}` echoing caller input) are
unauthenticated, contradicting SECURITY.md (agent requests must be token-authenticated). **Fix:**
gate manifest/teleport behind agent-token auth now (401 when unauthenticated), even while the body
is unimplemented; don't return a success stub. Also remove the dead `citations.py` `/contexts`
`{"status":"todo"}` route from the OpenAPI surface.

### H5 — No production build/config; only a dev stack exists  [MEDIUM]
This can be solved at later stages, since we are still in development.
`backend/Dockerfile` installs `requirements-dev.txt` and runs `uvicorn --reload`;
`frontend/Dockerfile` runs `npm run dev` (Vite dev server); `docker-compose.yml` hardcodes
`PARACORD_ENV=development` and binds `0.0.0.0`. There is no prod target. SECURITY.md anticipates LAN
exposure, but the only stack is the dev stack. **Fix:** add a multi-stage prod build (runtime deps
only, gunicorn/uvicorn workers without `--reload`, `npm run build` + static serve) and a prod
compose/profile; default `PARACORD_ENV` to production.

### H6 — Local `.env` uses the wrong variable prefix  [HIGH, config]
`.env` (gitignored local file) sets `PAPERRACKS_ENV` / `PAPERRACKS_SERVER_CONFIG` /
`PAPERRACKS_AGENT_CONFIG`, but `config.py` reads `PARACORD_*` (and `.env.example` is correct). The
operator's `PARACORD_ENV` / server-config path are silently ignored and fall back to defaults.
**Fix:** regenerate `.env` from `.env.example` (standardize on `PARACORD_*`). No code change.

### H7 — `embeddings.vector` is JSON, not pgvector; `vector` extension never created  [HIGH/spec]
Spec §9.3 mandates a `vector` column; `0008_embeddings.py` stores `JSON` and cosine is computed in
Python (`embeddings.py`). The compose image is `pgvector/pgvector:pg17` but no `CREATE EXTENSION
vector` exists. Fine for a small single-user library, but it won't scale and the spec and code
disagree silently. **Fix:** either adopt `pgvector` (`Vector` column + extension + index) or amend
spec §9.3 to record the JSON-array decision. Pick one.

---

## 2. Functional fidelity — what the user actually gets

The UI is a **single page** (`frontend/src/App.svelte` mounts only `LibraryPage.svelte`): no router,
none of the §13.1 navigation areas, **no Dashboard, no Admin UI**. Owner endpoints (users, audit,
agent approval) and the metadata-review/enrich endpoints exist but have **no frontend** and are
reachable only via raw HTTP.

| Capability | Status | Gap (user perspective) |
|---|---|---|
| Auth / roles / audit | **DONE** | No `/auth/me`, change-password, session-revoke; **read/view audit events (`file.viewed`/`downloaded`/`paper.viewed`, §7.6) never emitted**; no audit viewer UI; no backups (§8.16). |
| Organize (shelves/racks/tags/status) | **DONE** | No reading-queue reordering (§8.17.1); tags lack color/description/hierarchy in UI. |
| Import | **PARTIAL** | Only server-folder + BibTeX of the **13** ingestion types in §8.1. No single-PDF upload, arXiv-id/DOI/URL, RIS/CSL/CSV, watched/agent folders; `/sources` only `server-folder`. |
| Keyword search | **PARTIAL** | Substring over 4 columns only (title/DOI/arXiv/venue) — not abstract/full-text/authors/notes. No structured query language (`author:`, `cites:`, `year:>=`, `has:pdf`, §8.7/§14), no suggestions, no saved filters. |
| Semantic search | **DIVERGENT** | `embeddings.py` is MD5 feature-hashing **bag-of-words** — literal token overlap, no meaning (a no-shared-word query scores 0). Marketed as §8.15 semantic; functionally a 2nd keyword matcher. |
| Reader / PDF | **DIVERGENT** | `PdfReader.svelte` is a plain `<iframe>`, **not PDF.js** (§5.1/§8.8/§13.8). No highlight/citation overlay, marker→reference jump, thumbnails, in-app text search, or coordinate-anchored annotations. |
| Annotations | **PARTIAL** | Create/list note/highlight/page_anchor/citation_note works, but the form **never captures PDF coordinates or a real selection** (`coordinates` always null), so highlights map to nothing. **No annotation search or export** (§8.8.7/§8.17.4). |
| Metadata review & provenance | **PARTIAL (backend-only)** | Excellent backend (assertions, conflict flag, `/metadata/select`, `/enrich`) but **no UI** — a user can't review conflicts, pick canonical, or even edit a title. `user_confirmed` is per-work all-or-nothing, not per-field (§8.12). |
| Extraction / GROBID | **PARTIAL** | Real TEI extraction + references + mention contexts, but **no PDF coordinate extraction** (`teiCoordinates` never requested → blocks PDF.js anchors), GROBID options hardcoded (no per-profile config / no zero-egress switch), **no OCR fallback** (§8.3), **no keyword extraction (YAKE/KeyBERT)** despite §8.15.1 being on-by-default. |
| Enrichment | **DONE** | arXiv/Crossref/OpenAlex/Semantic Scholar, identifier-only egress, provenance + audit. No fuzzy-title lookup; no per-work "Enrich" button in UI. |
| Duplicate / version / multiwork | **DONE** | Strongest area; matches §8.4/§13.10 (merge/version/mark-dup/split/keep/ignore + full review UI). Fuzzy match is SequenceMatcher, not MinHash (adequate). |
| Citation contexts | **PARTIAL** | Contexts extracted + shown, but §8.10 click-throughs need the (absent) PDF.js reader. |
| Citation graph | **DIVERGENT** | `CitationGraph.svelte` is a **static text edge-list, not Cytoscape** (§5.1/§8.9/§13.9). No PageRank/centrality, version collapse, layouts, click-to-open, or "add node to shelf / import missing reference". Resolution identifier-only. |
| Export | **PARTIAL** | 7 formats + scopes + `paper.exported` audit + UI. No preview, copy-to-clipboard, CSL styles, search-result/graph/selection scopes, key overrides, or **live shelf/rack bibliography** (§8.17.3). |
| Summaries | **PARTIAL** | Tier 0/1 per-work (provenance). Tier 2 (Ollama) absent by design. **Shelf/rack & citation-context summaries (§8.11 — a headline goal) are MISSING** (`/ai/summaries` is a `todo` stub). |
| Topics | **DIVERGENT** | TF-IDF + k-means with keyword labels, not BERTopic/embeddings (§8.15.3). Collapsed to one `topic_assignments` table (no `topic_models`/`topics`/`work_topics`). No accept-as-tag / shelf-from-topic; **no related-papers feature (§8.17.2)**. |
| Local agent | **MISSING beyond enrollment** | Owner-gated enrollment works; **manifest/teleport are stubs** → external-LAN-server mode (§6.2/§11, the agent's whole purpose) is non-functional. |

**Honest-framing note:** PROGRESS.md/CHANGELOG describe "semantic search" and "topic modeling" as
delivered. They are lexical/TF-IDF approximations; the docs should say so (now corrected) so the
next programmer doesn't assume embedding-quality behavior.

---

## 3. Data-model divergences from SPEC §9.3  [MEDIUM]

Each also contributes autogenerate noise (C3/C4).

- **`works`**: no `arxiv_base_id` (version stripped at query time in `metadata_enrichment._arxiv_base`
  instead of persisted); **no UNIQUE** on `doi` / arXiv base (duplicate-by-identifier not prevented
  at the DB). Spec names `canonical_abstract`; model uses `abstract`.
- **`Reference`**: missing `resolution_status` enum (`unresolved|local_match|external_match|ambiguous|ignored`)
  — the citation-graph edge classifier the spec wants. (`resolved_work_id` is computed per-request,
  never persisted.)
- **`CitationMention`**: stores 4 float coord columns instead of `pdf_coordinates jsonb` — can't hold
  multi-quad/multi-line spans; blocks the PDF.js anchor contract.
- **Topics**: single `topic_assignments` table instead of `topic_models` / `topics` / `work_topics`
  (loses model params/version/keywords, no model freezing).
- **`metadata_assertions`**: scalar `value` string + no `conflict_status` vs spec `field_value jsonb`.
- **`users`**: missing `display_name`, `email`, `last_login_at`, `password_changed_at` (and
  `is_active` is proxied by `disabled_at`).
- **`agents`**: missing `host_alias`, `capabilities jsonb`, `last_seen_at`, `created_by_user_id`,
  `revoked_at`; status set is narrower than spec.

**Decide per item:** implement to match §9.3, or amend §9.3 to ratify the simpler shape. Do the first
two (`arxiv_base_id`+UNIQUE, `Reference.resolution_status`) before more is built on duplicate/version
and graph code — they cost a migration + re-resolve later.

---

## 4. Security & docs accuracy  [MEDIUM/LOW]

- **M2 — Encryption-at-rest is documented but not implemented.** SECURITY.md claims at-rest field
  encryption and a `PARACORD_SECRET_KEY`; there is no encryption code and the key is read nowhere
  (paths stored as plaintext `path_alias`/`internal_uri`; spec §9.3 wants `path_encrypted_or_alias`).
  The token-session design is sound and needs no signing key. **Fix:** implement field encryption +
  wire the key, or correct the docs to state paths are stored as aliases/plaintext with no at-rest
  encryption. (Do not leave the docs overstating the posture.)
- **M3 — `guest_access_enabled` is loaded but never enforced** (`config.py`); the real guard is
  `assert_no_guest_roles(allowed_roles)` at startup, which validates the static list, not actual
  `User.role` values. **Fix:** remove the dead flag (or reject when truthy) and validate `role`
  against `Role` on user writes.
- **M4 — GROBID consolidation flags hardcoded on** (`grobid_client.py`), so the "self-host / disable
  for zero egress" control SECURITY.md promises isn't exposed. **Fix:** drive from settings.
- **M5 — Enrichment SSRF hardening:** fetchers interpolate the DOI/arXiv id into the URL path
  without encoding and `follow_redirects=True`. Low practical risk (hardcoded hosts) but worth
  URL-encoding identifiers and restricting/forbidding cross-host redirects.
- **L — SSRF/egress copy:** SECURITY.md "never transmits PDF contents" is true only against third
  parties; PDFs are POSTed to the operator's GROBID. Reword to "…to third parties; PDFs go only to
  the operator-controlled GROBID."

---

## 5. Test-coverage blind spots (all stem from SQLite-from-models)  [MEDIUM]

Strong where it counts (auth 401/403 matrix, no-guest, account-enumeration timing, **path traversal**
in `files.py` is tested, role gating, dedup/export/graph/summaries/topics/semantic/bibtex/agents
service + API tests). But SQLite-from-`Base.metadata` masks Postgres behavior with **no**
representative test for: migrations-apply + autogenerate-parity (C2), FK cascade / `SET NULL`
(PRAGMA off), `JSON` vs `JSONB` querying, `timestamptz` round-trip / `onupdate`, `server_default`
values, pgvector, and `String(n)` / case-sensitive `LIKE` collation (affects dedup & search realism).
**Fix:** add a Postgres-backed integration suite (compose `postgres` or testcontainers).

---

## Path to a fully functional app

Ordered by leverage. Each item references the findings above.

**P0 — Make production real & trustworthy (do first)**
1. ~~**Postgres migration/parity test** (C2) — the guard that would have caught C1.~~ **Done**
   (`test_migration_parity.py`, `make test-migrations`, CI Postgres service). Follow-up: extend to
   assert autogenerate-clean once C3/C4 land, and add FK-cascade/timestamptz/JSONB assertions.
2. **FK + JSONB model alignment** (C3, C4) so autogenerate is clean and cascades are real.
3. **Production build + compose profile** (H5); pin/decide `httpx2` (H1); fix `.env` prefix (H6).

**P1 — Schema decisions that block downstream work**
4. Persist `normalized_doi` + `arxiv_base_id` (+ UNIQUE) and `Reference.resolution_status`
   (§9.3 / H3) — unblocks SQL-pushdown dedup and proper graph edge classification.
5. Move embeddings + dedup full-scans off the request thread onto RQ; SQL-pushdown the identifier
   lookups; `rapidfuzz` for fuzzy title (H2, H3).

**P2 — Close the biggest user-facing gaps**
6. **Navigation shell + Dashboard + Admin UI** (users/agents/audit) — currently owner ops are
   raw-HTTP only.
7. **PDF.js reader** with `teiCoordinates` extraction → coordinate-anchored highlights, marker→ref
   and ref→mentions jumps, thumbnails, in-app search. Unblocks the annotation & citation UX.
8. **Metadata review/edit UI** (conflicts, pick-canonical, edit work, per-work Enrich) + per-field
   `user_confirmed` locking (§8.12).
9. **Shelf/rack & citation-context summaries** (§8.11) — currently a stub; a headline product goal.
10. Expand import to arXiv-id/DOI/URL/upload/RIS/CSL (§8.1).

**P3 — Upgrade the approximations & finish the agent**
11. Real embeddings (sentence-transformers/Ollama, opt-in) behind the existing `embed_text`
    interface + pgvector (H7); keyword extraction (YAKE/KeyBERT) tier; consider BERTopic for topics.
12. Interactive Cytoscape graph (centrality, version collapse, click-to-open, import-missing-ref).
13. Agent manifest ingestion + teleport into the content-addressed store (H4 auth first).
14. Backups/restore (§8.16); read/view audit events (§7.6); export preview/clipboard/CSL styles.

**P4 — Security/doc truthfulness**
15. Implement at-rest encryption or correct SECURITY.md (M2); enforce/remove `guest_access_enabled`
    (M3); configurable GROBID consolidation (M4); SSRF hardening (M5); reword egress copy (L).


# AUDIT Addendum — PaRacORD / PaperRacks

Date: 2026-06-26
Scope: static repository audit of `main` after the recent CI stabilization work
Intended placement: append to `docs/AUDIT.md` or commit as `docs/AUDIT_ADDENDUM_2026-06-26.md`

## 0. Method and limitations

This addendum is based on a static inspection of the public repository tree, documentation, Docker/CI configuration, backend services, frontend components, agent code, tests, and the current `docs/AUDIT.md`, `SPECIFICATION.md`, `PROGRESS.md`, `ROADMAP.md`, and `CHANGELOG.md`.

It is **not** a full dynamic penetration test, load test, or UX review. CI is reported as passing by the maintainer; this audit assumes the current passing state after the recent Docker target, Node 24, timezone, frontend-lock, and topic-test fixes.

The previous audit dated 2026-06-25 remains useful, but parts of it are now stale because several high-priority items were fixed or partially fixed after that audit.

## 1. Executive summary

The project has moved from a thin scaffold to a credible early vertical slice of the intended application.

The strongest areas are now:

- authentication/session model and owner/editor/reader authorization boundaries;
- audit-event infrastructure;
- Docker development workflow after explicit `development` build targets were added;
- database migration parity testing against real PostgreSQL/pgvector;
- server-folder import, managed upload import, shelves/racks/tags, duplicate review, metadata review, citation graph basics, export basics, extractive summaries, lightweight topic modeling, and semantic-search scaffolding;
- pre-commit and CI hygiene, including frontend tests/builds and lockfile checks.

The biggest remaining gaps are not random implementation errors. They are mostly **MVP-vs-spec distance**:

- the frontend is still an all-in-one operator page rather than the final shelf/rack/file/reader/graph application;
- the local agent is still mostly scanner/enrollment scaffolding, not yet a full remote-file/teleport bridge;
- semantic search, topics, and summaries are intentionally lightweight lexical approximations, not yet the intended local-model/BERTopic/pgvector pipeline;
- GROBID integration works structurally but does not yet expose/configure all coordinates and consolidation controls required by the spec;
- several docs overstate or lag implementation, especially architecture docs, security/encryption claims, and roadmap status.

There is also one important correctness issue to fix early:

> **Managed-upload PDFs are queued for extraction, but `extract_and_store()` currently resolves only `server_path` locations. Uploaded managed-library PDFs use `managed_path`, so background extraction for uploaded PDFs is likely to fail.**

That should be treated as a near-term high-priority bug because upload-to-managed-library is one of the main intended workflows.

## 2. Previous audit status update

The previous audit identified critical/high issues C1–C5 and H1–H7. Current status appears to be:

| Prior ID | Prior issue | Current assessment |
|---|---|---|
| C1 | Missing migrations for summaries/topic assignments | **Fixed.** Migrations and migration parity tests now cover these tables. |
| C2 | No migration parity test | **Fixed.** `backend/tests/test_migration_parity.py` runs Alembic against PostgreSQL in CI. |
| C3 | ORM lacked FK declarations | **Mostly fixed.** Core user/session/work/file/shelf/rack/tag relations now have FKs. Some weaker edges remain, especially `Location.agent_id`, `Reference`, and `CitationMention` relationships. |
| C4 | JSONB vs JSON mismatch | **Partially fixed.** `AuditEvent.details` uses a JSONB variant on PostgreSQL. Other JSON-like fields remain generic JSON or text where appropriate for MVP. |
| C5 | Docker builds accidentally used production stages | **Fixed for development Compose.** `api`, `worker`, and `frontend` should explicitly target `development`; production override targets `production`. |
| H1 | `httpx2` unpinned | **Fixed.** Pinned as `httpx2==2.4.0`. Longer-term question: why `httpx2` rather than standard `httpx`? |
| H2 | Semantic search writes embeddings on read path and scans in Python | **Still open.** This remains acceptable for small local corpora, but not the intended final architecture. |
| H3 | Duplicate scan/import O(n²) risk | **Partially fixed.** DOI/arXiv paths improved; fuzzy title matching and candidate upsert are still request-bound and will become expensive at larger scale. |
| H4 | Unauthenticated stub agent endpoints | **Fixed/contained.** Agent manifest/teleport endpoints require approved agent token and currently return 501; old registration route returns 410. |
| H5 | No production build/config | **Substantially improved.** Multi-stage backend/frontend Dockerfiles and `docker-compose.prod.yml` exist. Production smoke/deployment validation is still limited. |
| H6 | Local env prefix mismatch | **Fixed.** `.env.example` uses `PARACORD_*` keys. |
| H7 | Embeddings JSON instead of pgvector/local model | **Still intentionally open.** Current hash-BOW embeddings are an MVP approximation. |

## 3. High-priority findings

### A1. Managed-upload extraction path gap

**Severity:** High
**Area:** imports, storage, extraction, worker pipeline
**Status:** likely bug

The upload endpoint accepts PDFs, stores them in the managed library, creates a `managed_path` location, commits the import, and queues extraction. However, the extraction service resolves only a `server_path` location for the file. The file-streaming endpoint already supports both `server_path` and `managed_path`, but extraction does not.

Current expected failure mode:

1. `POST /imports/upload` stores a PDF under the managed library root.
2. `import_uploaded_pdf()` creates a `Location(location_type="managed_path", ...)`.
3. The import endpoint queues extraction for that file.
4. Worker calls `extract_and_store(file_id=...)`.
5. `extract_and_store()` searches for `Location.location_type == "server_path"` only.
6. It raises a “No server-path location available for extraction” style error.

This matters because managed upload/teleport is a core user workflow.

**Recommended fix:**

Create a shared resolver for backend-readable file locations:

```python
resolve_backend_readable_pdf_path(db, file_id, settings) -> Path
```

It should support at least:

- `server_path`, validated against configured `server_allowed_roots`;
- `managed_path`, validated against `managed_library_root`;
- later, agent-backed file IDs should be streamed through the agent/teleport path rather than resolved as raw paths.

Then use this resolver in:

- `files.py` streaming;
- `extraction.py`;
- any future OCR/hash reprocessing path.

**Acceptance tests:**

- Upload a tiny PDF via `/imports/upload`.
- Confirm a `managed_path` location is created.
- Run extraction job or service function against that file.
- Assert a `RawTeiDocument` or extraction failure record is stored as expected, without “server_path missing.”

### A2. Documentation drift after rapid fixes

**Severity:** High
**Area:** docs, agent onboarding, development reliability
**Status:** open

Several docs now describe pre-fix behavior. This is risky because future coding agents may follow stale guidance and reintroduce solved problems.

Examples:

- `docs/AUDIT.md` still lists some now-fixed items as open.
- `docs/architecture/api_surface.md` still describes old unauthenticated/stub agent endpoints and removed citation-context routes.
- `docs/architecture/data_model.md` still describes missing ORM FKs and JSONB mismatch even though core fixes landed.
- Some runbooks still contain older wording around `make check`, Docker-vs-host checks, frontend lockfile handling, and optional profile shutdown.
- `ROADMAP.md` underrepresents implemented M3–M7 backend functionality compared with `PROGRESS.md` and `CHANGELOG.md`.

**Recommended fix:**

Do a focused documentation maintenance PR:

1. Append this addendum to `docs/AUDIT.md`.
2. Refresh `docs/architecture/api_surface.md` from the current router/endpoints.
3. Refresh `docs/architecture/data_model.md` from current SQLAlchemy models and Alembic migrations.
4. Update `ROADMAP.md` status to match `PROGRESS.md` and `CHANGELOG.md`.
5. Update runbooks to reflect current Makefile targets, especially `hard-down`, `frontend-check`, `frontend-lock`, and the Docker/host CI split.

**Agent rule suggestion:** Treat stale docs as failing implementation, not cosmetic debt. For this project, docs are part of the multi-agent coordination system.

### A3. `make ready` / local CI parity still incomplete

**Severity:** High/Medium
**Area:** developer workflow, CI parity
**Status:** open

Current Makefile flow is much improved, but local readiness still does not obviously mirror the real CI surface.

Important current facts:

- `ready` runs `fix`, `precommit`, and `check`.
- `check` runs lint plus backend/agent tests.
- frontend has `frontend-check`, but `ready` does not necessarily include it.
- migration parity exists and is covered by full pytest in GitHub CI because CI has a real PostgreSQL service. Local `make test` in containers may or may not exercise migration parity depending on container DB connectivity and skip behavior.
- CI frontend job runs `npm ci`, tests, and build.

**Recommended Makefile policy:**

Use two explicit tiers:

```makefile
check: lint test test-migrations
frontend-check: frontend-install frontend-test frontend-build
ready: fix precommit check frontend-check
ci: lint test test-migrations frontend-check check-secrets
```

If `test` already includes `test_migration_parity.py` under the same real PostgreSQL path, `test-migrations` may be redundant. But making it explicit is clearer and protects against local skip behavior.

**Acceptance criteria:**

- `make ready` fails if frontend build fails.
- `make ready` fails if migration parity fails.
- Docs say exactly what `make ready` covers.

## 4. Medium-priority findings

### B1. GROBID integration still lacks spec-level configuration and coordinates

**Severity:** Medium/High
**Area:** extraction, citation contexts, privacy profiles, PDF anchoring
**Status:** partial

Current GROBID integration calls full-text extraction and includes useful options such as consolidation, raw citations, and sentence segmentation. However, options appear hardcoded rather than driven by settings/import policy, and the client still has a TODO around extraction options including coordinates.

The specification requires:

- header/reference/full-text extraction;
- raw citations;
- citation contexts;
- PDF coordinates for anchors and PDF reader overlays;
- configurable consolidation/privacy behavior;
- eventually OCR fallback for scanned PDFs.

**Recommended fix:**

Add a `GrobidSettings` section to backend settings and config parsing:

```yaml
grobid:
  url: http://grobid:8070
  consolidate_header: true
  consolidate_citations: true
  include_raw_citations: true
  segment_sentences: true
  include_coordinates:
    - ref
    - biblStruct
    - s
    - p
```

Then pass these settings into `GrobidClient.process_fulltext_document()`.

**Follow-up:** Expand TEI parser/tests to store coordinate-bearing citation mentions and verify at least one coordinate can be surfaced through the reader API.

### B2. Semantic search and embeddings are useful placeholders, not the final design

**Severity:** Medium/High
**Area:** search, AI, performance
**Status:** intentional MVP divergence

Current semantic search uses feature-hashed bag-of-words vectors stored as JSON and cosine similarity computed in Python. Embeddings may be created lazily during search, which mutates state on a read path.

For hundreds or a few thousand papers, this may be acceptable as a no-dependency prototype. It is not aligned with the end-state specification, which calls for local embedding models and pgvector-like indexing.

**Risks:**

- first search after many imports may become slow and write-heavy;
- concurrent searches can race to create embeddings;
- no model/version provenance comparable to a real embedding pipeline;
- no vector index.

**Recommended staged fix:**

1. Keep current hash-BOW as `embedding_model="hash-bow-v1"`.
2. Move embedding creation to import/background jobs.
3. Add uniqueness constraint on `(work_id, model)` and use upsert.
4. Add pgvector column/table when a real embedding model is introduced.
5. Add an embedding provider interface:
   - `hash_bow` for tests/offline minimal mode;
   - `ollama` or `sentence-transformers` for local model mode.

### B3. Topic modeling and summaries are MVP approximations

**Severity:** Medium
**Area:** AI, literature-review functionality
**Status:** acceptable for M7 scaffold, not final

Current topic modeling is TF-IDF plus deterministic k-means. Current summaries are extractive/frequency-based summaries from title/abstract/metadata, not local LLM summaries.

This is a good low-dependency baseline. It should be presented as such in UI/docs. Avoid implying that BERTopic/local LLM summarization is already implemented.

**Recommended next steps:**

- Rename UI labels from “AI summary” to “Extractive summary” or “Local summary (baseline)” where appropriate.
- Store summary provenance clearly: `summary_type`, `model`, `source_sections`.
- Add future provider interface for local LLMs.
- Implement shelf/rack citation-context summaries separately from paper abstract summaries.

### B4. Duplicate detection improved but still has scaling risks

**Severity:** Medium
**Area:** duplicate/version resolution, import performance
**Status:** partial

DOI and arXiv duplicate paths appear improved with direct SQL lookup. Fuzzy title matching still loops through all works, and candidate upsert can be chatty.

For the target range of hundreds to thousands of PDFs, this is probably manageable initially. It will become a bottleneck during large imports or repeated duplicate scans.

**Recommended fix later:**

- Use normalized-title blocking prefixes or trigram indexes on PostgreSQL.
- Add an import-batch duplicate job rather than request-bound full scans.
- Use bulk insert/upsert for duplicate candidates.
- Store text fingerprints once instead of recomputing.

### B5. Agent architecture is correct, but implementation is still mostly scaffold

**Severity:** Medium
**Area:** local agent, teleport, remote-file security
**Status:** conceptually aligned, functionally incomplete

The agent folder and server-side agent enrollment now align with the original security concept: no arbitrary filesystem browsing, approved agent tokens, and future opaque file IDs.

Current agent implementation still lacks the central value proposition:

- no full registration/pairing flow;
- no durable local index beyond simple manifest behavior;
- no implemented server manifest ingestion;
- no implemented teleport/upload session;
- `agent/teleport.py` still accepts a raw path in an internal helper and contains a TODO to resolve by `local_file_id`.

**Recommended work package:**

Implement “Agent M1” as a separate vertical:

1. Agent local SQLite/index of scanned files.
2. Server `AgentFile` model/table.
3. Manifest upload endpoint requiring approved agent token.
4. Teleport session endpoint requiring user authorization and agent token validation.
5. Chunked upload or one-shot upload with SHA-256 verification.
6. Audit events for manifest, teleport requested, teleport completed/failed.
7. Remove/replace raw-path teleport helper before exposing any command.

### B6. Frontend is broad but not yet the intended app UX

**Severity:** Medium
**Area:** frontend, user workflow
**Status:** functional early console

The frontend now exposes many backend functions in one place: login, filters, import, upload, shelves/racks/tags, exports, citation graph, summaries, topics, duplicate review, reader/notes, and admin-ish flows.

This is good for rapid backend validation but not yet the final application experience described in the spec.

Remaining UX gaps:

- no route-level app structure for library/shelves/racks/file view/reader/admin/settings;
- PDF reader is not yet a full PDF.js anchored reader with citation/annotation overlays;
- graph view is an edge list, not Cytoscape/interactive graph;
- no dedicated metadata-conflict dashboard;
- no import queue/worker status dashboard;
- no clear shelf/rack dashboard summaries.

**Recommended next UI refactor:**

Split the current large page into route-level views:

- Library/Search;
- Rack/Shelf browser;
- File view;
- Work detail/reader;
- Citation graph;
- Import/queue;
- Duplicates/metadata review;
- Admin/settings.

Keep the current page temporarily as a “debug console” if it is useful for development.

### B7. Remaining model/spec divergences should be explicitly accepted or scheduled

**Severity:** Medium
**Area:** data model, spec alignment
**Status:** open design decisions

Current implementation has sensible MVP simplifications, but they should be documented so agents do not treat them as accidental bugs.

Notable divergences:

- `MetadataAssertion.value` is a scalar string, not typed JSON with richer conflict/resolution state.
- `CitationMention` stores simple coordinate fields but not a richer coordinate model/multi-box data structure.
- `Embedding.vector` is JSON, not pgvector.
- `TopicAssignment` is collapsed and does not preserve full topic-model run metadata as specified.
- Agent data model is minimal and does not yet represent agent files/manifests/teleport sessions.
- `Location.agent_id` is not a strong FK relationship yet.
- References/citation mentions are not fully FK-connected to works/sections/paragraphs in the model.

**Recommended action:**

Add a section to `docs/architecture/data_model.md` called “Intentional MVP simplifications” and mark each as:

- accepted long-term simplification;
- scheduled for a milestone;
- needs design decision.

### B8. Security docs overstate implemented encryption and controls

**Severity:** Medium
**Area:** security documentation, compliance, user trust
**Status:** open

`SECURITY.md` and `docs/runbooks/secrets_management.md` describe encryption-at-rest expectations for recoverable sensitive fields. The current code does not appear to implement a general encryption helper or encrypted columns. This was noted in the previous audit and remains a documentation/implementation mismatch.

Other security/config items that appear documented but not fully implemented:

- failed-login lockout settings;
- secret-key env indirection in YAML beyond basic settings;
- GROBID consolidation privacy profiles;
- SSRF hardening for URL import beyond basic identifier paths;
- comprehensive production deployment hardening.

**Recommended fix:**

Short term:

- Reword docs to say encryption-at-rest is a planned requirement unless it is implemented.
- Add an issue/backlog item for encryption helper and columns if needed.

Medium term:

- Implement a small encryption utility if the app will store recoverable agent tokens, API keys, or other non-password secrets.
- Add tests proving plaintext secrets are not persisted.

### B9. DOI/identifier normalization should be centralized everywhere

**Severity:** Medium/Low
**Area:** metadata quality, deduplication
**Status:** partial

The project has DOI normalization and a migration for normalized DOI handling. Some paths still appear to manipulate identifiers manually or patch DOI fields directly.

Examples to inspect/fix:

- `/imports/identifier` manually strips `https://doi.org/` rather than calling the central normalizer.
- `PATCH /works/{id}` updates arbitrary fields and may set `doi` directly without normalization.
- `normalize_doi()` could likely handle more resolver prefixes, such as `dx.doi.org`.

**Recommended fix:**

- Make all DOI assignments go through a single helper/service function.
- Add tests for uppercase DOI, `doi:`, `https://doi.org/`, `http://doi.org/`, `https://dx.doi.org/`, trailing punctuation/spaces.
- Consider storing both raw external identifier and normalized canonical DOI where useful.

### B10. Production path exists but needs a smoke test

**Severity:** Low/Medium
**Area:** deployment, Docker
**Status:** improved but not proven

The backend and frontend Dockerfiles now have production targets, and `docker-compose.prod.yml` selects them. That is a major improvement. However, CI and Makefile do not appear to prove the production image path works.

**Recommended fix:**

Add a light production smoke target:

```makefile
prod-smoke:
	$(COMPOSE_PROD) build api frontend
	$(COMPOSE_PROD) run --rm --no-deps api python -c "import app.main; print('api import ok')"
```

A fuller version can start Postgres/API and hit `/api/v1/health`, but avoid making local development too slow.

## 5. Specification and roadmap alignment matrix

| Spec capability | Current state | Assessment |
|---|---|---|
| Central server + local workstation agent | Server strong; agent scaffold/enrollment/scanner only | Concept aligned, implementation incomplete |
| Keep PDFs in original folders | Server-folder import implemented | Good for same-machine/server roots |
| Teleport PDFs to managed server library | Managed upload implemented; agent teleport not implemented; extraction gap for managed PDFs | Partial; fix A1 and agent M1 |
| External metadata enrichment | arXiv/Crossref/OpenAlex/Semantic Scholar scaffolding present | Good MVP, needs provenance/conflict polish |
| GROBID extraction | Live client/TEI parser/storage present | Good base; coordinates/config/OCR missing |
| Work/version/file model | Core present | Good MVP; multi-paper file/version UX still needs depth |
| Shelves/racks/tags | Implemented backend and exposed frontend | Good MVP |
| Shelf/rack/file views | Backend filters exist; frontend still single-page | Partial UX |
| PDF reading | Basic embedded reader/streaming | Not yet PDF.js anchored reader |
| Annotations separate from PDFs | Implemented basic notes/highlights | Good MVP; needs anchored coordinate UX |
| Citation contexts | Basic citation mentions/contexts | Good MVP; coordinate-quality depends on extraction/parser |
| Local citation graph | API and frontend edge-list | Good base; not interactive graph yet |
| Citation summaries across shelves/racks | Extractive scope summaries; graph summary counts | Partial; no citation-context thematic summaries |
| Citation export | Multiple formats/scopes implemented | Strong MVP; CSL-style formatted bibliographies remain future |
| Duplicate/version handling | Backend review/actions fairly strong | One of stronger areas; performance later |
| Topic modeling | TF-IDF/k-means baseline | Useful placeholder, not BERTopic |
| Local AI summaries | Extractive baseline | Useful placeholder, not local LLM |
| Semantic search | Hash-BOW JSON vectors | Useful placeholder, not pgvector/local embeddings |
| Access control/no guest | Implemented role model, no guest role | Strong base |
| Audit logging | Broadly used | Strong base; ensure all new mutations log |
| Production deployment | Multi-stage images and prod compose | Improved, needs smoke/hardening |

## 6. Algorithmic efficiency review

### Imports

Server-folder import is acceptable for hundreds/thousands of PDFs. The main future concern is repeated full-folder scans without persistent file-system watch state or incremental mtime/hash cache. For the target scale, hash-first duplicate handling is fine.

**Recommendation:** keep import request simple now; add background batch import and progress events before importing very large libraries.

### Duplicate detection

Exact hash/identifier paths are efficient enough. Fuzzy title matching over all works is acceptable now but will degrade with repeated large imports.

**Recommendation:** add blocking/indexing before large-scale usage. Do not prematurely optimize unless import latency becomes a user-visible problem.

### Metadata enrichment

Online enrichment is acceptable because user explicitly allowed metadata leaks to reference servers. The pollution risk is controlled partly by user-confirmed locks and source priority, but provenance/conflict states could be richer.

**Recommendation:** add field-level conflict dashboard and do not automatically overwrite user-confirmed fields.

### Extraction

GROBID extraction is inherently background-job work. Current queue integration is appropriate. The managed-path extraction bug should be fixed before deeper extraction work.

**Recommendation:** after A1, add extraction status/error fields visible in UI.

### Semantic search

Current Python cosine over JSON vectors is okay for early testing and small libraries, but not optimal. Read-path embedding creation is the bigger design smell.

**Recommendation:** background embeddings and pgvector when real embeddings land.

### Topic modeling

TF-IDF/k-means is fast and dependency-light. It is a reasonable baseline. It should not be over-specified in tests; exact cluster sizes are not guaranteed.
User request: the best solution is likely to combine both approaches or give users a choice.
I.e., there would be two option for semantic search.

**Recommendation:** keep tests semantic rather than exact-cluster-count unless a deterministic algorithm with such guarantee is implemented.

### Citation graph

Graph building over local works/references is fine for thousands of nodes if kept simple. Interactive rendering should eventually move to Cytoscape or similar and avoid huge all-at-once DOM/text edge lists.
Caution! Do not sacrifice detailed interactive graph if it is not expected that the number of nodes will be large (more than a few thousand).
Ideally, give users an option to switch between different rendering modes.

**Recommendation:** add server-side graph filtering/limits and frontend progressive rendering before broad external graph expansion.

### Exports

Current export generation is lightweight and appropriate. CSL-style formatting may add complexity, but it is the correct tool for real “free text” bibliographies.

**Recommendation:** keep current exports; add citeproc/CSL style selection later.

## 7. Tooling and framework assessment

| Tool/library | Current fit | Notes |
|---|---|---|
| FastAPI | Good | Appropriate for typed API and modular services. |
| SQLAlchemy/Alembic | Good | Not overkill; schema is central. Early migration history can still be squashed before real data. |
| PostgreSQL/pgvector image | Good | Correct long-term choice. pgvector not fully used yet. |
| Redis/RQ | Good but underused | Appropriate once extraction/AI/import queues become more active. |
| Docker Compose | Good | Development/prod target split fixed. Profiles need docs clarity. |
| Svelte/Vite/Vitest | Good | Lightweight frontend fit. UI needs decomposition. |
| PDF.js dependency | Good future fit | Not yet used as full reader. |
| Cytoscape dependency | Good future fit | Not yet used for real graph UI. |
| GROBID | Good | Core extraction engine; config/coordinate handling next. |
| Ollama | Good optional future fit | Not yet integrated beyond config/service. |
| httpx2 | Questionable | Pinned now, but standard `httpx` may be less surprising unless `httpx2` is intentional. |
| PyMuPDF | Good | Useful for preview/text fallback and PDF metadata. |
| Ruff/pre-commit/codespell/gitleaks | Good | Current hygiene stack is productive. |

## 8. Suggested next work packages for coding agents

### Package 1 — Managed upload extraction fix

**Goal:** uploaded managed PDFs can be extracted just like server-folder PDFs.

Tasks:

- Add shared backend file resolver for `server_path` and `managed_path`.
- Use it in extraction and streaming.
- Add tests for uploaded PDF extraction path.
- Update `PROGRESS.md` and this audit if completed.

Acceptance:

- Upload endpoint creates managed file.
- Extraction service can read managed file.
- No raw path exposure.

### Package 2 — Documentation truth refresh

**Goal:** make docs match current code.

Tasks:

- Append this addendum to audit docs.
- Refresh API surface doc from current routers.
- Refresh data model doc from current models/migrations.
- Refresh ROADMAP status.
- Refresh runbooks for current Makefile and CI behavior.

Acceptance:

- No doc still claims old unauthenticated agent stubs.
- No doc claims JSONB/FK issues that are already fixed without qualification.
- Makefile runbook describes `frontend-check`, `frontend-lock`, `hard-down`, production targets.

### Package 3 — GROBID config and coordinates

**Goal:** make extraction options configurable and prepare PDF anchors.

Tasks:

- Add settings for GROBID options.
- Pass settings to client.
- Store at least basic coordinate data from TEI where available.
- Add tests with fixture TEI containing coordinates.

Acceptance:

- Consolidation and coordinate options are controlled by config.
- Citation mentions can expose page/coordinate data.

### Package 4 — Agent M1 manifest/teleport

**Goal:** deliver the main remote-workstation value.

Tasks:

- Agent local index.
- Server `AgentFile`/manifest ingestion.
- Token-authenticated manifest endpoint.
- User-authorized teleport flow.
- Checksum and audit logging.

Acceptance:

- Server never asks for raw path.
- Agent never exposes raw path API.
- Teleported file becomes managed library file.

### Package 5 — Frontend view decomposition

**Goal:** move from debug console to app shell.

Tasks:

- Introduce route/view structure.
- Split library, shelf/rack, import, reader, graph, duplicate/admin views.
- Keep current functionality accessible.

Acceptance:

- No single massive page is required for normal workflows.
- Core workflows are discoverable.

### Package 6 — Semantic search pipeline hardening

**Goal:** eliminate read-path writes and prepare real embeddings.

Tasks:

- Move embedding creation to import/background job.
- Add uniqueness/upsert by `(work_id, model)`.
- Add provider interface.
- Keep hash-BOW provider for tests.

Acceptance:

- Search does not create embeddings during normal read path unless explicitly requested.
- Tests pass with deterministic provider.

## 9. Recommended near-term priority order

1. **Fix managed upload extraction path.** This is the most concrete correctness bug.
2. **Update stale architecture/runbook/audit docs.** Prevent future agents from following old state.
3. **Make `make ready` include migration and frontend checks.** Prevent local/CI drift.
4. **Add GROBID settings and coordinate parsing tests.** Enables the citation-context/PDF-reader roadmap.
5. **Implement agent manifest/teleport vertical.** Delivers the distinctive external-server/local-agent requirement.
6. **Decompose frontend UX.** Improves usability and reveals API gaps.
7. **Move embeddings/topics/summaries toward provider-based architecture.** Keep current lexical versions as baseline/test providers.

## 10. Bottom line

PaRacORD is now a coherent early application, not merely a scaffold. The backend architecture and development hygiene are strong enough to support parallel agent work. The main danger is now **coordination drift**: docs, roadmap, and audit state must be kept as accurate as the code, because this project is explicitly using documents to guide multiple coding agents.

The highest-value technical fix is the managed-upload extraction gap. The highest-value process fix is making `make ready` and docs fully mirror the current CI/development reality. The highest-value roadmap fix is to clearly label current AI/search/topic features as lightweight baselines while preserving the path toward GROBID coordinates, pgvector/local embeddings, BERTopic-like topics, local LLM summaries, and a real PDF.js/Cytoscape frontend.

---

# AUDIT Re-validation — 2026-06-29

Every prior finding was re-checked against the actual tree at `HEAD` on 2026-06-29 (not the
audit's original commit), partly via targeted code inspection. The ordered remediation plan now
lives in **`docs/WORKPLAN.md`**; this section only records validity/status so the audit stops
drifting (addresses finding **A2**).

## Status of prior findings

| ID | Finding | Status @ 2026-06-29 | Evidence / note |
|---|---|---|---|
| C1 | summaries/topics migration missing | **FIXED** | migration `0010`, parity test covers it |
| C2 | no migration parity test | **FIXED** | `test_migration_parity.py` + CI Postgres |
| C3 | ORM missing FKs | **MOSTLY FIXED** | core relations have FKs; weak edges remain (`Location.agent_id`, `Reference`, `CitationMention`) → Stage 7 |
| C4 | JSONB vs JSON | **PARTIAL (accepted)** | `AuditEvent.details` is JSONB; others stay JSON for MVP → Stage 7 |
| C5 | docker built prod by mistake | **FIXED** | commit `c274605`; compose pins `target: development` |
| H1 | `httpx2` unpinned | **FIXED** | pinned `httpx2==2.4.0` (keep — Pydantic-maintained fork) |
| H2 | semantic search writes on read path | **OPEN (guarded)** | `semantic_search.py:70` still inserts+commits on read; guarded against the unique constraint by a Python set check → Stage 6 |
| H3 | dedup O(n²) | **PARTIAL** | DOI (`duplicate_detection.py:147`) + arXiv-base now SQL; fuzzy-title still `SequenceMatcher` over all works (`:216,:222`); no `rapidfuzz` → Stage 7 |
| H4 | unauth agent stubs | **FIXED** | manifest/teleport require approved-agent token, return 501; register → 410 |
| H5 | no prod build | **FIXED** | multi-stage Dockerfiles + `docker-compose.prod.yml` |
| H6 | `.env` prefix mismatch | **N/A (operator)** | `.env.example` correct; regenerate local `.env` |
| H7 | embeddings not pgvector | **BY DESIGN** | JSON vectors documented as portable; pgvector deferred → Stage 7 |
| A1 | managed-path extraction gap | **FIXED (2026-06-29)** | shared `services/file_paths.py::resolve_backend_readable_pdf_path` resolves `server_path` + `managed_path` with root validation; `extract_and_store()` and `stream_file` both use it; regression test `test_extract_and_store_reads_managed_path` |
| A2 | doc drift after fixes | **OPEN → being closed** | this section + WORKPLAN + refreshed PROGRESS/ROADMAP/CHANGELOG |
| A3 | `make ready` ≠ CI surface | **OPEN** | `ready: fix precommit check`, `check: lint test`, `test: test-api test-agent` — no `frontend-check`/`test-migrations` in `ready`/`ci` → **Stage 1** |
| B1 | GROBID config/coordinates | **OPEN** | flags hardcoded `grobid_client.py:23-26`, TODO `:32`, no coordinate parsing; `config.py` has only `grobid_url` → **Stage 2** |
| B6 | frontend single-page | **PARTIALLY ADDRESSED** | hash router + Admin UI landed (`94151b4`); PDF.js reader, Cytoscape graph, metadata-review UI still pending → Stages 3–4 |
| B5 | agent scaffold only | **OPEN** | enrollment works; manifest/teleport stubs → **Stage 5** |
| P1/item4 | `arxiv_base_id` + UNIQUE | **FIXED** | migration `0011`, partial unique indexes |
| P1/item5 | DOI normalization | **FIXED** | normalize-at-write + SQL pushdown, migration `0012` |
| P2/item9 | scope summaries | **FIXED** | `POST /ai/summaries` real implementation |
| P2/item10 | import expansion | **PARTIAL** | upload + identifier done (frontend+backend); upload extraction now works (A1 fixed); RIS/CSL pending → Stage 4 |

## Confirmed-valid open items, by priority

1. ~~**A1** (HIGH correctness) — managed-path extraction.~~ **FIXED 2026-06-29.**
2. **A3** (process) — make local readiness mirror CI.
3. **B1** (extraction) — GROBID settings + coordinates; gates the PDF.js reader.
4. **B6 remainder** — PDF.js reader, Cytoscape graph, metadata-review UI.
5. **B5** — agent manifest/teleport vertical.
6. **H2** — embeddings off the read path; provider interface.
7. Deferred (Stage 7): H3 perf, C3/C4 remainder, H7 pgvector, export polish, M0 auth hardening,
   security-doc truthfulness (M2/M3/M4/M5), backups, prod smoke.

The audit's own top-3 priorities (managed-upload extraction, `make ready`/doc parity, then
GROBID coordinates) are preserved as Stages 1–2 of `docs/WORKPLAN.md`.

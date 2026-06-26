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

### H1 — `httpx2` dependency: unpinned niche fork on the egress path  [HIGH]
`backend/requirements.txt` and `agent/requirements.txt` list `httpx2` (unpinned); imported as
`import httpx2 as httpx` in `metadata_enrichment.py:14`, `grobid_client.py:5`, and the agent.
**Verified:** `httpx2 2.4.0` *is* installed in the built image and works (plain `httpx` is absent),
so the running stack is fine — but `httpx2` is an obscure fork, unpinned, and its public-index
availability is unconfirmed, so a fresh non-container `pip install -r requirements.txt` may fail
and the build is not reproducible. The code uses the standard httpx API. **Fix:** pin it
(`httpx2==2.4.0`) and document/vendor the install source, or switch back to mainline
`httpx>=0.27` (drop-in) unless there is a specific reason for the fork.

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

### H5 — No production build/config; only a dev stack exists  [HIGH]
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

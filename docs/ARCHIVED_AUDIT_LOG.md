# PaRacORD — Archived audit log (resolved & stale items)

Historical record of audit findings that are **resolved, superseded, or accepted as-is**. Current
open issues live in `AUDIT.md`; open choices in `DISCUSSIONS.md`. The full original documents
(the 1 100-line 2026-06-25 audit + addenda, `DECISIONS.md`, `NEEDS_DISCUSSION.md`, `FOLLOWUP.md`)
are preserved verbatim in the gitignored `docs/archive/audit_docs_pre-2026-07-02.zip` and in git
history; this file is the condensed, chronological digest.

---

## 2026-06-25 — First full audit (at M7)

Findings C1–C5, H1–H7 + functional-fidelity matrix. Final status (per the 06-26/06-29
re-validations):

| ID | Finding | Outcome |
|---|---|---|
| C1 | `summaries`/`topic_assignments` had no migration (prod-breaking) | FIXED — migration 0010 + parity test |
| C2 | Migrations never run in tests | FIXED — `test_migration_parity.py`, CI Postgres, autogenerate-clean assertion |
| C3 | ORM missing FKs vs migrations | FIXED (core); weak edges closed in later stages |
| C4 | JSONB vs generic JSON | PARTIAL/ACCEPTED — JSONB where queried; rest generic by design |
| C5 | `make build` built production images | FIXED — explicit `target: development` |
| H1 | `httpx2` unpinned | FIXED — pinned; legitimacy verified 2026-07-02 (see below) |
| H2 | Semantic search wrote on the read path | FIXED — background indexing, read-only search |
| H3 | O(n²) dedup/import scans | FIXED — SQL pushdown, `arxiv_base_id`, blocking + rapidfuzz |
| H4 | Unauthenticated agent stubs | FIXED — token-gated, stubs removed |
| H5 | No production build | FIXED — multi-stage images + `docker-compose.prod.yml` + prod-smoke |
| H6 | `.env` prefix mismatch | FIXED — operator regenerated from `.env.example` |
| H7 | Embeddings JSON, no pgvector | RESOLVED — per-model `vector(dim)` + HNSW registry (remainder: DISCUSSIONS D37) |

The functional-fidelity gaps of this audit (single-page UI, iframe reader, text edge-list graph,
TF-IDF "semantic" search, stub agent) were all closed by Stages 3–9: tabbed shell, PDF.js reader
with coordinate-anchored annotations, Cytoscape graph, provider-based embeddings + structured
search, full agent manifest/teleport vertical (SPEC §32).

## 2026-06-26 — Addendum

- **A1** managed-upload extraction gap — FIXED (shared `resolve_backend_readable_pdf_path`).
- **A2** documentation drift — FIXED (re-validation sections + doc refresh; superseded again by
  this consolidation).
- **A3** `make ready` ≠ CI surface — FIXED (`ready`/`ci` include migrations + frontend).
- **B1–B10** (GROBID config/coordinates, semantic-search placeholders, MVP topics/summaries,
  dedup scaling, agent scaffold, frontend UX, model/spec divergences, security-doc overstatement,
  DOI normalization, prod smoke) — all FIXED or ratified in Stages 1–9 + the 2026-06-30
  functional-gap closure; the deliberate leftovers became DECISIONS items (now AUDIT/DISCUSSIONS).

## 2026-06-30 — Functional-gap closure + efficiency review

All previously-open §2/§3/§4 functional items closed (auth extras, read/view audit events, §9.3
user/agent fields, structured search operators, annotation search/export, per-field metadata
locks, reading queue, keyword extraction, OCR-needed signal, topics accept-as-tag, related
papers, graph import-missing-ref, styled export, autogenerate parity, M2–M5 security-doc items).

Efficiency findings **E1–E7** (export author N+1, `paper.viewed` write-per-GET, agent `last_seen`
commit-per-request, Python-scan search paths, `reindex_status` full load, `get_ai_config`
rollback foot-gun, full-rescan imports) — **all fixed** (commit `9ec7d64`), including the agent
re-hash-every-scan variant of E7. Spot-checked still present and effective on 2026-07-02.

## 2026-07-02 — Six-pass consolidated audit (security ×2, efficiency, stability, tech-stack,
doc-verification)

Full text in `docs/archive/audit_docs_pre-2026-07-02.zip` → `DECISIONS.md`. Verified with
`make test-full` (660 backend + 32 agent green), `make frontend-check` (75 green + build), CI
green. Auto-fixed the same day (commits `e2375ab`…`0844279` + `5d63526`):

**Security fixes**
- Agent web-GUI token file `web.json` → `0600`/`0700` (was world-readable; local user could steal
  the GUI token, browse readable dirs, repoint `server_url`, exfiltrate the agent bearer token).
- DOM XSS in agent GUI banner (server-supplied `name`/`status` + `server_url` now escaped).
- `state.sqlite3` + `secrets.json` created `0600` without a perm race.
- IDOR: `GET /imports/{batch_id}` now owner-clamped like the list endpoint.
- `ensure_e2e_user.py` (hardcoded-password admin) refuses to run outside development/test.

**Stability fixes**
- RQ default job timeout 180 s → 900 s (OCR jobs were killed mid-run, permanently, for scanned
  PDFs).
- `extract_pdf_job` failure handler rolls back before marking `extract_failed` (could persist a
  half-done reference wipe or mask the root cause).
- Chunk/embed jobs enqueued even when enrichment fails (offline installs never got searchable
  papers).
- HNSW `CREATE INDEX` failure no longer aborts the reindex transaction (savepoint; degrades to
  exact scan).
- Reindex commits in batches (an Ollama flap used to lose all progress).
- `make backup`/`restore` made transactional (`--clean --if-exists`, `ON_ERROR_STOP`, api/worker
  stopped; restore previously produced a silent inconsistent merge and printed success).
- Agent sync: failed `get_my_files()` skips the action phase (was a full re-upload storm).
- Derived OCR copies deleted on `index_and_extract` discard (privacy: "PDF discarded after
  extraction" used to leave a full OCR'd copy on disk).
- Model-delete DDL takes a 5 s `lock_timeout` → 409 instead of wedging chunk search.
- Concurrent duplicate-PDF import race → savepoint + re-select (was 500 + whole-batch rollback).
- GROBID timeouts now degrade like GROBID-down.
- Corrupted agent `state.sqlite3` moved aside and recreated; WAL + busy_timeout enabled.

**Performance fixes**
- Reused HTTP clients for Ollama embeds + enrichment (was one TCP handshake per chunk).
- Embedding-provider cache — SentenceTransformer weights load once per process, evicted on model
  delete (was a full model reload on every search/reindex; the single biggest latency win; closed
  NEEDS_DISCUSSION 3a).
- Citation graph no longer hydrates the entire `works` table per request.
- Duplicate-candidate listing batched (~1000+ SELECTs → 2 per page).
- Upload handlers no longer block the event loop (one 200 MB upload froze every request).
- Jobs-page poll resolution batched (~75 queries/poll → 4).
- Summary generation fetches TEI once (was twice, second unordered).
- Library status change updates the row in place (was a full refetch).
- Stale `bm25-*.npy` files pruned on save (was unbounded growth).

**Hygiene**
- Removed 4 dead dependencies never imported anywhere: `python-jose` (unmaintained, CVEs),
  `networkx`, `bibtexparser`, `pybtex` (+ README credits).
- Default-shelf placement hooked into the last creation paths (identifier import, agent
  ingestion, duplicate merge) — no paper can be created loose (closed NEEDS_DISCUSSION 2c).
- `ruff format` applied to the two files the fixer missed (CI lint failure `f4a8295`, fixed in
  `5d63526`).

**Verified sound (no action needed)**
- Path traversal: streaming/extraction share one validated resolver; OCR derived-copy fix has no
  sibling bugs. `vec_*` registry SQL injection-safe (slug allowlist + regex + int casts).
  Visibility clamps consistent on every work-returning endpoint incl. exports. find-on-web SSRF
  guards re-classify every redirect hop; enrichment refuses cross-host redirects.
- XXE not exploitable at pinned lxml ≥5.2. Privilege model (owner immutability,
  admin-can't-manage-admins) clean; mass assignment bounded by strict Pydantic schemas.
- All service ports bind 127.0.0.1 in both compose files. No secrets committed (git index
  checked). Admin scripts use `getpass`.
- Migrations 0030–0040 reversible/linear/tz-safe. Every outbound HTTP call has an explicit
  timeout; no bare `except:`; Redis-down degrades without 500s.
- Frontend: 6 runtime deps all used; PDF.js worker bundled locally; Cytoscape fields escaped.
  Agent loopback GUI design (token + HttpOnly SameSite cookie, opaque-id teleport) sound.
- Heavy services properly profile-gated (GROBID/Ollama/torch extractors opt-in).

**Closed / stale items from the superseded documents**
- `httpx2` supply-chain alarm — **false positive**: verified as the real Pydantic-maintained
  httpx fork (github.com/pydantic/httpx2; PyPI maintainer "Pydantic Services Inc."; first release
  2026-05, v2.5.0 current). Keep; bump pin at next rebuild (tracked in AUDIT D24).
- CSL/citeproc (FOLLOWUP §1) — DONE earlier: `render_styled()` delegates to citeproc-py, 7
  bundled styles, dynamic `GET /exports/styles`, golden tests.
- pgvector ANN (FOLLOWUP §3) — mostly DONE: per-model `vector(dim)` + HNSW on `work_chunks`
  (remainder → DISCUSSIONS D37).
- Playwright E2E (FOLLOWUP §4) — mostly DONE: 12 journeys (remainder → AUDIT D36a /
  DISCUSSIONS D36).
- NEEDS_DISCUSSION 2c (default-shelf hooks) and 3a (provider cache) — implemented (above).
- D21 (multimode per-model COUNT/hydration) — de-fanged by the provider cache; decided: leave.
- D23 (`httpx2`) — resolved by verification (above).

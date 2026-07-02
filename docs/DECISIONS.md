# PaRacORD — Consolidated audit & decision list (2026-07-02)

One document to read and decide from. It merges: a fresh full audit (security, efficiency,
stability, tech-stack suitability — six independent review passes at HEAD), the old
`FOLLOWUP.md` (Stage 8/9 seams), and `docs/NEEDS_DISCUSSION.md` (post-B1 items). Both older
documents are superseded by this one; every item in them was re-verified against the code
first — several were already resolved and are listed in §D, not re-asked.

Scale assumption used throughout: mostly single-user, but **a few users on a LAN is a
supported mode** — cross-user visibility, LAN transport, and shared-workstation issues are
treated as real product concerns. Efficiency judged at hundreds–few-thousand papers.

Test baseline before changes: 490 backend + 32 agent tests green.

---

## A. Fixed automatically in this pass (read, no decision needed)

All fixes below are committed (`8da72e3`…`2cde68a` + `a522303`) and verified: `make test-full`
green (**660 backend + 32 agent**, up from 690 total incl. new regression tests),
`make frontend-check` green (75 tests + build), ruff clean, `check_secrets` clean.

| ID | Fix | Where |
|---|---|---|
| SEC-1 | Agent web-GUI token file (`web.json`) now written `0600` in a `0700` dir (was world-readable; any local user could steal the GUI token, browse readable dirs, repoint `server_url` and exfiltrate the agent bearer token) | `agent/.../web_server.py` |
| SEC-2 | DOM XSS in agent GUI banner: server-supplied `name`/`status` + `server_url` now escaped before `innerHTML` | `agent/.../web.py` |
| SEC-3 | `state.sqlite3` (maps real paths of your papers) and `secrets.json` now created `0600` without a perm race | `agent/.../state.py`, `secrets.py` |
| SEC-4 | IDOR: `GET /imports/{batch_id}` now enforces the same owner-clamp as the list endpoint | `backend/.../endpoints/imports.py` |
| SEC-5 | `ensure_e2e_user.py` (hardcoded-password admin) refuses to run outside development/test env | `scripts/ensure_e2e_user.py` |
| STAB-1 | RQ jobs got a 900 s default timeout (was 180 s default — any OCR run legally allowed 300 s was killed mid-job, permanently for scanned PDFs) | `backend/.../workers/queue.py` |
| STAB-3 | `extract_pdf_job` failure handler rolls back before marking `extract_failed` (was: could persist a half-done reference wipe, or mask the root cause with `PendingRollbackError`) | `backend/.../workers/jobs.py` |
| STAB-2a | Chunking/embedding jobs are now enqueued even when metadata enrichment fails (was: an offline/flaky machine meant imported papers silently never became searchable) | `backend/.../workers/jobs.py` |
| STAB-4 | HNSW `CREATE INDEX` failure no longer aborts the whole reindex transaction (savepoint; degrades to exact scan as intended) | `backend/.../embedding_registry.py` |
| STAB-5 | Reindex commits in batches (was: one giant transaction; an Ollama flap at chunk 18 000 lost all progress) — also shrinks the 3e lock window | `backend/.../workers/jobs.py`, `chunk_embeddings.py` |
| STAB-6 | `make backup`/`restore` actually work now: `--clean --if-exists`, `ON_ERROR_STOP`, api/worker stopped during restore (was: restore over existing data silently produced an inconsistent merge and printed success) | `Makefile` |
| STAB-8 | Agent sync: a failed `get_my_files()` now skips the action phase (was: treated as "server empty" → full corpus re-upload storm every cycle) | `agent/.../agent_ops.py` |
| STAB-9 | Derived OCR copies deleted on `index_and_extract` discard (was: the "PDF is discarded after extraction" privacy promise left a full OCR'd copy on disk forever) | `backend/.../agent_files.py` |
| STAB-10 | Model delete DDL takes a 5 s `lock_timeout` → 409 instead of wedging chunk search behind a running reindex | `backend/.../embedding_registry.py`, `ai_admin.py` |
| STAB-11 | Concurrent duplicate-PDF import race handled with savepoint + re-select (was: 500 + whole-batch rollback) | `backend/.../storage.py` |
| STAB-13 | GROBID timeout now degrades like GROBID-down (was: only `ConnectError` mapped; overloaded GROBID → raw 500) | `backend/.../grobid_client.py` |
| STAB-14b | Corrupted agent `state.sqlite3` is moved aside and recreated (was: crash on every start) | `agent/.../state.py` |
| PERF-2 | Reused HTTP clients for Ollama embeds + enrichment (was: new TCP connection per chunk — 20 k chunks = 20 k handshakes) | `backend/.../embeddings.py`, `metadata_enrichment.py` |
| PERF-3a | Embedding-provider cache: SentenceTransformer weights load once per process, evicted on model unregister/delete (was: full model reload on every search/reindex — the single biggest latency win; closes NEEDS_DISCUSSION 3a) | `backend/.../embeddings.py`, `embedding_registry.py` |
| PERF-5 | Citation graph no longer hydrates the entire `works` table per request (scope-narrowed, output-identical) | `backend/.../citation_graph.py` |
| PERF-6 | Duplicate-candidate listing batched (was: ~1000+ SELECTs for a 500-row list) | `backend/.../duplicate_resolution.py` |
| PERF-7 | Large-PDF uploads no longer block the event loop (all async upload handlers now thread-pooled; was: one 200 MB upload froze every other request incl. health checks) | `backend/.../endpoints/imports.py`, `works.py`, `agents.py` |
| PERF-11 | Jobs-page poll backend resolution batched (was: ~75 queries per 4 s poll) | `backend/.../workers/queue.py` |
| PERF-13 | Summary generation fetches the TEI document once (was: twice, second unordered — could inspect a different row) | `backend/.../summarization.py` |
| PERF-10 | Library status change updates the row in place (was: full list refetch) | `frontend/.../LibraryPage.svelte` |
| PERF-1a | Stale `bm25-*.npy` index files cleaned up on save (was: unbounded index-dir growth) | `backend/.../bm25_index.py` |
| TECH-2 | Removed 4 dead dependencies never imported anywhere: `python-jose` (unmaintained, CVEs), `networkx`, `bibtexparser`, `pybtex` + their README credits | `backend/requirements.txt`, `README.md` |
| TECH-12 | Agent SQLite: WAL + busy_timeout (no more sporadic "database is locked" between sync loop and web GUI) | `agent/.../state.py` |
| ND-2c | Default-shelf placement hooked into the last creation paths (identifier import, agent ingestion, duplicate merge) — no paper can be created loose anymore (closes NEEDS_DISCUSSION 2c) | `backend/` (3 call sites) |

---

## B. Decisions needed

Ordered: security first, then correctness/robustness, then performance, then product scope.
Each has a recommendation — "agree" is a sufficient answer.

### B1 · Security

**D1. Login throttle is per-process, but production runs 2 gunicorn workers.**
The brute-force lockout budget silently doubles (and the `paper.viewed` debounce halves) with
`GUNICORN_WORKERS=2` (`backend/Dockerfile`); the throttle's own docstring assumes one process.
*Options:* (a) default `GUNICORN_WORKERS=1` — simplest, fine at this scale; (b) back the
throttle with the Redis that's already running. **Recommend (a) now, (b) if you ever raise
worker count.**

**D2. Browser token in `localStorage` + no CSP/security headers from nginx.**
These compound: paper titles/abstracts are external (PDF/Crossref) data rendered in the SPA;
any XSS anywhere reads the token → account takeover. The one `{@html}` was verified escaped,
but there is no second layer. *Options:* (a) add CSP + `X-Frame-Options` + `nosniff` +
`Referrer-Policy` to `frontend/nginx.conf` (compensating control, ~zero risk to legit assets
since everything is bundled/self-hosted); (b) also migrate auth to an `HttpOnly` cookie
(bigger change, needs CSRF story). **Recommend (a) now; (b) only if you ever expose beyond
the LAN.** Proposed header set is in the audit notes; needs one manual smoke of the built
bundle.

**D3. Agent ↔ server traffic is plaintext HTTP with no warning.**
On a real LAN deployment the 180-day agent bearer token and the enrollment token cross the
wire in clear; a LAN sniffer can impersonate the agent. *Options:* (a) warn/refuse plaintext
for non-loopback hosts unless `allow_insecure_http: true` is set; (b) document reverse-proxy
TLS as the deployment story; (c) accept for trusted home LANs. **Recommend (a) + a short
INSTALL note on putting the API behind caddy/nginx TLS.**

**D4. Containers run as root.**
An RCE in any dependency (the API parses untrusted PDFs) is root-in-container; in dev the
source tree is bind-mounted read-write. Fix is a standard non-root `USER` per image **but**
your existing volumes have root-owned files — needs a one-time `chown` migration, which is why
this wasn't auto-applied to your running stack. **Recommend: do it; I'll include the volume
migration step.**

**D5. `.env.example` ships a literal default Postgres password (and your `.env` still uses it).**
Postgres is loopback-only so it's not LAN-reachable today; it becomes a problem the day the
port mapping changes. *Options:* (a) `make init` generates a random password; (b) just a
rotation note in INSTALL.md. **Recommend (a).**

**D6. Admin-set `ollama_url` has no SSRF guard** (carried over — NEEDS_DISCUSSION 3d).
Admin/owner-only and defaults to loopback; but it's the one egress URL with no validation.
**Recommend: validate scheme+host, allow loopback/docker-service names freely, require an
explicit config opt-in for other hosts.**

### B2 · Correctness / robustness

**D7. Redis down at enqueue = imports "succeed" but files silently never get extracted.**
Best-effort enqueue is a documented design choice, but there's no recovery sweep and no signal
in the response — 40 uploaded PDFs keep filename-titles forever. **Recommend: add
`extraction_queued: false` to the import/upload response (UI warns) + a startup sweep that
re-enqueues files with no extraction.** (Same theme as TECH-7's "surface queue health in the
admin UI".)

**D8. Metadata-enrichment failures: fail the job or degrade per-source?**
Auto-fix STAB-2a already guarantees chunk/embed indexing happens regardless. Remaining choice:
`enrich_work` currently raises on the first failing source (arXiv down → Crossref never
tried). **Recommend: catch per source, record which sources failed in the job result, continue
with the rest.**

**D9. Folder import is one giant HTTP-request transaction.**
Any mid-scan error rolls back every file imported in that scan (and the `batch.status="failed"`
marker with it); big folders risk proxy timeouts. **Recommend: commit the batch row first,
per-file savepoints, finalize status at the end — or move whole-folder scans to the worker
queue.** (Mild contract change: partial imports become visible.)

**D10. Worker container starts without waiting for migrations.**
After an upgrade with jobs still queued, the worker can run them against a not-yet-migrated
schema. **Recommend: gate the worker command on `alembic current == head` (wait loop in the
entrypoint).**

**D11. Rolling-deploy window for the "no loose papers" invariant** (carried over — 3f).
**Recommend: run the 0037 backfill idempotently at startup — cheap, closes the window, and
makes ND-2c airtight.**

**D12. Multimode clustering: enforce per-model dimensions strictly?** (carried over — 3h; a
guard exists, pad/truncate enforcement doesn't.) **Recommend: skip-with-warning on dim
mismatch rather than pad — mismatches indicate a real registry bug and padding hides it.**

### B3 · Performance (visible at your scale)

**D13. BM25 lexical index rebuilds synchronously inside the first search after ANY edit.**
The biggest new performance finding: `corpus_signature` includes `max(updated_at)`, so editing
one paper's status invalidates the whole index; the next lexical/hybrid search then rebuilds it
inline — including one SELECT + full TEI XML re-parse per work (2 k papers → seconds-to-minutes
first search, every session). *Options:* (a) enqueue rebuild as a background job and serve the
stale index meanwhile; (b) also build from the already-materialized `work_chunks` instead of
re-parsing TEI. **Recommend both; (a) is the user-visible win.** (The unbounded index-file
growth part was already auto-fixed.)

**D14. First activation of a real embedding model = 20 k sequential HTTP calls.**
Backfill embeds one chunk per Ollama round-trip (Ollama's API accepts batches), and the legacy
`POST /search/reindex` endpoint runs the whole pipeline synchronously in-request. Batched
commits (auto-fixed) make it survivable; batching the embeds makes it fast. **Recommend: add
`embed_many()` + route `/search/reindex` to the queued job.**

**D15. Whole-library duplicate scan in one request.**
Per-work query fan-out was batched in the auto-fixes, but a full-library scan is still a
minutes-long synchronous request at 2 k works. **Recommend: force `background=true` for
full-library scans (the queued path already exists); keep sync for single-work scans.**

**D16. Frontend batch operations run N sequential requests.**
"Select 100 → set status" = 100 serial round-trips; batch re-extract is N×M. **Recommend:
chunked `Promise.all` (concurrency ~6) now; a backend batch endpoint only if you feel it
later.**

**D17. Cytoscape graph fully rebuilds + re-runs layout on every display toggle.**
At library scope each checkbox is a multi-second freeze. **Recommend: show/hide elements on
the live instance; re-layout only on explicit button.** (Small visual behavior change.)

**D18. Library table silently caps at 100 rows.**
The client never sends `limit`, backend defaults to 100 — with 300 papers the library view just
truncates with no indicator. Needs a deliberate choice: pagination, infinite scroll, or a
higher default + count display. **Recommend: server-driven pagination with a total count —
it's also the prerequisite for the 2a shelves/racks columns below.**

**D19. Topic-modeling fallback embedding policy** (carried over — 3b). When chunk vectors are
missing for the selected model, topic views embed inline one-by-one. *Options:* (a) batch the
fallback; (b) require pre-indexed vectors and skip un-indexed papers. **Recommend (b) + a
"N papers not indexed for this model — reindex" notice: keeps reads read-only, consistent with
search.**

**D20. Topic-graph O(n²) pure-Python cosine** (carried over — 3c). Bounded by MAX_NODES=400,
fine today. **Recommend: do the numpy rewrite next time you touch topics; not urgent.**

**D21. Multimode search per-model COUNT/hydration** (carried over — 3g). Mostly de-fanged by
the provider cache (auto-fixed). **Recommend: leave.**

**D22. HNSW provisioning inside the reindex transaction** (carried over — 3e). The batched
commits (STAB-5) shrank the lock window a lot. Remaining: split provisioning into its own short
transaction. **Recommend: do the split — it's small now that commits are batched.**

### B4 · Architecture / stack (no bugs — direction calls)

**D23. `httpx2` — verified legitimate, keep.** Confirmed online: real Pydantic-maintained
fork of httpx (github.com/pydantic/httpx2, PyPI maintainer "Pydantic Services Inc.", first
release 2026-05, current 2.5.0). The audit's supply-chain alarm was a false positive caused by
its recency. Minor: bump the pin 2.4.0 → 2.5.0 at next image rebuild.

**D24. No backend lockfile.** Docker is your source of truth, yet every image rebuild
re-resolves `>=` ranges — a future major of sqlalchemy/pydantic lands unreviewed. **Recommend:
compile a lock (`uv pip compile` / `pip-compile`) that Dockerfiles install from; keep
`requirements.txt` as intent.**

**D25. Embedding-model registry (runtime DDL, up to 8 models) is over-built for the product.**
It works and is tested, but web-admin-triggered `ALTER TABLE` is a standing risk category, and
one user needs one model (+ maybe multimode experiments). *Options:* (a) keep as-is, treat as
frozen; (b) simplify to single-configured-model + re-embed on change. **Recommend (a) freeze —
it's sunk cost that works; revisit only if it causes an incident.**

**D26. Hand-rolled BM25F engine vs Postgres FTS.** Genuinely well-built, but it's permanent
bespoke maintenance (mmap files, signatures, warm endpoint) for something `tsvector` does at
this scale. **Recommend: freeze — never extend it; if D13 leads to a rewrite anyway, that's
the moment to evaluate Postgres FTS instead.**

**D27. Backend maintains dual SQLite/Postgres code paths so tests run on SQLite.**
Every feature is written twice (vector fallbacks, dialect branches); tests mostly don't
exercise the dialect users run. **Recommend: gradually move the default test run to Postgres
(harness already exists for parity tests), then delete SQLite branches. Do it opportunistically,
not as a big-bang.**

**D28. Redis/RQ: keep, but the "best-effort enqueue" half is D7.** Worker isolation for
GROBID/OCR/embedding jobs is worth two containers. **Recommend: keep RQ; fix visibility (D7).**

**D29. Frontend rides bleeding-edge majors** (Vite 8, TS 6, pdfjs 6, vitest 4, jsdom 29).
Lockfile + `npm ci` protect reproducibility; the risk is early-adopter migration bugs for zero
feature payoff. **Recommend: verify each is a stable release; pin back any that are
pre-stable/beta. Low priority.**

**D30. Ops polish (from the audit, all optional):** OCR toolchain (+300–500 MB) is baked into
the base backend image against the otherwise-consistent "heavy = opt-in" rule → optional `slim`
target; `VITE_API_BASE_URL` baked at build time → runtime `config.js` injection would avoid
frontend rebuilds on address changes.

### B5 · Product scope (features, not fixes)

**D31. Spec-conformance small batch** (carried over — NEEDS_DISCUSSION §4, all verified still
missing): audit-event wiring (`shelf.*`, `rack.*`, `paper.metadata_edited`, `annotation.*`,
`job.*`, backup/restore — one line each at known sites), summary provenance columns
(computed but not persisted), annotation JSON export (spec requires it), extra search operators
(`abstract:`/`fulltext:`/`has:grobid`/`file:`/…), LaTeX/Pandoc export formats, import-to-rack.
**Recommend implementing in that order; audit events + provenance columns + annotation-JSON
are a small, safe batch.**

**D32. Library table shelves/racks columns** (carried over — 2a). Backend batched
serialization + two columns; pairs naturally with D18 pagination. **Recommend: yes, with D18.**

**D33. Per-section BM25 scores for lexical hits** (carried over — 2b). **Recommend: skip** —
semantic/hybrid hits already show the section; low value for the plumbing required.

**D34. `summary_provider` UX** (carried over — 2d): both provider AND model must be set for
LLM summaries; the panel shows a fallback badge. **Confirm the current UX is clear enough, or
ask for a one-line "provider is extractive — select local_llm to use this model" hint.**

**D35. ML extraction backend (Nougat/Marker)** — the one FOLLOWUP item still fully open: the
config flag exists, the worker ignores it, no extractor code exists. This is a large feature
(torch image, markdown→persistence mapping). *Options:* (a) build it; (b) drop the flag and
the detection stub until there's a real need (OCRmyPDF already covers scanned PDFs). 
**Recommend (b) for now — remove the dead seam or mark it explicitly experimental; revisit if
GROBID quality on hard PDFs becomes a real pain.**

**D36. E2E remaining gap**: harness + 12 journeys exist; missing identifier-import, annotate,
export, duplicates-review journeys and CI wiring (Playwright not in ci.yml). **Recommend: add
CI wiring first (catches regressions in the 12 existing journeys), add journeys
opportunistically.**

**D37. pgvector remainder** (FOLLOWUP §3): chunk-level ANN is done (per-model HNSW). Left:
document-level `Embedding` still JSON, `pgvector_enabled` default-off, `hash_bow` default
provider. **Recommend: once you routinely run with a real model, flip the defaults; leave the
document-level JSON path as the SQLite/test fallback it already is.**

**D38. Big spec features** (carried over — NEEDS_DISCUSSION §5, all confirmed still absent):
citation summaries §8.11 (the `citations.py` router is literally empty — this is the largest
headline-goal gap), citation-graph depth §8.9 (8 modes / PageRank / encodings vs today's 2
modes), and the smaller deltas (preprint↔published duplicate kind, backup REST endpoints,
CSV/Zotero/watched-folder import, reference-string fallback parser). **These need scoping into
a workplan — say which (if any) you want next; §8.11 is the one that pays off the product's
own pitch.**

---

## C. Verified sound (for your confidence — no action)

- Path traversal: file serving + extraction share one validated resolver; the OCR derived-copy
  fix (aa14143) has no sibling bugs. Vec_* registry SQL injection-safe (slug allowlist +
  regex re-check + int casts). Visibility clamps consistent across all work-returning
  endpoints incl. exports (re-derived independently). Find-on-web SSRF guards re-classify
  every redirect hop; enrichment refuses cross-host redirects.
- XXE: not exploitable at the pinned lxml (≥5.2 doesn't resolve external entities); hardened
  parser flags would be belt-and-suspenders only.
- Privilege model (owner immutability, admin-can't-manage-admins, no self-escalation) clean.
  Mass-assignment bounded by strict Pydantic schemas everywhere checked.
- All service ports (Postgres/Redis/GROBID/Ollama/API/frontend) bind 127.0.0.1 in both compose
  files. No secrets committed (checked git index, not just the working tree). Admin scripts use
  `getpass`, nothing on argv.
- Migrations 0030–0040: reversible, linear, tz-safe. API commit/rollback discipline is clean;
  every outbound HTTP call has an explicit timeout; no bare `except:` anywhere; Redis-down
  degrades without 500s (visibility gap = D7).
- Frontend deps: 6 runtime deps, all earning their keep; PDF.js worker bundled locally;
  Cytoscape node fields escaped. Agent design (Starlette loopback + token + HttpOnly SameSite
  cookie, opaque-id-only teleport) is sound.
- Heavy services properly profile-gated (GROBID `extraction`, Ollama `ai`, torch extractors
  opt-in image) — the ops discipline is genuinely good.
- E1–E7 efficiency fixes from the 2026-06-30 audit: spot-checked present and effective.
- `httpx2` supply-chain: verified legitimate (see D23).

## D. Closed since the old documents were written (no action)

- **CSL/citeproc (FOLLOWUP §1): DONE** — `render_styled()` delegates to citeproc-py; 7 bundled
  styles + locale; dynamic `GET /exports/styles`; golden tests.
- **pgvector ANN (FOLLOWUP §3): mostly DONE** — per-model `vector(dim)` columns + HNSW on
  `work_chunks` via the registry; ANN is the primary chunk-search path (remainder → D37).
- **Playwright E2E (FOLLOWUP §4): mostly DONE** — 12 journeys incl. import+GROBID, reader,
  racks/shelves (remainder → D36).
- **NEEDS_DISCUSSION 2c (default-shelf hooks): DONE** in this pass (auto-fix ND-2c).
- **NEEDS_DISCUSSION 3a (provider cache): DONE** in this pass (auto-fix PERF-3a).
- FOLLOWUP.md and docs/NEEDS_DISCUSSION.md are superseded by this file.

# PaRacORD — Audit: current & deferred issues

Living list of **known technical issues** (security, correctness, performance, hygiene) that are
open or deferred. Each has a severity and a recommended fix; none blocks daily use. Product- and
architecture-level *choices* live in `DISCUSSIONS.md`; everything resolved or stale is in
`ARCHIVED_AUDIT_LOG.md` (originals of the pre-2026-07-02 documents are in `docs/archive/`).

Item IDs (D1…) are stable and shared with the 2026-07-02 consolidated audit; when an item is
fixed, move it to the archive log with its ID.

Scale assumption: mostly single-user, but a few users on a LAN is a supported mode — cross-user
visibility, LAN transport, and shared-workstation issues are real product concerns. Efficiency is
judged at hundreds–few-thousand papers.

Status at last full audit (2026-07-02): `make test-full` green (660 backend + 32 agent),
`make frontend-check` green (75 tests + build), CI green.

---

## Security

**D1 · MEDIUM — Per-process login throttle across multiple API workers.** — **DECIDED
2026-07-02 · fix properly (do NOT pin workers to 1).** Note: gunicorn/uvicorn workers (the API
HTTP servers, `GUNICORN_WORKERS`) are separate from the RQ extraction workers (the `worker`
container); RQ workers scale freely with no throttle effect. The owner wants the ability to raise
**API** workers (e.g. 4), so the in-process throttle must move to a **shared Redis store**. Scope:
- Move `login_throttle` to Redis (fall back to in-process if Redis down — fail-open).
- Add **rate limiting** — per-client (user/IP) and global request caps, Redis-backed,
  admin-configurable thresholds. A batch import counts as a single request for rate-limiting.
- **Batch item cap** (`max_batch_items`, AppConfig, default **100**, admin-editable): applies to
  all batch imports (agent, DOI/identifier, BibTeX, RIS/CSL, citation). Server web-GUI batches
  over the cap are **rejected with a clear warning**; the **local agent chunks** oversized imports
  into ≤cap batches client-side (fetches the server's cap).
- Then API worker count can be raised safely (document `GUNICORN_WORKERS`).

**D2 · MEDIUM — Browser token in `localStorage` + no CSP/security headers from nginx.**
These compound: paper titles/abstracts are external (PDF/Crossref) data rendered in the SPA; any
XSS anywhere reads the token → account takeover. The one `{@html}` is verified escaped, but there
is no second layer. *Fix:* add `Content-Security-Policy`, `X-Frame-Options DENY`,
`X-Content-Type-Options nosniff`, `Referrer-Policy no-referrer` to `frontend/nginx.conf`
(everything is bundled/self-hosted, so `'self'`-based CSP should be safe — needs one manual smoke
of the built bundle). Migrating auth to an `HttpOnly` cookie is a bigger change, only worth it if
ever exposed beyond the LAN.

**D3 · MEDIUM — Agent ↔ server traffic is plaintext HTTP with no warning.**
On a real LAN deployment the 180-day agent bearer token and the enrollment token cross the wire
in clear; a LAN sniffer can impersonate the agent. *Fix:* warn/refuse plaintext for non-loopback
hosts unless `allow_insecure_http: true` is set, plus a short INSTALL note on putting the API
behind caddy/nginx TLS.

**D4 · MEDIUM — Containers run as root.**
An RCE in any dependency (the API parses untrusted PDFs) is root-in-container; in dev the source
tree is bind-mounted read-write. *Fix:* add a non-root `USER` to each image. **Caution:** existing
volumes have root-owned files — needs a one-time `chown` migration step for running deployments.

**D5 · LOW — `.env.example` ships a literal default Postgres password** (and local `.env`s tend
to keep it). Postgres is loopback-only today; becomes a problem the day the port mapping changes.
*Fix:* have `make init` generate a random `POSTGRES_PASSWORD`.

**D6 · LOW — Admin-set `ollama_url` has no SSRF guard.**
Admin/owner-only and defaults to loopback, but it's the one egress URL with no validation.
*Fix:* validate scheme+host; allow loopback/docker-service names freely; require an explicit
config opt-in for other hosts.

## Correctness / robustness

**D7 · MEDIUM — Redis down at enqueue = imports "succeed" but files silently never get
extracted.** (Detailed write-up requested by the owner 2026-07-02; **awaiting approval of the fix
shape below before implementing.**)

*What happens.* Import paths (folder scan, upload, agent teleport) create the File/Work rows,
commit, then **enqueue** the background jobs — GROBID extraction, metadata enrichment, chunking,
embedding — onto Redis/RQ. Enqueue is deliberately best-effort: `workers/queue.py` catches any
failure to reach Redis, logs a warning, and returns `None` instead of raising (rationale: "a
missing/unreachable Redis must not break the import flow").

*The hole.* If Redis is down — or the worker container is mid-restart during a
`docker compose up -d --build` upgrade — at the enqueue moment, the jobs are silently dropped: no
retry, no dead-letter, no record they were owed. The import HTTP response still reports success.
So you can upload 40 PDFs, see them all in the library, and none ever get extraction, references,
real metadata, or embeddings — they keep filename-derived titles forever. Nothing surfaces it:
the only endpoint that 503s on a dead queue is the manual `/files/{id}/extract`; bulk import
swallows it. No sweep later notices "status=available but never extracted" and re-queues.

*Why it's real, not theoretical.* A brief Redis blip is normal exactly during an upgrade — which
is also when someone might import. On a single self-hosted box, one transient blip =
permanently unprocessed papers with no signal.

*Fix — DECIDED 2026-07-02 · implementing (layer 1 + startup sweep; no outbox table):*
1. **Make it visible — everywhere.** Thread the enqueue result into responses: `extraction_queued:
   false` in the server web-GUI import/upload payloads (UI warns "imported, but couldn't queue
   extraction — retry later"), AND in the **local-agent** push responses so the agent surfaces it
   (log + status) and keeps the item retryable rather than marking it done. Also add a **queue/
   worker health indicator to the Jobs tab**: a semaphore (green = workers up + queue draining;
   yellow = queued but idle/degraded; red = Redis unreachable / no workers) + a short status line
   (worker count, queue depth). Needs a queue-health endpoint (RQ worker count + depth + Redis
   reachability).
2. **Make it self-heal — with a durable "owed" marker (no double-work).** Add a durable
   `File.extraction_requested_at` (or equivalent) set **transactionally at import** whenever
   extraction is intended; the worker clears it on terminal success/failure. A startup (and
   optionally periodic / admin-button) sweep re-enqueues files whose marker is set and that are
   not currently `extracting`/queued. This cleanly distinguishes:
   - **owed** (enqueue dropped): marker set, not extracted, not in-flight → re-enqueue;
   - **never requested** (nobody clicked extract, if reachable): marker null → left alone;
   - **user clicked re-extract**: sets the marker + status `extracting` + enqueues with a
     **deterministic job id** (`extract:{file_id}`) so a concurrent sweep is a no-op, not a
     duplicate — this closes the collision the owner flagged.

*Not doing:* a transactional `pending_jobs` outbox table (most robust, but duplicates RQ; overkill
at this scale). Ties to DISCUSSIONS D28 (keep RQ).

**D8 · MEDIUM — Enrichment stops at the first failing source.**
`enrich_work` raises on the first failing source (arXiv down → Crossref never tried). Chunk/embed
indexing is already guaranteed regardless (fixed 2026-07-02). *Fix:* catch per source, record
which sources failed in the job result, continue with the rest.

**D9 · MEDIUM — Folder import is one giant HTTP-request transaction.**
Any mid-scan error rolls back every file imported in that scan (including the
`batch.status="failed"` marker); big folders risk proxy timeouts. *Fix:* commit the batch row
first, per-file savepoints, finalize status at the end — or move whole-folder scans to the worker
queue. (Mild contract change: partial imports become visible.)

**D10 · MEDIUM — Worker container starts without waiting for migrations.**
After an upgrade with jobs still queued, the worker can run them against a not-yet-migrated
schema. *Fix:* gate the worker command on `alembic current == head` (wait loop in the entrypoint).

**D11 · LOW — Rolling-deploy window for the "no loose papers" invariant.**
Migration 0037 backfilled; the invariant is enforced in app code (all creation paths hooked as of
2026-07-02). *Fix:* run the backfill idempotently at startup — cheap, closes the deploy window.

**D12 · LOW — Multimode clustering does not enforce per-model dimensions strictly.**
A malformed-row guard exists; pad/truncate enforcement doesn't. *Fix:* skip-with-warning on dim
mismatch rather than pad — mismatches indicate a real registry bug and padding would hide it.

## Performance (visible at the target scale)

**D13 · HIGH — BM25 lexical index rebuilds synchronously inside the first search after ANY
edit.** `corpus_signature` includes `max(updated_at)`, so editing one paper invalidates the whole
index; the next lexical/hybrid search rebuilds it inline — one SELECT + full TEI XML re-parse per
work (2 k papers → seconds-to-minutes first search, every session). *Fix:* (a) enqueue rebuild as
a background job and serve the stale index meanwhile — the user-visible win; (b) build from the
already-materialized `work_chunks` instead of re-parsing TEI. (The unbounded index-file growth
half was fixed 2026-07-02.)

**D14 · MEDIUM — First activation of a real embedding model = 20 k sequential HTTP calls.**
Backfill embeds one chunk per Ollama round-trip (the API accepts batches), and the legacy
`POST /search/reindex` runs the pipeline synchronously in-request. Batched commits (fixed
2026-07-02) make it survivable; batching makes it fast. *Fix:* add `embed_many()` + route
`/search/reindex` to the queued job.

**D15 · MEDIUM — Whole-library duplicate scan in one request.**
Per-work query fan-out was batched (2026-07-02), but a full-library scan is still a minutes-long
synchronous request at 2 k works. *Fix:* force `background=true` for full-library scans (the
queued path exists); keep sync for single-work scans.

**D16 · MEDIUM — Frontend batch operations run N sequential requests.**
"Select 100 → set status" = 100 serial round-trips; batch re-extract is N×M. *Fix:* chunked
`Promise.all` (concurrency ~6) now; a backend batch endpoint only if still felt.

**D17 · MEDIUM — Cytoscape graph fully rebuilds + re-runs layout on every display toggle.**
At library scope each checkbox is a multi-second freeze. *Fix:* show/hide elements on the live
instance; re-layout only on explicit button. (Small visual behavior change.)

**D19 · LOW — Topic views embed un-indexed papers inline, one at a time.**
When chunk vectors are missing for the selected model, the read path embeds inline. *Fix:*
require pre-indexed vectors and skip un-indexed papers, with an "N papers not indexed for this
model — reindex" notice; keeps reads read-only, consistent with search.

**D20 · LOW — Topic-graph O(n²) pure-Python cosine.** Bounded by `MAX_NODES=400`, fine today.
*Fix:* numpy rewrite (`M @ M.T` after one normalize) next time topics are touched; not urgent.

**D22 · LOW — HNSW provisioning runs inside the reindex transaction.**
Batched commits (2026-07-02) shrank the lock window a lot. *Fix:* split provisioning into its own
short transaction — small now that commits are batched.

## Dependency / ops hygiene

**D24 · MEDIUM — No backend lockfile.** Docker is the source of truth, yet every image rebuild
re-resolves `>=` ranges — a future major of sqlalchemy/pydantic lands unreviewed. *Fix:* compile a
lock (`uv pip compile` / `pip-compile`) that Dockerfiles install from; keep `requirements.txt` as
intent. Minor related: bump `httpx2` pin 2.4.0 → 2.5.0 at next rebuild.

**D29 · LOW — Frontend rides bleeding-edge majors** (Vite 8, TS 6, pdfjs 6, vitest 4, jsdom 29).
Lockfile + `npm ci` protect reproducibility; the risk is early-adopter migration bugs for zero
feature payoff. *Fix:* verify each is a stable release; pin back any that are pre-stable.

**D30 · LOW — Ops polish (optional):** OCR toolchain (+300–500 MB) is baked into the base
backend image against the otherwise-consistent "heavy = opt-in" rule → optional `slim` target;
`VITE_API_BASE_URL` is baked at build time → runtime `config.js` injection would avoid frontend
rebuilds on address changes.

**D36a · LOW — Playwright E2E not wired into CI.** Harness + 12 journeys exist and pass locally;
CI never runs them. *Fix:* add the CI job (catches regressions in existing journeys). The missing
journeys are a scope choice → `DISCUSSIONS.md` D36.

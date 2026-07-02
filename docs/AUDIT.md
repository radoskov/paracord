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

**D1 · MEDIUM — Login throttle is per-process, but production runs 2 gunicorn workers.**
The brute-force lockout budget silently doubles (and the `paper.viewed` debounce halves) with
`GUNICORN_WORKERS=2` (`backend/Dockerfile`); the throttle's docstring assumes one process.
*Fix:* default `GUNICORN_WORKERS=1` now; back the throttle with Redis if the worker count is
ever raised.

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
extracted.** Best-effort enqueue is a documented design choice, but there is no recovery sweep
and no signal in the response — 40 uploaded PDFs keep filename-titles forever. *Fix:* add
`extraction_queued: false` to import/upload responses (UI warns) + a startup sweep that
re-enqueues files with no extraction. (Also surfaces queue health in the admin UI.)

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

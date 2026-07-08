# PaRacORD — Audit (merged)

**Consolidated & status-verified 2026-07-08.** The single register of **known technical issues**
(security, correctness, performance, hygiene). Merges the former `AUDIT.md` (D-IDs), `AUDIT_EXT.md`
(2026-07-08 extended audit; its net-new items are the **E-series** below), and
`ARCHIVED_AUDIT_LOG.md` (resolved tail). Product/architecture **choices** are not issues — they live
at the end of [`WORKPLAN.md`](WORKPLAN.md); build tasks that resolve an open issue are in
`WORKPLAN.md` and cross-reference the IDs here.

**Scale assumption.** Mostly single-user, but **a few users on a LAN is a supported mode** —
multi-user / IDOR / transport findings are weighted accordingly (taken seriously, not inflated to
internet-facing severity).

**Important:** the pre-consolidation `AUDIT.md` was badly stale — it listed most D-items as open when
git history and `PROGRESS.md` show they shipped in the 2026-07-02/03 and 2026-07-07/08 batches. The
table below reflects **verified** current status; almost everything moved to the archive section.

Item IDs are stable. When an open item is fixed, move it to the archive section with its ID.

---

## Open & partial issues

| ID | Title | Status | Description (merged; source IDs) | Priority |
|---|---|---|---|---|
| **D3** | Agent↔server traffic is plaintext HTTP, no guard | **OPEN** | The 180-day agent bearer + enrollment token cross the LAN in clear; an active sniffer can impersonate the agent. No `allow_insecure_http` guard exists. Fix: warn/refuse plaintext for non-loopback hosts unless opted in, + an INSTALL TLS note. (Overlaps AUDIT_EXT §2.2 "prod deployment guide".) Build task: `WORKPLAN.md` M2. | **Medium** — real transport/impersonation risk in the *supported* LAN mode, but confined to the owner's own LAN and needs an active sniffer. Highest genuinely-open item. |
| **D38** | Big spec features — remaining deltas | **PARTIAL** | Headline items done (§8.11 citation summaries, §8.9 graph depth). Still absent: preprint↔published & same-file-different-path duplicate kinds (§8.4); backup/restore REST endpoints (§10.2, CLI-only today); CSV/TSV + watched-folder + Zotero import (§8.1); reference-string fallback parser (§8.2). Build task: `WORKPLAN.md` M1. | **Medium** — product features, owner-scheduled; none is a defect. |
| **E6** | Onboarding / library-health / unified-jobs UX gap | **OPEN (product)** | First-run wizard, library-health dashboard, single progress center, plain-language explanations for index-only/teleport. (AUDIT_EXT §6; agent mental-model sub-item already met by the batch-6 agent Help tab.) Build tasks: `WORKPLAN.md` H1/H2/M3. | **Medium (product value)** — not a bug; UI density is the app's main UX risk. |
| **D30** | Ops polish: slim OCR image + runtime frontend config | **OPEN** | OCR toolchain (+300–500 MB) is baked into the base backend image (breaks "heavy = opt-in"); `VITE_API_BASE_URL` is baked at build time. Optional `slim` target + runtime `config.js` injection. Build task: `WORKPLAN.md` L3. | **Low** |
| **E1** | No fail-closed option when Redis is unreachable | **OPEN** | Rate-limit + queue-cap + login-throttle all fail *open* if Redis is down (deliberate, correct for single-user). Add a `PARACORD_PRODUCTION_REQUIRE_REDIS`-style flag + a red admin "limits unavailable" status. (was AUDIT_EXT **S1**.) | **Low** — defense-in-depth for the LAN mode. |
| **E2** | PDF validation is header-only, not parser-level | **PARTIAL** | Uploads check size + `%PDF` header; the Batch-S safety battery already tests 413/400 on oversized/non-PDF/malformed/zero-byte/bomb. Missing: a PyMuPDF open-first-page probe to reject encrypted/unsupported PDFs with a clear failed state *before* GROBID/OCR. (was AUDIT_EXT **S3**.) | **Low** — meaningful hardening already landed; residual is defense-in-depth. |
| **E3** | Python-materialized visibility sets vs SQL predicates | **OPEN** | `visible_work_ids()` returns a Python set; files-list/graph/export scopes build large `IN` lists. Reuse `_visible_work_condition` / `EXISTS`. (was AUDIT_EXT **O1**.) | **Low** — fine at hundreds–few-thousand papers; only bites large multi-user collections (out of the stated scale). |
| **E4** | Extraction pipeline not queue-budget/phase aware | **OPEN** | Split bulk import into cheap/expensive phases surfaced in the Jobs UI with pause/cancel per batch. (was AUDIT_EXT **O4**; overlaps the "unified progress center", `WORKPLAN.md` M3.) | **Low** — feature, not a defect. |
| **E5** | Production Compose least-privilege residuals | **PARTIAL** | Non-root containers (D4) + DB/Redis/API healthchecks (Batch W) are done. Missing: read-only container mounts where possible, and backup/restore verification in CI / a scheduled run. (was AUDIT_EXT **R4**.) | **Low** |
| **D2** | Browser token in `localStorage` | **PARTIAL** | CSP + `X-Frame-Options`/`X-Content-Type-Options`/`Referrer-Policy` were added to `frontend/nginx.conf` (2026-07-03, smoke-tested). **Deliberate caveat:** `connect-src` is relaxed to `http:/https:` because the API is a separate origin from the nginx-served SPA — do not "fix" this back. Residual: the token still lives in `localStorage`; the HttpOnly-cookie migration is **deferred by design** (only worth it beyond LAN). | **Low** — the requested second layer (CSP) shipped; residual is explicitly deferred. |
| **S2** | Agent loopback-GUI token hardening residual | **PARTIAL** | The agent GUI already uses a token + HttpOnly SameSite cookie + a `0600` token file (assessed "sound"). Residual niceties: a one-time launch token, rotation on restart, no-referrer / cache-control on the launch URL. (was AUDIT_EXT **S2**.) | **Low** — loopback-only. |

---

## Resolved / archived issues

Historical tail; kept for the audit trail. All verified fixed.

**Implemented since the 2026-07-02 audit (the old `AUDIT.md` never reflected these):**
- **D1** — overload protection: Redis login-throttle, per-client + global rate-limit middleware,
  `max_batch_items=100` (413), `rq_worker_count` supervisor. Migrations 0043–0045.
- **D39** — `max_queue_len` cap (429) + admin Clear-queue / Reset-workers. Migration 0046.
- **D4** — non-root `appuser`/`node` + gosu entrypoints (reaffirmed Batch W). *(nginx **master** still
  root — see open D-adjacent hardening in `WORKPLAN.md` M2.)*
- **D5** — `make init` generates a random `POSTGRES_PASSWORD`.
- **D6** — `ollama_url` SSRF guard (`ai_config._validate`, `ALLOW_EXTERNAL_OLLAMA`). Absorbs
  AUDIT_EXT **S4** (find-on-web SSRF re-classifies every redirect hop).
- **D7** — `extraction_queued` surfaced everywhere + Jobs queue-health semaphore + durable
  `File.extraction_requested_at` (migration 0042) + startup sweep + deterministic `extract-{id}` id.
- **D8** — per-source enrichment resilience (`failed` list).
- **D9** — folder import per-file SAVEPOINTs; batch row committed up front (partial imports visible).
- **D10** — worker supervisor waits `alembic current == head`; Batch W also gates the worker on
  `api: service_healthy`.
- **D11** — idempotent loose-paper backfill in the FastAPI lifespan.
- **D12** — multimode clustering skips dim-mismatched models (no padding).
- **D13** *(was HIGH)* — BM25 rebuild moved off the read path to a coalesced `rebuild_bm25_job`;
  builds from `work_chunks`, not TEI.
- **D14** — `embed_many()` batch embedding; `/search/reindex` routed to the queue.
- **D15** — full-library duplicate scan forced onto the worker.
- **D16** — chunked `Promise.allSettled` (concurrency 6) for batch frontend ops.
- **D17** — Cytoscape show/hide on the live instance; relayout only on data change.
- **D19** — topic views skip un-indexed papers + `unindexed_work_count` notice.
- **D20** — topic-graph cosine → a single numpy `M @ M.T`.
- **D22** — HNSW provisioning in its own short transaction.
- **D24** — hash-pinned `backend/requirements.lock` (`--require-hashes`); httpx2 2.4.0→2.5.0.
- **D29** — all 7 frontend majors verified stable; echarts later bumped 5.5→6.1 (`abdf368`), which
  also cleared XSS **W1** (GHSA-fgmj-fm8m-jvvx).
- **D31** items 1–5 — audit-event wiring + JSONL sink; summary provenance (migration 0048);
  annotation JSON export; extra search operators; latex/pandoc export + import_batch/missing_references
  targets. *(Item 6 dropped by owner.)*
- **D35** — dead Nougat/Marker/`full_ml` ML-extraction seam removed (migration 0047).
- **D36 / D36a** — Playwright wired into CI; suite expanded to 25+ journeys.
- **D37** — `pgvector_enabled` default flipped on; ANN (HNSW) route with JSON/Python-cosine fallback.
- **D18 / D32** — library pagination envelope + per-user `papers_per_page` + shelves/racks columns.
- **R3** (AUDIT_EXT) — guarded one-shot delete-on-disk (watched-root boundary, symlink-escape reject,
  arm-flag, cap 100, trash dir) shipped in Batch A + covered by Batch-S safety tests.
- **S5 / O5** (AUDIT_EXT) — access service already centralized; topic-modeling ordering stabilized.
- **issue_batch_6** (owner report, 2026-07-07/08) — all 7 items closed (viz axis/help/edges/overlap
  markers/default view; embed-count fix; lexical-index freshness + Rebuild button; agent
  bulk-prune-skips-watched; library Refresh button; `index_only`→server stub + agent Help tab;
  per-paper weighted reference graph; stored per-paper AI summary).

**Already in the former `ARCHIVED_AUDIT_LOG.md`:** C1–C5, H1–H7 (2026-06-25); A1–A3, B1–B10
(2026-06-26); efficiency E1–E7 (2026-06-30); the ~30-fix 2026-07-02 consolidated pass (agent
perms/XSS, import IDOR, 900 s RQ timeout, transactional backup/restore, provider cache, 4 dead deps
removed, etc.); D21/D23 de-fanged/resolved; the httpx2 supply-chain flag = false positive. *(Full
original documents are in `documentation_archive.zip` and the earlier gitignored `docs/archive/`.)*

---

## Duplicates folded (audit-trail of the merge)
- AUDIT_EXT **S1** → **E1** (Redis fail-closed) — distinct from D1/D7/D39, which are the fail-*open*
  mechanisms.
- AUDIT_EXT **S2** → **S2** (agent GUI token) — mostly covered by the archived "agent GUI sound"
  finding; only niceties remain.
- AUDIT_EXT **S3** → **E2** (PDF validation) — partly covered by Batch-S upload-abuse tests.
- AUDIT_EXT **S4** → folded into **D6** + archived find-on-web SSRF (resolved).
- AUDIT_EXT **R1** → the SQLite/Postgres dual-path *discussion* (`WORKPLAN.md` → D27).
- AUDIT_EXT **R2** (migration squash) → pre-release *discussion* (`WORKPLAN.md`).
- AUDIT_EXT **R3** → resolved (Batch A guarded delete + Batch-S).
- AUDIT_EXT **R4** → **E5** (partial).
- AUDIT_EXT **O1** → **E3** (SQL visibility).
- AUDIT_EXT **O2** (materialize graph summaries) → folded into **D38 §8.11** (in-process cache
  shipped; a persisted cache is the stated extension).
- AUDIT_EXT **O3** (Postgres FTS + pgvector) → the BM25-vs-FTS *discussion* (`WORKPLAN.md` → D26) +
  **D37** (pgvector done).
- AUDIT_EXT **O4** → **E4** (phased extraction).
- AUDIT_EXT **O5** → resolved (topic determinism).
- AUDIT_EXT **§6 UX** → **E6** (UX cluster).
- AUDIT_EXT **§2.2** duplicate/Zotero/OCR/reference-parser gaps → folded into **D38**.

---

## Notes & oddities
- **`CHANGELOG.md` `[Unreleased]` is stale** — its top entries are old Stage 6–9 content, not the
  recent batches. `PROGRESS.md` is the authoritative status record.
- **`Agent.revoked_at` is dead code** (Batch-S finding): revocation is via `status != "approved"` /
  `delete_agent`, both of which correctly 401 a stale token. Not a hole; a cleanup candidate (see
  `WORKPLAN.md` L7).
- **AUDIT_EXT S5** ("add a new-endpoint access-control checklist to `AGENTS.md`") is a *process*
  recommendation, not a defect — worth adopting, but it isn't in the issue table.
- The two audits agreed on severity throughout; AUDIT_EXT's risks are a superset framing of the
  already-fixed D-items, with E1–E6 as the net-new content.

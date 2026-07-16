# 11 — Future Work & Revision Notes

[← User workflows](10_user_workflows.md) · [Index](00_index.md)

This is the consolidated register of things worth revisiting, drawn from reading the current code.
Each item names the component, the concern, and a suggested direction. Severity is weighted for the
project's scale (**mostly single-user, a few trusted LAN users**), so LAN-multi-user and
egress-facing issues are ranked higher than pure single-user ones.

Legend: 🔴 high · 🟠 medium · 🟡 low. Categories: **Sec**urity · **Alg**orithmic · **Perf** ·
**Rob**ustness · **Stab**ility · **UX** · **Tech**-debt · **Doc**.

> **Update — 2026-07-12 (audit fixes F1–F4 landed).** All four 🔴 items this register surfaced have
> been implemented and are annotated **✅ RESOLVED** inline below:
> **F1** export citation-key injection (sanitised, preserving Unicode/DOI/DBLP punctuation);
> **F4** stale agent `config`/`systemd` examples (rewritten to the real schema + a staleness test);
> **F3a** the citation-graph read-path write (removed → graph is read-only; delete now re-resolves
> references; owner rescan-on-startup toggle + manual "rescan whole library" control);
> **F2** job retries + downstream recovery (bounded transient-extraction retries, chunk/embed
> derive-from-state recovery sweep, per-paper failure indicator + jobs-tab retry surfacing).
> **Update 2026-07-13:** the F3b caches are now bounded (S10 — `utils/bounded_cache.BoundedTTLCache`
> in citation_summary / visualization / external_preview — confirmed in code: LRU eviction beyond
> `maxsize` + a `ttl_seconds` expiry, `backend/app/utils/bounded_cache.py:23`). The item below is kept
> for history but is fully resolved; nothing is still open from F3b. Everything else below stands.
>
> **Update — 2026-07-16 (re-verification pass).** Re-checked every factual claim in this register
> against the current code. Several more items had landed since 2026-07-12 and are now annotated
> **✅ RESOLVED** inline: the arXiv-parser duplication (S3, one canonical parser); the
> `saved_filters`/`shelf_membership` layering issues and the missing generic exception handler (S4,
> `DomainError` + one handler); the reference-dedup consolidation job (S13/S14, now wired to a
> startup hook + admin button); and `summarize_scope_job`/`topic_model_job`, which are real pipelines
> now (S15), not stubs. Two corrections went the other way: `actOnReference`'s double-`JSON.stringify`
> is now **confirmed** (not just suspected) as a live bug, and `CitationGraph`'s old `{@html}`/
> `escapeHtml` note is stale — the component moved to ECharts and now builds an **unescaped** HTML
> tooltip string instead, which is a new (if lower-severity, given this deployment's scale) finding
> in the same spot the old note flagged. Migration numbers cited by number (`0057`/`0058`/`0066`)
> no longer resolve to individual files — the chain was squashed into `0067_squashed_baseline.py`
> afterwards; the associated features/columns are unaffected, only the file-name citations moved.

---

## Cross-cutting: documentation drift (fix first — cheap, high-value)

The following existing docs are **stale** and contradicted by the current code. Update or supersede
them (this `docs/reference/` set is the new source of truth):

- ✅ **Doc** — ~~`docs/architecture/*` describe an "M7 audit" state~~ **Resolved 2026-07-13
  (S17):** archived into the gitignored `documentation_archive.zip`; may be refreshed later.
  `AGENTS.md` now points to `docs/reference/` as the engineering source of truth and keeps
  `SPECIFICATION.md` as product intent (banner added there).
- 🟠 **Doc** — `FILE_TREE.md` lists ~7 migrations and a fraction of the real models/services/
  endpoints/tests. Regenerate or delete.
- ✅ **RESOLVED (F4)** — `config/agent.example.yaml` rewritten to the real `AgentConfig` schema
  (`server_url`, `refresh_interval`, `folders`/`files`, `web_port`, `default_action`, …), with a new
  `agent/tests/test_example_config.py` that fails if the example ever drifts from the model (keys
  must be real fields — pydantic silently ignores unknown ones, which was the original bug).
- ✅ **RESOLVED (F4)** — `agent/systemd/paperracks-agent.service.example` fixed to run
  `paracord-agent start` (with the real config mechanism), a valid `User=`, and a note that
  `--daemon` is a reserved no-op.
- ✅ **Doc** — ~~`config/server.example.yaml` documentation-only key surface~~ **Resolved
  2026-07-13 (S19):** misleading dead twins deleted (`failed_login_lockout.*`, `processing.ai.*`,
  unread grobid consolidation keys, `agents.*` — per-agent DB policy is canon); the never-wired
  blocks moved under a commented-out "REFERENCE ONLY" divider; env-pointer keys kept by design.
  The example now parses with no unread live keys beyond the five deliberate env pointers.
  Still open from the original entry: the OCR enum comment mentions `full_ml` but the code also
  accepts `pymupdf`.

---

## Data model

- 🟠 **Rob** — **Pervasive soft (no-FK) polymorphic links.** `entity_type/entity_id` on `TagLink`,
  `MetadataAssertion`, `Embedding`, `Summary`, `TopicAssignment`, `DuplicateCandidate`, plus
  `Annotation.work_id`, `RawTeiDocument.file_id/work_id`, `GroupGrant/DefaultGrant.target_id`. No
  referential integrity → **orphan rows on parent delete**. The single biggest integrity risk.
  *Direction:* audit every delete path for explicit cleanup, or add a periodic orphan-GC job, or
  (Postgres) partial FKs per `entity_type`.
- 🟡 **Tech** — `created_by/updated_by/owner/added_by_user_id` almost never FK to `users.id`;
  deleting a user leaves stale attribution ids. Lower severity at this scale.
- ✅ **RESOLVED (S13/S14)** — **`Reference.dedup_key` is still intentionally non-unique** (a future
  unique index is the eventual goal, per `reference_consolidation.py`'s own docstring), but the
  consolidation job is now wired in: `consolidate_references_job`
  (`backend/app/workers/jobs.py:610`) runs from a startup hook (`backend/app/main.py:78-80`) and an
  admin-triggered button (`backend/app/api/v1/endpoints/admin.py:771-773`), both funnelled through
  the deterministic-id `enqueue_reference_consolidation` (`backend/app/workers/queue.py:285`).
  Compatible-state groups auto-fold; genuine contradictions (conflicting user confirmations) get a
  `|conflict:<id8>` dedup-key suffix and wait for admin review instead of being silently merged. Any
  code reading references must still tolerate dupes until that unique index lands.
- 🟡 **Tech** — `Work.topics`/`keywords` (JSONB) duplicate what `TopicAssignment` models relationally
  (drift risk). `ImportStagingItem.duplicates` / `DuplicateCandidate.signals` are opaque blobs
  (not queryable).
- 🟡 **Perf** — **Confirmed:** `Work.citation_count`/`citation_count_fetched_at` have no DB index
  (`0067_squashed_baseline.py`), yet `citation_count` is a live Library sort key
  (`backend/app/api/v1/endpoints/works.py:391`, NULLs ordered last) — every citation-count sort is a
  full table scan/sort. `TopicAssignment` still lacks a composite index on its common
  `(topic_model_id, scope_type, scope_id, work_id)` access pattern (only single-column indexes exist
  on each of those four columns).
- 🟡 **Stab** — Python-side timestamp defaults (not `server_default`) make ordering by `created_at`
  slightly non-monotonic under concurrent multi-client writes; also inconsistent with the columns
  that *do* use `server_default`.
- 🟡 **Tech** — external-citations migration churn (`0057` denormalized → `0058` normalized) — this
  history is now baked into the squashed `0067_squashed_baseline.py` (the 68-migration chain was
  squashed into one baseline afterwards), so the individual `0057`/`0058` files no longer exist to
  inspect; `references` uses a reserved SQL keyword (quoted everywhere — a footgun for hand-written SQL);
  singleton id-by-convention mixes `int(1)`/`int(2)`.
- 🟡 **Tech** — confirm `WorkVersion` is still actively populated (it's referenced only via
  `FileWorkLink.version_id` and `Annotation.version_id`) and not vestigial.

## API

- 🟠 **Sec** — **Two merge paths, two floors**: `POST /works/{id}/merge` needs only **contributor**,
  the `/duplicates` merge needs **editor**. Reconcile (a contributor can merge via `/works` what they
  can't via `/duplicates`).
- 🟠 **Sec** — `GET /jobs` has **no role floor** beyond authentication — any reader sees queue
  internals. Add a librarian/editor floor for status.
- 🟠 **Sec/UX** — `GET /tags` is **unscoped and unbounded** — leaks the whole tag vocabulary to
  readers and is the one list endpoint with no cap.
- 🟡 **Tech** — Dead optional-auth branches in `files.stream_file`/`file_text` (`actor: User | None`
  that can never be `None`) — a trap if the router dep is ever loosened.
- ✅ **RESOLVED (S4)** — a generic `DomainError` exception handler now exists
  (`backend/app/main.py:131`, alongside the `BatchTooLargeError → 413` one) mapping
  `NotFoundError`/`ConflictError`/`PermissionDeniedError`/etc. (`backend/app/errors.py`) to their
  `status_code` in one place. Services raise these instead of `fastapi.HTTPException`, so they stay
  callable from workers/CLI too; there is no more `except ValueError` in `app/api/*.py` — adoption is
  incremental as services are touched, but the centralizing mechanism itself is in place.
- 🟡 **Tech** — Untyped `dict` responses (`/auth/me`, `/admin/audit-events`, all `ai_admin`) are
  absent from the OpenAPI schema; duplicated `MergePreview`/graph-node schemas across modules.
- 🟠 **Sec** — CORS `allow_credentials=True` + `*` methods/headers is safe **only** because origins
  default to explicit localhost. Add a config guard so widening `cors_origins` to `*` is rejected.
- 🟡 **Tech** — `require_agent_token` performs a DB write + commit inside a dependency (throttled,
  but unusual coupling).

## Pipeline & workers

- ✅ **RESOLVED (F2)** — transient failures (`GrobidUnavailableError`/`OperationalError`) now retry
  automatically (RQ `Retry`) and keep the owed marker; terminal failures (DOI conflict, corrupt PDF,
  cap reached) mark `extract_failed` without wasteful re-runs but stay visible in the failed-jobs
  list. A durable `File.extraction_attempts` (cap 3, reset by a manual re-extract) bounds retries
  across restarts.
- ✅ **RESOLVED (F2)** — the recovery sweep now also recovers the downstream stages: a derive-from-
  state sweep (`sweep_owed_downstream`, startup + `/jobs/reprocess-pending`) re-enqueues chunk/embed
  for works extracted-but-never-indexed. Enrich/keyword/topic aren't auto-recovered by decision, but
  now fail **loudly** — a per-paper `Work.processing_error` badge on top of the `job.failed` audit.
- ✅ **RESOLVED (F2)** — the dead `_SKIP_STATUSES={"extracting"}` was **removed** (rather than made
  real, which would have regressed auto-recovery on worker death); the deterministic-job-id
  live-guard is the sole, self-healing in-flight protection.
- 🟠 **Perf/Rob** — `scan_duplicates_job` / `rescan_reference_matches_job` load the whole corpus in
  one transaction (Perf M2). Paginate with `commit_every`.
- 🟡 **Stab** — `find_or_create_reference` + orphan prune could race if two works citing the same new
  reference extract simultaneously (no explicit locking).
- 🟡 **UX/Rob** — `rq_worker_count` is read once at supervisor startup; a change needs a container
  restart (documented, but a SIGHUP reload would be friendlier).
- ✅ **RESOLVED (S15)** — `summarize_scope_job` / `topic_model_job`
  (`backend/app/workers/jobs.py:840`/`:897`) are no longer stubs: they run the real
  `summarization.summarize_scope` / `topic_modeling.model_topics` pipelines off the request path,
  recomputing the requesting user's visibility and reporting progress/cancellation. Enqueued once a
  scope exceeds `ai_scope_job_threshold` (S16).

## Services (algorithmic & correctness)

- ✅ **RESOLVED (F1)** — user-supplied `citation_keys` are now sanitised (`_safe_citation_key`):
  structural breakout characters are neutralised while Unicode letters and real key punctuation
  (`. : + / _ -`) are **preserved** (so DBLP/DOI-style and accented keys survive), then de-duplicated.
  *(Still open, lower severity: markdown/pandoc field **values** — titles/authors — aren't escaped.)*
- ✅ **RESOLVED — F3a and F3b both done.** The read-path write is gone:
  `citation_graph.build_citation_graph` (`backend/app/services/citation_graph.py:105`) is now
  read-only (the matcher owns `resolution_status`; a work delete re-resolves affected references).
  **F3b resolved 2026-07-13 (S10):** the three formerly-unbounded in-process caches —
  `citation_summary._SUMMARY_CACHE`, `visualization._LAYOUT_CACHE`, `external_preview._PREVIEW_CACHE`
  — are now backed by `BoundedTTLCache` (`backend/app/utils/bounded_cache.py`), which evicts LRU-style
  beyond `maxsize` and expires entries after `ttl_seconds`; verified in code, not just the docstring.
- 🟠 **Sec** — **`topic_graph` visibility depends on the caller passing `visible_ids`.** With
  `work_ids` set and `visible_ids=None`, only shadow-filtering applies → IDOR risk if an endpoint
  forgets it. Audit every caller; consider requiring `visible_ids`.
- ✅ **Rob** — ~~`citing_papers.store_citing_papers` wipes links when called with an empty list~~
  **Resolved 2026-07-13 (S12):** the fetch now distinguishes three outcomes — a provider that
  listed citers (replace), an *authoritative zero* answer (replace with empty + stamp
  `works.citing_fetched_at/_source`), and no answer at all (cache kept, failure surfaced). A failed
  fetch can no longer wipe good data, and a genuinely-zero answer no longer leaves a stale list
  forever. (The `citing_fetched_at/_source` columns now live in the squashed
  `0067_squashed_baseline.py` — the migration chain was squashed from 68 files into one baseline
  afterwards, so the original per-feature migration number no longer exists as a separate file.)
- 🟠 **Alg** — **`agent_protocol.validate_agent_file_id` always returns `False`** (dead stub) — a
  caller relying on it rejects every file. Wire up or delete.
- 🟠 **Sec** — **`model_management` performs no integrity verification of downloaded weights**
  (Ollama pull / `SentenceTransformer(model)` trust upstream); arbitrary model strings are forwarded
  with no allowlist. Typo-squat / compromised-registry risk. Add an allowlist + checksum where
  feasible.
- 🟠 **Alg** — **Naming overreach**: `topic_modeling backend="bertopic"` does **not** run BERTopic
  (it reuses embedding k-means); "hierarchy" is a single nearest-neighbor pass. Rename or implement.
- ✅ **RESOLVED (S3)** — the arXiv parser is now one canonical implementation:
  `app/utils/normalization.split_arxiv_id`/`arxiv_base_id` (`backend/app/utils/normalization.py:50`),
  which `identifiers.py` and `duplicate_detection.py` both import and re-export rather than
  reimplementing. *(Still open, lower severity)* `author_matching` over-matches common surnames
  (missing-initial = agreement); `normalize_title` still drops non-ASCII instead of folding
  (`backend/app/utils/normalization.py:16`, `[^a-z0-9 ]` strip), inconsistent with
  `author_matching._fold`'s NFKD diacritic-fold. Magic dedup thresholds (0.92/0.78/0.68,
  `backend/app/services/duplicate_detection.py:26,204,211`) remain uncited.
- 🟡 **Alg** — `chunking` uses a whitespace-token proxy that can overflow a 512-*token* embedding
  model; `bibtex._clean_value` strips all braces (destroys `{DNA}` casing / `{\LaTeX}`).
- ✅ **RESOLVED (S4)** — `build_works_query` now lives in `app/services/works_query.py` (moved out of
  the works *endpoint* module, per that module's own docstring: "It lived in the works endpoint
  module, which forced services (`saved_filters`) to import from the HTTP layer — an inverted
  dependency."); `saved_filters` imports it as a normal service. `shelf_membership`
  (`backend/app/services/shelf_membership.py:50`) now raises `NotFoundError`/`PermissionDeniedError`
  (framework-free domain errors, mapped by the `DomainError` handler above) instead of
  `fastapi.HTTPException`, so worker/CLI callers work too.
  `venue_author_summary` does still reach into another service's private name, but it's
  `citation_graph._scope_works` (`backend/app/services/venue_author_summary.py:25`), not
  `export_service` — the `export_service.authors_by_work` it also imports
  (`backend/app/services/venue_author_summary.py:27`) has no leading underscore and is public
  (`backend/app/services/export_service.py:252`). The underlying "reaches into another service's
  internals" concern still holds, just against a different module than named.
- 🟡 **Sec** — `audit.py` is **not** cryptographically tamper-evident despite the docstring; the file
  sink has no rotation. If tamper-evidence is a goal, add hash-chaining.

## Security (residual risks)

- 🟠 **Sec** — **Rate limiter + login throttle fail *open* when Redis is down.** Set
  `PARACORD_PRODUCTION_REQUIRE_REDIS=true` on any exposed instance.
- 🟠 **Sec** — **Plaintext LAN transport by default** (SPA + agent `http://`); tokens (in
  `localStorage`) and PDFs cross the LAN unencrypted. Front a TLS reverse proxy.
- 🟠 **Sec** — **Hardcoded dev DB password as the code default** (`paperracks_dev_password`) — if
  `DATABASE_URL` is unset, a real deployment silently uses a well-known password. Fail closed outside
  development.
- 🟠 **Sec** — **`PARACORD_SECRET_KEY` defaults to `None` → clear-text at rest** with no warning.
  Warn loudly or fail closed when `environment != development`.
- 🟡 **Sec** — **XXE/XML-bomb safety depends on implicit lxml defaults**, not an explicit hardened
  parser. Pin `etree.XMLParser(resolve_entities=False, no_network=True, resolve_dtd=False)`.
- 🟡 **Sec** — Password minimum is only 8 chars, no complexity/breach check; sessions are long-lived
  with no idle timeout / rotation; CSP lives only in the nginx image (the safety test *skips* when
  nginx.conf isn't reachable — run release tests where it is).
- 🟡 **Sec** — `web_find` **DNS-rebinding TOCTOU**: the guard resolves DNS then httpx re-resolves
  (host re-checked per hop, but the resolved IP isn't pinned). Consider pinning the resolved address.
- 🟡 **Sec** — `web_find_allowed_hosts` doesn't reject adding a denylisted host at add-time
  (enforcement is downstream, so the host silently never works); `access_settings` setter writes no
  audit event; `preferences` has no file lock (concurrent multi-user writes can clobber slices).

## Efficiency

See [09 — Efficiency](09_efficiency.md) for the ranked list. Headline: **H1** Python-cosine norm
recomputation (interactive, pgvector-off), **M1** N+1 author lookup in reference matching, **M2**
whole-corpus jobs in one transaction, **M4** unbounded library-scope extractive summary, **M5**
`can_modify_work` per-shelf re-query, **L4** unbounded in-process caches.

## Frontend

- 🟠 **Rob** — **`actOnReference` double-stringifies its body — CONFIRMED, not just probable.**
  `client.ts:1961` passes `body: JSON.stringify({ action })`, and the shared `request()` helper
  (`client.ts:3506`) does `JSON.stringify(options.body)` again on top — so the PATCH body becomes a
  JSON string literal, not a JSON object. The endpoint (`PATCH /works/{id}/references/{reference_id}`,
  `backend/app/api/v1/endpoints/works.py:1603`) expects a `ReferenceActionRequest` object
  (`{"action": "link"|"reject"|"import"}`); a double-encoded body fails that schema. No frontend or
  backend test exercises this call, so the "Link"/"Reject"/"Import" reference-action buttons in
  `WorkDetail.svelte` are likely broken end-to-end. Fix: don't pre-stringify in `actOnReference`.
- 🟡 **Rob** — Raw-fetch upload/stream helpers (`uploadPdf*`, `streamFindOnWeb`) don't surface
  `onQueueFull` — a "queue full" during upload/streaming won't raise the app-wide toast.
- 🟡 **Perf** — PdfReader fully buffers the PDF (`.arrayBuffer()`); large scans load entirely into
  RAM. Consider ranged loading. Semantic-result caps (50 ranked ∩ 500 filtered) can silently hide
  results in a large library — surface the cap.
- 🟡 **UX** — `window.prompt`/`window.confirm` used for saved-filter naming + destructive confirms
  (not theme-aware; inconsistent with the app's own `Modal`).
- 🟡 **Sec** — token in `localStorage` (XSS-exfiltratable). **Updated:** `CitationGraph` no longer
  uses `{@html}`/`escapeHtml` at all — the graph moved to one ECharts-based renderer (2026-07-13
  decision, replacing the old Cytoscape surface); but its tooltip `formatter`
  (`CitationGraph.svelte:301`) now builds an HTML string with node `label`/`venue`/`doi` interpolated
  **unescaped** (`` `<strong>${m.label}</strong>` ``), and ECharts renders a tooltip formatter's
  returned string as HTML. Node labels come from paper titles (library + external/imported metadata),
  so an attacker-influenced title could inject markup into the hover tooltip — the same class of risk
  the old `{@html}` note flagged, now unguarded. Escape `label`/`venue`/`doi` before interpolating, or
  switch the tooltip to ECharts' rich-text (non-HTML) formatter mode.

---

## Future features / expansion notes

Ideas that fit the architecture, roughly ordered by leverage. Several are already flagged as "future"
in `ROADMAP.md`/`SPECIFICATION.md`.

**Ingestion & extraction**
- **Optional ML extraction path** (Nougat/Marker) for hard/scanned documents behind the existing OCR
  seam — the extraction service is already provider-shaped.
- **GROBID horizontal scale** — run multiple GROBID instances behind a balancer; the worker fan-out
  is already there, GROBID is the ceiling ([09 §3](09_efficiency.md#3-the-dominant-cost-at-scale-is-extraction-not-queries)).
- ~~Retryable vs terminal job classification + an owed-enrichment/embedding recovery sweep~~ —
  **done (F2)**, see Pipeline & workers above.
- ~~Reference dedup consolidation job wired into the pipeline~~ — **done (S13/S14)**: startup hook +
  admin button. Remaining idea: a unique index on `dedup_key` once the contested-group backlog is
  clear.

**Search & analysis**
- **pgvector as the default** with a real embedding model (the JSON `vector` source-of-truth already
  mirrors to `vector_pg`); retire the Python-cosine read path for Postgres.
- **Real BERTopic** behind the existing `topic_backend` seam (the name already exists); real
  hierarchical topics.
- **Trigram/GIN indexes** for the substring `fulltext:`/`abstract:` filters, or route them to BM25.
- **Cross-visibility DOI-collision mediation** — the known gap in `ROADMAP.md`: a permission-aware
  conflict message so a user isn't blocked by (or told about) a paper on a shelf they can't see.

**Collaboration & access**
- **At-rest field encryption** using the reserved `PARACORD_SECRET_KEY` (Fernet) for emails / any
  future sensitive fields.
- **TLS-terminating reverse-proxy recipe** + `require-redis` as a documented "LAN-exposed" profile.
- **Shareable saved views / shelves across users** with finer grant granularity.

**UX**
- **In-app admin teleport browser** (the deferred agent-redesign item) so an owner can request
  teleports from the UI.
- **Modal-based confirmations** replacing `window.prompt/confirm`; **ranged PDF loading** for large
  scans; surfaced result caps.
- **Live worker-count reload** (SIGHUP) instead of container restart.

**Platform**
- **Regenerate `FILE_TREE.md` in CI** and add a doc-freshness check so this reference set and the
  OpenAPI schema can't silently drift (there is already `make openapi-check`).
- ~~Bound the in-process caches (`_SUMMARY_CACHE`/`_LAYOUT_CACHE`/`_PREVIEW_CACHE`) with an LRU~~ —
  **done (S10)**, `BoundedTTLCache`. Still no hit/miss/eviction **metric** on them — that part of the
  idea stands.

---

*This register was produced by reading the current code (2026-07-12; re-verified against the code
2026-07-16 — see the update note near the top). Treat each `⚠️`/severity item as a candidate for
`docs/AUDIT.md` and the `docs/WORKPLAN.md` backlog; nothing here has been changed in the code — it is
documentation only.*

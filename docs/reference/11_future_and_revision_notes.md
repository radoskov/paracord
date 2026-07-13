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
> **Still open from those:** the three unbounded in-process caches (the F3b half of the "read paths
> that leak" item). Everything else below stands.

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
- 🟠 **Doc** — `config/server.example.yaml` has a large **documentation-only** key surface: only a
  whitelist is parsed by `_server_settings_from_yaml`. Keys under `processing.*`, `summaries.*`,
  `topics.*`, `audit.*`, `agents.*`, `keywords.*`, `credential_recovery.*`, `security.failed_login_
  lockout.*`, `storage.temp_root/max_upload_mb` are ignored. Wire them up or clearly mark
  reference-only. Also: the OCR enum comment says `full_ml` but the code accepts `pymupdf`.

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
- 🟠 **Alg** — **`Reference.dedup_key` is intentionally non-unique**; duplicates coexist until an
  out-of-band consolidation job runs — but that job is not yet wired into the auto-pipeline. Any code
  reading references must tolerate dupes. *Direction:* schedule/implement the consolidation job.
- 🟡 **Tech** — `Work.topics`/`keywords` (JSONB) duplicate what `TopicAssignment` models relationally
  (drift risk). `ImportStagingItem.duplicates` / `DuplicateCandidate.signals` are opaque blobs
  (not queryable).
- 🟡 **Perf** — `Work.citation_count`/`citation_count_fetched_at` are not indexed although recent
  commits added sortable citation columns — verify the sort path is indexed. `TopicAssignment` lacks
  a composite index on its common `(topic_model_id, scope_type, scope_id, work_id)` access pattern.
- 🟡 **Stab** — Python-side timestamp defaults (not `server_default`) make ordering by `created_at`
  slightly non-monotonic under concurrent multi-client writes; also inconsistent with the columns
  that *do* use `server_default`.
- 🟡 **Tech** — external-citations migration churn (`0057` denormalized → `0058` normalized);
  `references` uses a reserved SQL keyword (quoted everywhere — a footgun for hand-written SQL);
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
- 🟡 **Rob** — No generic exception handler (only `BatchTooLargeError → 413`); `ValueError → 400` is
  repeated per-endpoint. Centralize.
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
- 🟡 **Tech** — `summarize_scope_job` / `topic_model_job` are no-op stubs; per-paper topics exist but
  aren't in the auto chain.

## Services (algorithmic & correctness)

- ✅ **RESOLVED (F1)** — user-supplied `citation_keys` are now sanitised (`_safe_citation_key`):
  structural breakout characters are neutralised while Unicode letters and real key punctuation
  (`. : + / _ -`) are **preserved** (so DBLP/DOI-style and accented keys survive), then de-duplicated.
  *(Still open, lower severity: markdown/pandoc field **values** — titles/authors — aren't escaped.)*
- ⚠️ **PARTIALLY RESOLVED — F3a done, F3b open.** ✅ The read-path write is gone:
  `citation_graph.build_citation_graph` is now read-only (the matcher owns `resolution_status`; a
  work delete re-resolves affected references). ❌ **Still open (F3b):** the three unbounded
  in-process caches — `citation_summary._SUMMARY_CACHE`, `visualization._LAYOUT_CACHE`,
  `external_preview._PREVIEW_CACHE` — are not yet LRU/TTL-bounded (memory creep on a long-lived
  process). This was deferred; discuss before implementing.
- 🟠 **Sec** — **`topic_graph` visibility depends on the caller passing `visible_ids`.** With
  `work_ids` set and `visible_ids=None`, only shadow-filtering applies → IDOR risk if an endpoint
  forgets it. Audit every caller; consider requiring `visible_ids`.
- ✅ **Rob** — ~~`citing_papers.store_citing_papers` wipes links when called with an empty list~~
  **Resolved 2026-07-13 (S12):** the fetch now distinguishes three outcomes — a provider that
  listed citers (replace), an *authoritative zero* answer (replace with empty + stamp
  `works.citing_fetched_at/_source`, migration 0066), and no answer at all (cache kept, failure
  surfaced). A failed fetch can no longer wipe good data, and a genuinely-zero answer no longer
  leaves a stale list forever.
- 🟠 **Alg** — **`agent_protocol.validate_agent_file_id` always returns `False`** (dead stub) — a
  caller relying on it rejects every file. Wire up or delete.
- 🟠 **Sec** — **`model_management` performs no integrity verification of downloaded weights**
  (Ollama pull / `SentenceTransformer(model)` trust upstream); arbitrary model strings are forwarded
  with no allowlist. Typo-squat / compromised-registry risk. Add an allowlist + checksum where
  feasible.
- 🟠 **Alg** — **Naming overreach**: `topic_modeling backend="bertopic"` does **not** run BERTopic
  (it reuses embedding k-means); "hierarchy" is a single nearest-neighbor pass. Rename or implement.
- 🟡 **Alg** — Two parallel arXiv parsers (`identifiers.arxiv_base_id` vs
  `duplicate_detection.split_arxiv_id`) can disagree; unify. `author_matching` over-matches common
  surnames (missing-initial = agreement); `normalize_title` drops non-ASCII instead of folding
  (inconsistent with `author_matching._fold`). Magic dedup thresholds (0.92/0.78/0.68) are uncited.
- 🟡 **Alg** — `chunking` uses a whitespace-token proxy that can overflow a 512-*token* embedding
  model; `bibtex._clean_value` strips all braces (destroys `{DNA}` casing / `{\LaTeX}`).
- 🟡 **Tech** — `saved_filters` imports `build_works_query` **from the endpoint layer** (layering
  inversion / cycle risk); `shelf_membership` raises `fastapi.HTTPException` from a service (breaks
  worker/CLI callers); `venue_author_summary` depends on a **private** `export_service` helper.
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

- 🟠 **Rob** — **`actOnReference` likely double-stringifies** its body (`JSON.stringify({action})`
  where `request()` also stringifies). Probable functional bug — verify against the backend + tests.
- 🟡 **Rob** — Raw-fetch upload/stream helpers (`uploadPdf*`, `streamFindOnWeb`) don't surface
  `onQueueFull` — a "queue full" during upload/streaming won't raise the app-wide toast.
- 🟡 **Perf** — PdfReader fully buffers the PDF (`.arrayBuffer()`); large scans load entirely into
  RAM. Consider ranged loading. Semantic-result caps (50 ranked ∩ 500 filtered) can silently hide
  results in a large library — surface the cap.
- 🟡 **UX** — `window.prompt`/`window.confirm` used for saved-filter naming + destructive confirms
  (not theme-aware; inconsistent with the app's own `Modal`).
- 🟡 **Sec** — token in `localStorage` (XSS-exfiltratable); `CitationGraph` uses `{@html}` (currently
  `escapeHtml`-sanitized — keep it that way).

---

## Future features / expansion notes

Ideas that fit the architecture, roughly ordered by leverage. Several are already flagged as "future"
in `ROADMAP.md`/`SPECIFICATION.md`.

**Ingestion & extraction**
- **Optional ML extraction path** (Nougat/Marker) for hard/scanned documents behind the existing OCR
  seam — the extraction service is already provider-shaped.
- **GROBID horizontal scale** — run multiple GROBID instances behind a balancer; the worker fan-out
  is already there, GROBID is the ceiling ([09 §3](09_efficiency.md#3-the-dominant-cost-at-scale-is-extraction-not-queries)).
- **Retryable vs terminal job classification** + an owed-enrichment/embedding recovery sweep
  (closes the two 🔴 pipeline gaps).
- **Reference dedup consolidation job** wired into the pipeline (uses the existing non-unique
  `dedup_key`).

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
- **Bound the in-process caches** (`_SUMMARY_CACHE`/`_LAYOUT_CACHE`/`_PREVIEW_CACHE`) with an LRU and
  a metric.

---

*This register was produced by reading the current code (2026-07-12). Treat each `⚠️`/severity item
as a candidate for `docs/AUDIT.md` and the `docs/WORKPLAN.md` backlog; nothing here has been changed
in the code — it is documentation only.*

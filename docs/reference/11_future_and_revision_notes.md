# 11 — Future Work & Revision Notes

[← User workflows](10_user_workflows.md) · [Index](00_index.md)

This is the consolidated register of things worth revisiting, drawn from reading the current code.
Each item names the component, the concern, and a suggested direction. Severity is weighted for the
project's scale (**mostly single-user, a few trusted LAN users**), so LAN-multi-user and
egress-facing issues are ranked higher than pure single-user ones.

Legend: 🔴 high · 🟠 medium · 🟡 low. Categories: **Sec**urity · **Alg**orithmic · **Perf** ·
**Rob**ustness · **Stab**ility · **UX** · **Tech**-debt · **Doc**.

---

## Cross-cutting: documentation drift (fix first — cheap, high-value)

The following existing docs are **stale** and contradicted by the current code. Update or supersede
them (this `docs/reference/` set is the new source of truth):

- 🟠 **Doc** — `docs/architecture/{architecture,api_surface,data_model}.md` describe an "M7 audit"
  state: unauthenticated agent stubs, no pgvector, no access control, `/citations/contexts` dead
  stub, only ~7 migrations. All long superseded.
- 🟠 **Doc** — `FILE_TREE.md` lists ~7 migrations and a fraction of the real models/services/
  endpoints/tests. Regenerate or delete.
- 🔴 **Doc/Rob** — `config/agent.example.yaml` documents a schema the real `AgentConfig` pydantic
  model **does not use** (`token_file`, `poll_interval_seconds`, `filesystem.allowed_roots`, …).
  Hand-editing it silently does nothing. Rewrite to the real keys (`server_url`, `refresh_interval`,
  `folders`/`files`, `web_port`, `default_action`, …).
- 🔴 **Doc/Rob** — `agent/systemd/paperracks-agent.service.example` invokes a non-existent `serve`
  command with a non-existent `--config` flag; the unit would fail to start. Fix to `start`.
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

- 🔴 **Rob** — **No RQ retries.** A transient GROBID/network failure fails the job; for extraction
  `_mark_failed` clears the owed marker, so a *transient* outage becomes a **terminal `extract_failed`
  needing a manual re-extract**. Distinguish transient (retryable) vs terminal failures.
- 🔴 **Rob** — **Only extraction has a recovery sweep.** If the worker dies between the extraction
  commit and `enqueue_enrichment` (or between chunking/embedding enqueues), the work is extracted but
  never enriched/chunked/embedded, and nothing recovers it. Add an "owed enrichment/embedding" marker
  analogous to D7.
- 🟡 **Stab** — `_SKIP_STATUSES={"extracting"}` is dead — no code sets `status="extracting"`, so the
  mid-flight guard relies entirely on the deterministic job id.
- 🟠 **Perf/Rob** — `scan_duplicates_job` / `rescan_reference_matches_job` load the whole corpus in
  one transaction (Perf M2). Paginate with `commit_every`.
- 🟡 **Stab** — `find_or_create_reference` + orphan prune could race if two works citing the same new
  reference extract simultaneously (no explicit locking).
- 🟡 **UX/Rob** — `rq_worker_count` is read once at supervisor startup; a change needs a container
  restart (documented, but a SIGHUP reload would be friendlier).
- 🟡 **Tech** — `summarize_scope_job` / `topic_model_job` are no-op stubs; per-paper topics exist but
  aren't in the auto chain.

## Services (algorithmic & correctness)

- 🔴 **Sec** — **`export_service` injects user `citation_keys` verbatim** into `@article{<key>,` /
  `\cite{}` / `[@key]` — a crafted key can break out. Sanitize to `[A-Za-z0-9_-]`. Also
  markdown/pandoc titles/authors aren't escaped.
- 🔴 **Stab** — **Read paths that mutate or leak.** `citation_graph.build_citation_graph` persists
  `reference.resolution_status` mid-read (concurrent-read race); `citation_summary._SUMMARY_CACHE`,
  `visualization._LAYOUT_CACHE`, `external_preview._PREVIEW_CACHE` are unbounded in-process dicts
  (memory leak on long-lived processes). Move the write off the read path; LRU-bound the caches.
- 🟠 **Sec** — **`topic_graph` visibility depends on the caller passing `visible_ids`.** With
  `work_ids` set and `visible_ids=None`, only shadow-filtering applies → IDOR risk if an endpoint
  forgets it. Audit every caller; consider requiring `visible_ids`.
- 🟠 **Rob** — **`citing_papers.store_citing_papers` wipes links when called with an empty list**
  (destructive replace). Guard against an empty fetch overwriting good data.
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

# PaRacORD — Discussions: open choices that need your call

Product- and architecture-level decisions where reasonable options differ. Each has a
recommendation — replying with just the ID + "agree" (or your alternative) is enough. Technical
defects/deferred fixes live in `AUDIT.md`; resolved/stale material is in `ARCHIVED_AUDIT_LOG.md`.
IDs are stable and shared with the 2026-07-02 consolidated audit.

---

## Product / UX

**D18. Library table silently caps at 100 rows.**
The client never sends `limit`, the backend defaults to 100 — with 300 papers the library view
just truncates with no indicator. *Options:* pagination, infinite scroll, or a higher default +
count display. **Recommend server-driven pagination with a total count** — also the prerequisite
for D32.

**D32. Library table "Shelves"/"Racks" columns** (old NEEDS_DISCUSSION 2a). Needs a batched
SEE-filtered serialization in `list_works` (one grouped query per page) + two `columns.ts`
entries; a per-work endpoint already exists for the detail pane. **Recommend: yes, together with
D18.**

**D33. Per-section BM25 scores for lexical hits** (old 2b). Would need `bm25_index` to return
per-field contributions. **Recommend: skip** — semantic/hybrid hits already show the matching
section; low value for the plumbing.

**D34. `summary_provider` UX** (old 2d): both the provider AND the model must be set for LLM
summaries; the admin panel shows a fallback badge. **Confirm the current UX is clear enough**, or
ask for a one-line "provider is extractive — select local_llm to use this model" hint.

## Architecture / stack direction

**D25. Embedding-model registry (runtime DDL, up to 8 models) is over-built for the product.**
Works and is tested, but web-admin-triggered `ALTER TABLE` is a standing risk category, and one
user needs one model (+ multimode experiments). *Options:* (a) keep as-is, treat as frozen;
(b) simplify to single-configured-model + re-embed on change. **Recommend (a) freeze** — sunk
cost that works; revisit only if it causes an incident.

**D26. Hand-rolled BM25F engine vs Postgres FTS.** Genuinely well-built, but permanent bespoke
maintenance (mmap files, signatures, warm endpoint) for something `tsvector` does at this scale.
**Recommend: freeze — never extend it**; if AUDIT D13 leads to a rebuild anyway, that's the
moment to evaluate Postgres FTS instead.

**D27. Backend maintains dual SQLite/Postgres code paths so tests run on SQLite.**
Every feature is written twice (vector fallbacks, dialect branches); most tests never exercise
the dialect users run. **Recommend: gradually move the default test run to Postgres** (the parity
harness exists), then delete SQLite branches — opportunistically, not big-bang.

**D28. Redis/RQ: keep or replace?** Worker isolation for GROBID/OCR/embedding jobs is worth two
containers; the real pain is the fail-open enqueue (AUDIT D7). **Recommend: keep RQ; fix D7.**
(Alternative: a Postgres-backed queue, e.g. procrastinate, drops two containers.)

## Feature scope (needs a workplan slot)

**D31. Spec-conformance small batch** (old NEEDS_DISCUSSION §4, all verified still missing):
audit-event wiring (`shelf.*`, `rack.*`, `paper.metadata_edited`, `annotation.*`, `job.*`,
backup/restore — one line each at known sites), summary provenance columns (computed but not
persisted), annotation JSON export (spec requires it), extra search operators
(`abstract:`/`fulltext:`/`has:grobid`/`file:`/…), LaTeX/Pandoc export formats, import-to-rack.
**Recommend implementing in that order**; audit events + provenance columns + annotation-JSON are
a small, safe first batch.

**D35. ML extraction backend (Nougat/Marker)** — the config flag exists, the worker ignores it,
no extractor code exists; a large feature (torch image, markdown→persistence mapping). *Options:*
(a) build it; (b) drop the flag + detection stub until there's a real need (OCRmyPDF already
covers scanned PDFs). **Recommend (b) for now** — remove the dead seam or mark it experimental;
revisit if GROBID quality on hard PDFs becomes a real pain.

**D36. E2E journey coverage** (CI wiring itself is AUDIT D36a): missing identifier-import,
annotate, export, and duplicates-review journeys. **Recommend: add opportunistically** as those
areas change.

**D37. pgvector remainder** (old FOLLOWUP §3): chunk-level ANN is done (per-model HNSW columns).
Left: document-level `Embedding` still JSON, `pgvector_enabled` default-off, `hash_bow` default
provider. **Recommend: flip the defaults once you routinely run a real model**; keep the
document-level JSON path as the SQLite/test fallback it already is.

**D38. Big spec features** (old NEEDS_DISCUSSION §5, all confirmed still absent):

- **Citation summaries (§8.11)** — per-shelf/rack citation analytics; the `citations.py` router
  is literally empty. The largest headline-goal gap — this is the feature that pays off the
  product's own pitch.
- **Citation-graph depth (§8.9)** — 8 graph modes / PageRank sizing / rich encodings vs today's
  2 modes + degree sizing.
- Smaller deltas: preprint↔published duplicate kind, backup REST endpoints, CSV/Zotero/
  watched-folder import, reference-string fallback parser (anystyle/refextract).

**Say which (if any) to scope into the next workplan; recommend starting with §8.11.**

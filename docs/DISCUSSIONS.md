# PaRacORD — Discussions: open choices that need your call

Product- and architecture-level decisions where reasonable options differ. Each has a
recommendation — replying with just the ID + "agree" (or your alternative) is enough. Technical
defects/deferred fixes live in `AUDIT.md`; resolved/stale material is in `ARCHIVED_AUDIT_LOG.md`.
IDs are stable and shared with the 2026-07-02 consolidated audit.

---

## Product / UX

**D18. Library table silently caps at 100 rows.** — **DECIDED 2026-07-02 · implementing.**
Server-controlled pagination, behaving like search results: **100 papers per page by default**,
even with no query (then column ordering alone drives the order). Controls: prev/next, a "go to
page" number field, and a page dropdown listing every valid page. A per-user **"max papers per
page"** setting lives in the profile; a **global maximum** (server-protection cap) lives in the
admin tab and clamps the per-user value. Effective per-page = `min(request or user pref or
default, global max)`. Prerequisite for D32.

**D32. Library table "Shelves"/"Racks" columns** (old NEEDS_DISCUSSION 2a). — **DECIDED
2026-07-02 · implementing** (with D18). Batched SEE-filtered serialization in `list_works` (one
grouped query per page) + two `columns.ts` entries.

**D33. Per-section BM25 scores for lexical hits** (old 2b). — **DECIDED 2026-07-02 · DEFERRED.**
Semantic/hybrid hits already show the matching section; revisit if lexical-only users ask for it.

**D34. `summary_provider` UX** (old 2d). — **DECIDED 2026-07-02 · skip for now** (owner can't
validate the UX without a PC on hand; revisit later).

## Architecture / stack direction

**D25. Embedding-model registry (runtime DDL, up to 8 models).** — **DECIDED 2026-07-02 · FROZEN.**
Kept as-is; do not extend. Web-admin `ALTER TABLE` stays but is treated as a closed surface;
revisit only if it causes an incident.

**D26. Hand-rolled BM25F engine vs Postgres FTS.** — **DECIDED 2026-07-02 · FROZEN.** Kept, never
extended. If AUDIT D13 forces a rebuild, evaluate Postgres FTS at that point.

**D27. Backend dual SQLite/Postgres code paths.** — **DECIDED 2026-07-02 · accepted direction.**
Gradually move the default test run to Postgres (parity harness exists), then delete SQLite
branches — opportunistic, not big-bang.

**D28. Redis/RQ: keep or replace?** — **DECIDED 2026-07-02 · keep RQ**; the real pain is the
fail-open enqueue (AUDIT D7), to be fixed there. (Owner asked for a detailed D7 write-up before
that fix — see the AUDIT D7 entry.)

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

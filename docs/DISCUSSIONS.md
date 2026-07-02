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

**D31. Spec-conformance gaps — six small, independent, additive items** (old NEEDS_DISCUSSION §4,
all verified still missing at HEAD). None is a bug; each is a SPECIFICATION-required feature that
was never built. Detailed so the scope is concrete:

1. **Audit-event wiring (§7.6).** The audit log exists and emits many events (login, `work.deleted`,
   `import.*`, `teleport.*`, …), but several the spec *requires* are never emitted:
   `shelf.created`/`shelf.modified`, `rack.created`/`rack.modified`, `paper.metadata_edited`,
   `annotation.created`/`annotation.edited`, `job.started`/`job.completed`/`job.failed`, and
   backup/restore events. *Impact:* an admin auditing "who changed what" is blind to shelf/rack
   creation+rename, metadata edits, annotation activity, and job lifecycle. *Fix:* one
   `record_event(...)` line at each known site (shelf/rack create+update service fns; the
   metadata-edit path; annotation create/edit endpoints; the RQ job wrapper for
   started/completed/failed; the backup/restore commands). Low risk — the only care is that a few
   tests assert audit-event *counts*, so update those expectations in the same pass.
2. **Summary provenance columns (§8.14.2).** Summary generation already *computes* provenance
   (provider requested, provider actually used, whether it fell back to extractive) but only
   returns it transiently — it is never stored. *Impact:* for an existing stored summary you can't
   tell how it was produced (model/provider, fallback, source sections). *Fix:* a small migration
   adding columns to `Summary` (`provider_requested`, `provider_used`, `fallback`, + per spec
   source-section labels / content hash / user + params) and write them on creation.
3. **Annotation JSON export (§8.8.7).** Annotation export supports `markdown` + `text`; the spec
   also requires `json`. *Impact:* no machine-readable annotation export. *Fix:* add a `json`
   branch to the export endpoint (it already assembles the annotation objects — just serialize).
4. **Additional search operators (§14.2).** The parser handles author:/year:/venue:/tag:/type:/
   title:/doi:/arxiv:/status:/shelf:/rack:/cites:/cited_by_local:/has:pdf|references|notes|
   annotations|summary|abstract. Still unparsed (silently ignored): `abstract:`/`fulltext:`/
   `summary:` (field-scoped text), `has:grobid`/`has:ocr` (extraction state), `file:…` (filename),
   `duplicate:`/`version:`/`warning:*` (review state). *Fix:* extend the parser with the missing
   tokens and map each to the works query; `fulltext:` searches extracted body text (chunks/TEI).
5. **Missing export formats/targets (§8.13).** Have: bibtex/biblatex/ris/csl-json/markdown/html/
   text/styled. Missing: LaTeX `\cite` command output, a Pandoc-Markdown citation list, plus
   import-batch / missing-references export *targets* and emitting unresolved-reference strings.
   *Fix:* add the two renderers alongside the existing ones and wire the extra scopes/targets.
6. **Import can target a rack (§8.1).** Imports accept only `target_shelf_id`, not
   `target_rack_id`. *Fix:* add `target_rack_id` to the import request(s) and place imported works
   onto the rack the same way `target_shelf_id` does.

**Recommended order: 1 → 2 → 3 first (a small, safe batch), then 4, 5, 6.** Awaiting owner go to
implement (which items, and whether all at once).

**D35. ML extraction backend (Nougat/Marker).** — **DECIDED 2026-07-02 · option (b), implementing.**
Keep only `ocrmypdf` + `pymupdf` OCR backends. Drop the dead Nougat/Marker extraction seam: the
`nougat`/`marker` extraction-backend config + `detect_providers()` "extraction" stubs and the
`full_ml` OCR-backend option (worker never used them, no extractor code exists). Removal, not a
feature; revisit only if GROBID quality on hard PDFs becomes a real pain.

**D36. E2E journey coverage.** — **DECIDED 2026-07-02 · implementing, approach left to me.** Wire
Playwright into CI and improve/expand the journeys as far as practical: identifier (arXiv/DOI)
import, annotate, export, duplicates-review, plus admin flows and the new pagination/queue UI.
Supersedes AUDIT D36a (CI wiring folded in here).

**D37. pgvector defaults.** — **DECIDED 2026-07-02 · flip defaults so pgvector + ANN works.** Turn
`pgvector_enabled` on by default and make the effective search/related path use the ANN (HNSW) route
so a real registered model gets sub-linear search out of the box; keep the JSON + Python-cosine path
as the SQLite/test and no-Redis/no-pgvector fallback. Open sub-choice being handled in
implementation: the *default embedding provider* stays `hash_bow` (zero-dependency, so a lightweight
install still works) — ANN kicks in once a real model (sentence-transformers / Ollama) is
registered; forcing a heavy model as the out-of-box default is NOT part of this.

**D38. Big spec features** (old NEEDS_DISCUSSION §5, all confirmed still absent):

- **Citation summaries (§8.11)** — per-shelf/rack citation analytics; the `citations.py` router
  is literally empty. The largest headline-goal gap — this is the feature that pays off the
  product's own pitch.
- **Citation-graph depth (§8.9)** — 8 graph modes / PageRank sizing / rich encodings vs today's
  2 modes + degree sizing.
- Smaller deltas: preprint↔published duplicate kind, backup REST endpoints, CSV/Zotero/
  watched-folder import, reference-string fallback parser (anystyle/refextract).

**Say which (if any) to scope into the next workplan; recommend starting with §8.11.**

# PaRacORD — Work Plan (2026-06-29)

This is the **execution-ordered** plan for finishing the app. It supersedes the loose "Next
recommended items" list that previously lived in `PROGRESS.md`. It reconciles three inputs:

1. `SPECIFICATION.md` (§20 milestones M0–M8) — the destination.
2. `docs/AUDIT.md` (2026-06-25 base + 2026-06-26 addendum) — findings, re-validated below.
3. The actual code at `HEAD` (validated 2026-06-29) — what is really done.

**Governing principle (per maintainer):** drive *steady progress toward a fully functional app*.
Front-load the work that unblocks whole feature areas; **defer minor polish, micro-optimizations,
and fine-tuning to the end** (Stage 7) so engineering time is not lost tinkering with non-blocking
details. Each stage below lists a concrete *Definition of Done*; several map onto the skipped
acceptance contracts already in `backend/tests/future/` — enabling those tests is the completion
signal.

---

## Audit re-validation snapshot (2026-06-29)

Verified against the current tree (not the audit's original commit). See the table at the bottom
of `docs/AUDIT.md` for the same data in audit-ID order.

**Resolved since the audit was written:** C1, C2 (migration parity), C3 (core FKs), C4 (audit
JSONB), C5 (docker dev/prod targets), H1 (`httpx2==2.4.0`), H4 (agent stub auth → 501/410), H5
(prod build), P1/item4 (`arxiv_base_id` + unique indexes), P1/item5 (DOI normalization + SQL
pushdown), P2/item6 (nav shell + Admin UI), P2/item9 (scope summaries), P2/item10-partial (PDF
upload + identifier import frontend + backend).

**Still open and scheduled below:**
- **A1** (HIGH, *correctness*) — uploaded `managed_path` PDFs cannot be extracted. → Stage 1
- **A3** (process) — `make ready`/`ci` don't mirror CI (no `frontend-check`/`test-migrations`). → Stage 1
- **B1** (extraction) — GROBID options hardcoded; no coordinate extraction. → Stage 2
- **PDF.js reader**, **Cytoscape graph**, **metadata-review UI** — packages installed, unused. → Stages 3–4
- **RIS/CSL import** — completes §8.1. → Stage 4
- **Agent manifest/teleport** — the distinctive remote feature; still scaffold. → Stage 5
- **H2** (AI read-path writes), embedding/topic/summary **provider interface**. → Stage 6
- **H3** fuzzy-title perf, **C3/C4** remaining edges, **H7** pgvector, export polish, auth
  hardening, security-doc truthfulness, backups, prod smoke. → Stage 7 (deferred polish)

---

## Stage 1 — Correctness & CI integrity  *(small, do immediately)*

Cheap, high-leverage fixes that protect everything built afterward. A shipped feature (upload)
is currently broken by A1; fix it before building more on top.

1. **A1 — Managed-path extraction fix. ✅ DONE (2026-06-29).** Added the shared resolver
   `app/services/file_paths.py::resolve_backend_readable_pdf_path(db, *, file, settings)` —
   resolves `server_path` (validated against the server-folder source root) and `managed_path`
   (validated against `managed_library_root`), picking the primary available location and raising
   `FileLocationError` (a `ValueError` subclass with a `kind` flag → 403/404 at the API layer).
   `extract_and_store()` (previously `server_path`-only, with no root check) and
   `files.py::stream_file` both route through it. Regression test
   `test_extraction.py::test_extract_and_store_reads_managed_path`; full backend suite green
   (175 passed, 7 skipped).

2. **A3 — `make ready`/`ci` mirror CI.** Make readiness fail on frontend or migration regressions:
   ```makefile
   check:         lint test test-migrations
   frontend-check: frontend-install frontend-test frontend-build
   ready:         fix precommit check frontend-check
   ci:            lint test test-migrations frontend-check check-secrets
   ```
   *DoD:* `make ready` fails if the frontend build or migration parity fails; runbook documents
   exactly what `ready` covers.

---

## Stage 2 — Extraction depth  *(unblocks the reader, graph, and annotations)*

The single most leverage-rich backend item: real PDF coordinates are the prerequisite for the
PDF.js reader, anchored highlights, and citation→mention jumps.

3. **B1 — GROBID settings + coordinate extraction. ✅ DONE (2026-06-29).** GROBID options are
   now config-driven (`grobid_consolidate_header/_citations`, `grobid_include_raw_citations`,
   `grobid_segment_sentences`, `grobid_coordinate_elements`), read from the `processing.grobid:`
   YAML block; `GrobidClient` builds the form data (incl. repeated `teiCoordinates` fields) from
   settings — the hardcoded flags and the TODO are gone. `tei_parser` parses the `coords`
   attribute into `pdf_coordinates` (a list of `{page,x,y,w,h}` boxes, multi-box for line wraps),
   which replaced the four scalar `pdf_*` columns on `CitationMention` (migration
   `0013_citation_pdf_coordinates`, §9.3). The citation-context API exposes `pdf_coordinates`
   plus convenience `pdf_x/y/w/h` from the primary box. The acceptance test
   `test_future_grobid_coordinates_acceptance.py` was rewritten to be deterministic (fixture-driven
   through the real `extract_and_store` + HTTP read) and is now enabled. Backend suite: 179 passed,
   6 skipped; migration parity green on Postgres.

---

## Stage 3 — The real reader & interactive graph  *(biggest user-facing leap)*

`pdfjs-dist` and `cytoscape` are already in `package.json` but unused. This stage turns the
"debug console" into the intended reading application.

4. **PDF.js reader (replaces the `<iframe>`).** Coordinate-anchored highlight overlay, marker→ref
   and ref→mentions jumps, page thumbnails, in-app text search, and a real selection that captures
   `coordinates` for annotations (currently always null). Depends on Stage 2 coordinates.
   *DoD:* highlights round-trip to stored annotation coordinates; clicking a citation marker scrolls
   to the reference; annotation create captures a real selection.

5. **Interactive Cytoscape citation graph.** Replace the text edge-list with an interactive canvas:
   click-to-open nodes, layout options, version collapse, centrality sizing. **Per maintainer
   guidance, keep a rendering-mode toggle** (interactive Cytoscape ↔ lightweight list) and do *not*
   sacrifice the detailed interactive view for graphs of a few thousand nodes; add server-side
   scope limits + progressive rendering only as a guard for very large graphs.
   *DoD:* graph renders interactively for a shelf/rack scope; mode toggle works; click-to-open
   navigates to the work.

---

## Stage 4 — Metadata review & import completion  *(closes §8.1 / §8.12 gaps)*

6. **Metadata review / edit UI.** Surface the existing backend (`GET /works/{id}/metadata`,
   `POST /works/{id}/metadata/select`, `POST /works/{id}/enrich`): a conflict dashboard to compare
   per-field assertions by source, pick the canonical value, edit a work, and a per-work "Enrich"
   button. Add **per-field `user_confirmed` locking** (§8.12) so enrichment never overwrites a
   field the user has confirmed (today it's per-work all-or-nothing).
   *DoD:* a user can resolve a title conflict and edit metadata entirely from the UI; a confirmed
   field is not overwritten by re-enrichment.

7. **RIS + CSL JSON import.** Add `POST /imports/ris` and `POST /imports/csl` mirroring the BibTeX
   path (dedup by normalized DOI/title, `ImportBatch` + audit event). Completes the headline §8.1
   ingestion set alongside server-folder, BibTeX, upload, and identifier import.
   *DoD:* round-trip a RIS and a CSL-JSON file into works with dedup; tests cover both.

---

## Stage 5 — Local agent vertical (M5)  *(the distinctive remote-machine feature)*

The agent is the project's differentiator and is still mostly enrollment scaffold. Build it as one
focused vertical (audit "Agent M1").

8. **Agent manifest + teleport.**
   - Agent: durable local index (SQLite) of scanned files under allowed roots.
   - Server: `AgentFile` model/table; token-authenticated manifest ingestion; user-authorized
     teleport session; chunked or one-shot upload with SHA-256 verification into the
     content-addressed managed store.
   - Audit events for manifest / teleport-requested / teleport-completed|failed.
   - **Remove the raw-path teleport helper** (`agent/teleport.py` TODO) before exposing any command —
     resolve strictly by opaque `local_file_id`.
   *DoD:* enable `backend/tests/future/test_future_agent_teleport_acceptance.py`; server never asks
   for and agent never exposes a raw path; a teleported file becomes a managed-library file and is
   extractable (via the Stage 1 resolver).

---

## Stage 6 — AI pipeline hardening  *(provider architecture; keep lexical baselines)*

Move the lightweight baselines behind provider interfaces so a real local model can drop in without
a rewrite. **Keep the hash-BOW / TF-IDF / extractive providers as the default + test providers.**

9. **H2 — Embeddings off the read path.** Generate embeddings on import / background RQ job, not
   inside `POST /search/semantic`; use upsert / `ON CONFLICT DO NOTHING` on `(entity_type,
   entity_id, model_name)`. Introduce an embedding-provider interface (`hash_bow` default;
   `sentence-transformers`/`ollama` opt-in).
   *DoD:* a normal search performs no writes; concurrent searches don't race; provider is swappable.

10. **Summaries & topics provider interface + semantic dual-mode.** Per maintainer note, offer the
    user a **choice of semantic-search modes** (lexical vs. embedding) rather than silently picking
    one. Add provider seams for local-LLM summaries (Ollama, opt-in) and a BERTopic option for
    topics, keeping the current deterministic baselines.
    *DoD:* enable `test_future_local_llm_acceptance.py` and `test_future_topic_modeling_acceptance.py`
    behind opt-in config; baselines remain the default with no new hard dependency.

---

## Stage 7 — Deferred polish & hardening  *(do last; non-blocking)*

Explicitly postponed so they don't consume time mid-build. Pull one forward only if it becomes a
user-visible problem (e.g. import latency for H3).

- **H3** — fuzzy-title dedup: `rapidfuzz` + normalized-title blocking/trigram index; move
  full-library scans to RQ. *(only when import latency is actually felt)*
- **C3/C4 remainder** — weak FKs (`Location.agent_id`, `Reference`, `CitationMention`), extend
  `JSONB` variant to remaining JSON columns, then assert autogenerate-clean in the parity test.
- **H7** — pgvector column + index + `CREATE EXTENSION vector` (ships with the real embedding model
  from Stage 6, not before).
- **Export polish** — CSL styles (citeproc), preview, copy-to-clipboard, search-result/graph/
  selection scopes, live always-current shelf/rack bibliography (§8.17.3).
- **Auth hardening (deferred M0)** — login rate limiting / lockout; in-app change-password with
  session revocation.
- **Security-doc truthfulness** — implement at-rest field encryption (`PARACORD_SECRET_KEY`) *or*
  correct `SECURITY.md` (M2/B8); remove/enforce `guest_access_enabled` (M3); SSRF hardening:
  URL-encode identifiers, forbid cross-host redirects (M5); reword egress copy (L).
- **Ops** — production smoke target (`prod-smoke`, B10); backup/restore (§8.16); emit and surface
  read/view audit events `file.viewed`/`downloaded`/`paper.viewed` (§7.6); Postgres-backed
  integration suite for FK-cascade/timestamptz/JSONB-query behavior.

---

## Sequencing rationale

```
Stage 1  correctness/CI ──► Stage 2  GROBID coordinates ──► Stage 3  reader + graph
                                                  │
Stage 4  metadata UI + RIS/CSL  ◄─────────────────┘
Stage 5  agent vertical (independent; can parallelize with 3–4)
Stage 6  AI provider hardening (independent; after 1)
Stage 7  deferred polish (last)
```

Stage 2 gates Stage 3 (coordinates → anchored reader). Stage 5 (agent) and Stage 6 (AI) are
independent of the reader work and can run in parallel by a second contributor per `WORK_SPLIT.md`
(Agent C owns the agent; Agent I owns AI). Everything in Stage 7 is intentionally last.

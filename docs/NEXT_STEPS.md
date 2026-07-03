# PaRacORD — Next steps (2026-07-03)

Written after the 2026-07 build round (the `WORKPLAN_2026-07.md` tracks + a test-expansion pass)
landed. Everything below is verified green locally: **864 backend + 4 migration-parity + 124
frontend + 27 E2E (2 profile-gated skips)**, ruff/secrets/OpenAPI clean.

## What shipped this round
- **Track A — 19 audit fixes.** Security (SSRF guard, CSP + security headers, non-root containers,
  random DB password), correctness (enrichment resilience, resilient folder import, worker-waits-
  for-migrations, startup loose-paper backfill), performance (BM25 rebuild off the read path from
  chunks, batch embedding, background full-library dedup scan, read-only topic path, numpy kNN,
  HNSW provisioning split), hygiene (hash-pinned backend lockfile, httpx2 2.5.0).
- **Track B — D31 spec conformance.** Audit-event wiring + append-only JSONL file sink + paginated
  audit UI; summary provenance columns; annotation JSON export; new search operators
  (`abstract:`/`fulltext:`/`summary:`/`file:`/`has:grobid|ocr`/`duplicate:`/`version:`/`warning:`);
  LaTeX `\cite` + Pandoc-Markdown export formats + import-batch/missing-refs targets.
- **Track C — the visualization module.** Citation counts (OpenAlex→S2→Crossref, shown in the paper
  view); an extensible provider→`VizPayload`→view-registry scaffold with lazy ECharts; the
  Litmaps-style **temporal citation map** (both-axis dropdowns, 6 axes, encodings); **PCA
  embedding-cluster map**; **§8.11 citation summaries** (most-cited local/external,
  cited-but-missing, bridge papers via exact Brandes betweenness, isolated, chronological);
  **co-citation/coupling network, topic river, similarity heatmap**; **§8.9 graph depth** (PageRank/
  betweenness sizing, color-by shelf/tag/topic/status, edge-width by mentions, warning badges,
  per-work neighborhood endpoint); **UMAP opt-in** layout (AI extra image).
- **Testing + fixes.** E2E grew to 27 journeys (rack/shelf/paper/tag CRUD + rename + reassign,
  visualizations, citation summary, pagination). Fixed a real regression (async duplicate-scan UI
  showed no results) and closed rename/tag gaps (rename shelves/racks/tags, delete tags, list a
  paper's applied tags).

## Recommended next steps (prioritized)

1. **Push + validate in CI** (immediate). 67 local commits; the CI Playwright job will exercise the
   journeys on CI infra.
2. **Ship a real embedding model as the practical default.** pgvector ANN is now on by default, but
   the default provider is still hash-BOW (lexical). Registering a sentence-transformers / Ollama
   model (Admin → AI) is what makes semantic search, related-papers, the embedding-cluster map, the
   similarity axes, and topic quality genuinely good — the biggest quality unlock now that the
   infra exists. Decide whether to ship one as the default (weigh image weight / first-run download).
3. **D38 "smaller deltas"** (not in this round): preprint↔published duplicate detection;
   backup/restore as REST endpoints (currently CLI only); CSV/TSV + watched-folder + Zotero import;
   a reference-string fallback parser (anystyle/refextract) for citations GROBID can't structure.
4. **Citation-count freshness.** Counts refresh on enrichment only; add a periodic/opt-in refresh
   job so they don't go stale.
5. **Merge-tags action.** Tag rename rejects a name collision with 409; a "merge tags" action is the
   natural complement.
6. **Prod hardening.** The prod nginx *master* still runs as root (workers are non-root); TLS is
   warned-on but not enforced (D3). A prod-deployment runbook + non-root nginx master would finish
   the hardening.
7. **UMAP by default?** Currently opt-in via the AI extra image. If prettier clusters are wanted
   out of the box, decide on adding numba/umap-learn to the base image (~tens of MB llvmlite).
8. **CI E2E with profiles.** The GROBID + online-identifier journeys skip in CI (profile-gated); a
   scheduled CI run with the `extraction`/`ai` profiles up would cover them.
9. **Deferred by decision:** D33 (per-section BM25 scores) and D34 (summary_provider UX) remain
   intentionally parked; revisit if they come up.

See `docs/AUDIT.md` / `docs/DISCUSSIONS.md` for any remaining open items and
`docs/agent_handoffs/2026-07-03-*.md` for per-task detail.

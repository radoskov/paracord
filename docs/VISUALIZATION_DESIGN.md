# PaRacORD — Visualization module design (D38 / §8.9 + §8.11)

Design notes for a **visualization module**: several purpose-specific citation/topic graphs, a
clean shared visual style, and — most importantly — an architecture built for future extension.
This is a design/discussion document (owner-driven, 2026-07-02); nothing here is implemented yet.

Grounding facts checked against the code at HEAD:
- **Citation counts are NOT fetched or stored** today (no `citation_count` on `Work`), but the
  enrichment connectors already integrated — Crossref (`is-referenced-by-count`), OpenAlex
  (`cited_by_count`), Semantic Scholar (`citationCount`) — all return them. So fetching + storing +
  showing in the paper view is very feasible.
- **No embedding→2D projection exists** (no PCA/UMAP/t-SNE anywhere); the topic graph does pairwise
  cosine capped at `MAX_NODES=400`.
- **Frontend has only Cytoscape** (+fcose) for networks — no plotting library for XY/scatter/heatmap.

---

## 1. Visual style (owner: "like Plotly/Seaborn themes — appealing but above all easy to read")

The frontend deliberately runs lean (6 runtime deps, heavy ones lazy-loaded). To get that
clean-and-readable look without bloat:

- **Networks** (node-link citation/cluster graphs): keep **Cytoscape** (already in).
- **XY / scatter / timeline / heatmap** (the Litmaps-style views): add **one** plotting library,
  **lazy-loaded** (as PDF.js and Cytoscape already are), so it never touches initial load.
  - Options, lightest→heaviest: **Observable Plot** (small, declarative, Seaborn-like defaults) ·
    **Vega-Lite** (clean, a bit bigger, very declarative) · **ECharts** (turnkey, mid-weight) ·
    **Plotly.js** (the exact look you named, but ~1 MB+ gzipped — the heaviest).
  - Recommendation: **Observable Plot** or **ECharts** — 90% of the Plotly look at a fraction of
    the weight. Only reach for Plotly.js if you specifically want its interactions and accept the size.
- A **shared theme layer**: one accessible categorical palette + sequential/diverging ramps,
  consistent light/dark, applied across every view so they read as one system.

**DECISION NEEDED — plot library:** Observable Plot (rec) / ECharts / Vega-Lite / Plotly.js.

---

## 2. Graph types

### 2a. Temporal citation map — "Litmaps-style" (the flagship)
A scatter where each paper is a point, with **swappable axes** (this is the extensible core):
- **X (default): publication year** (`Work.year`, already stored/indexed).
- **Y options:**
  - **Citation count** — external impact. *Needs the fetch below.* Only for papers that resolve to
    a DOI/arXiv/OpenAlex id (others: unknown → shown muted / excluded).
  - **Local citation degree** — how many of *your* papers cite it. Always available (from the
    reference table), no fetch, no external dependency. A great always-on default before counts land.
  - **Similarity to a focus paper** — cosine of embeddings to a selected seed paper (or the
    centroid of a selection/shelf). *Similarity is pairwise, so it needs a reference point* — the UI
    picks a "focus". Cheap (one dot product per paper).
  - **Topic similarity to a focus** — same, in topic space (if topics are available).
- **Encodings (the "additional axes" you asked about):**
  - **Size** = citation count or local in-degree (impact at a glance).
  - **Color** = topic / shelf / tag / reading-status (categorical; reuses existing data).
  - **Marker shape** = in-library vs cited-but-absent (surfaces gaps to import).
  - Optional **citation edges** overlaid → turns the scatter into a true temporal citation map.
- **Extra axis idea:** **citation velocity** (citations ÷ years-since-publication) — separates
  "seminal/steady" from "hot/rising", very much the Litmaps insight.

### 2b. Embedding-cluster map (≈ what exists now, made lighter + prettier)
Papers placed in 2D by embedding proximity, colored by cluster/topic.
- **Layout, keeping it light:**
  - **PCA-2D (numpy, instant, no new dependency)** — the default. Linear but fine at this scale.
  - **UMAP** — much better separation, but pulls `umap-learn` (+ numba): make it an **opt-in**
    (like the sentence-transformers / torch extras), not a default dep.
  - Compute the layout **server-side and cache** it keyed by (scope, model, embedding-version), so
    it's computed once, not per view. Cap nodes (today's 400) with server-side sampling for big scopes.
- Clustering + labels: reuse the existing topic k-means / keyword labels for coloring.

### 2c. Citation network (the current node-link graph) + §8.9 depth
Keep and deepen: PageRank/centrality node sizing, color-by shelf/tag/topic/status, edge thickness by
mention count, warning badges, per-work neighborhood, the fuller mode matrix.

### 2d. Other views worth having (all feasible at this scale)
- **Co-citation / bibliographic-coupling network** — link papers that cite the same works (or are
  cited together); reveals "schools of thought". Computable from the reference table.
- **Topic river / streamgraph** — topic prevalence across publication years (how your library's
  themes evolved).
- **Similarity heatmap / adjacency matrix** — pairwise similarity for a small selection (~≤50);
  great for "how related are these papers".
- *Probably NOT feasible:* a per-paper **citation-over-time** curve — free APIs give a current
  count, not the historical time series. Flag as out of scope unless a data source appears.

---

## 3. §8.11 citation summaries (the analytics behind the graphs)
The same computed layer feeds textual analytics (per scope, cached + versioned): most-cited local /
external works, frequently-cited-but-missing works, bridge papers, isolated papers, chronological
distribution. The graphs are the visual face; the summaries are the numeric face — one service.

---

## 4. Architecture for extension (owner: "designed for future extension")

A **provider + renderer registry** so a new graph = one provider + one renderer entry, no plumbing:

- **Server — visualization data providers.** `provider(scope, view_type, params) -> VizPayload`,
  where `VizPayload` is normalized:
  `{ nodes:[{id, x?, y?, size?, color_group?, shape?, meta}], edges?:[...],
     axes?:{x:{label,scale}, y:{label,scale}}, legend?, cache_key }`.
  Each view type (temporal-map, embedding-cluster, citation-network, co-citation, topic-river,
  heatmap) is a registered provider. All heavy metrics — citation count, local degree, PCA coords,
  similarity-to-focus, centrality — are computed and **cached** here, reusing `ScopeResolver`.
- **Frontend — view registry.** `view_type -> renderer`; Cytoscape for networks, the plot lib for
  XY/scatter/heatmap. Every renderer consumes the same `VizPayload`. Shared theme layer.
- Adding a view later: register a provider + a renderer. No endpoint/schema churn.

---

## 5. Prerequisite: fetch + store citation counts
- Add `Work.citation_count`, `citation_count_source`, `citation_count_fetched_at` (migration).
- Parse the count in the Crossref / OpenAlex / Semantic Scholar connectors (already called during
  enrichment); pick a source priority; **show it in the paper metadata view**.
- It's a snapshot → cache + periodic/opt-in refresh; papers without a resolvable id have no count.

---

## 6. Open decisions (owner)
1. **Plot library:** Observable Plot (rec) / ECharts / Vega-Lite / Plotly.js.
2. **Embedding layout:** PCA-default (light, no dep) with UMAP as an opt-in extra? (rec: yes.)
3. **Citation counts:** OK to add the field + fetch from OpenAlex/S2/Crossref + surface in the paper
   view, with periodic refresh? (rec: yes — it unlocks the flagship Y-axis and the §8.11 analytics.)
4. **Phasing:** suggested order — (P1) citation-count fetch + paper-view display; (P2) the
   provider/renderer scaffold + the temporal citation map with local-degree & citation-count axes;
   (P3) embedding-cluster (PCA) + topic coloring; (P4) §8.11 textual summaries on the same layer;
   (P5) co-citation / topic-river / heatmap + §8.9 network depth. Each phase is shippable alone.

This is a mini-project, not a one-shot — it should land phase by phase off this doc.

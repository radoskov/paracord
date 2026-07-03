# Handoff — Track C P3 embedding-cluster map (PCA-2D) (2026-07-03)

Registered the **second** visualization view on the P2 provider/renderer seam: `embedding_cluster`,
a scatter that places a scope's SEE-filtered papers in 2D by embedding proximity, colored by topic
cluster. All work committed on `main` (not pushed).

## Commits (on `main`)

- `756e017` — `backend: add embedding_cluster viz provider (PCA-2D, cached, topic-cluster coloring)`
- `4c28129` — `frontend: add embedding-cluster scatter renderer + register in viz page`
- (this docs commit updates `PROGRESS.md` + adds this note)

## 1. Backend provider — `backend/app/services/visualization.py`

`@register_viz("embedding_cluster")` — one decorator, no plumbing (the endpoint + frontend page
already dispatch dynamically). Returns the same normalized `VizPayload` as `temporal_map`.

**Layout — PCA-2D (`_pca_2d`), the §2b/D3 default, numpy-only, deterministic.** Mean-center the
n×d matrix, `np.linalg.svd`, project onto the top 2 right-singular vectors. Component **signs are
fixed** (largest-magnitude loading made positive — the sklearn `svd_flip` convention) so the same
input always yields the same coordinates across runs/platforms. Pads to two columns when the data
spans a single component. No new dependency (numpy is already in `backend/requirements.txt`).

**Vector source (`_scope_dense_matrix`) — stored vectors, never re-embedded on the read path for a
real model:**
- Primary: `topic_modeling._paper_dense_vectors(db, works, embedding_model)` — the same
  related-works / topic-graph embedding access. A real model's mean-pooled stored chunk vectors are
  reused; papers with **no pre-indexed vector are skipped (D19)** and reported as
  `"{n} papers not indexed for this model — reindex to include them."` (not embedded inline).
- Fallback: when only the hash-BOW baseline is active (`_paper_dense_vectors` returns `None`), embed
  each paper's title+abstract with the resolved baseline provider. **hash-BOW vectors are dense
  256-d and PCA-usable**, so the view still renders with the *default* provider — with an honest
  note (`"…uses the built-in baseline embedder; enable a real embedding model … for sharper
  clusters."`). Papers with no text are omitted (noted). This is the vector source exercised by the
  default stack in tests; the real-model + D19-skip path is covered by monkeypatching
  `_paper_dense_vectors`.

**Coloring — reuses the topic modeller (§2b "reuse the existing topic k-means / keyword labels"):**
cluster papers with `topic_modeling._kmeans` over the nD dense vectors (k = `min(DEFAULT_MAX_TOPICS,
n)`), label each cluster from its top-2 TF-IDF terms via `_tfidf` + `_centroid` + `_cluster_keywords`
(`_cluster_labels`). `color_group` = `"1. attention, translation"` (id-prefixed so labels stay
distinct even on keyword collision). `legend = {color_by: "cluster", groups: […]}`. `size` reuses
the shared `_size_value` (local degree by default, from `build_citation_graph` over the placed set).

**Cache — the scope-keyed cache `_METRIC_CACHE_NOTE` flagged in P2.** `_LAYOUT_CACHE` (an in-process
dict, fine for the mostly-single-user / few-LAN-user scale) is keyed by
`(scope_signature, model)` where `scope_signature` = sorted placed-work-id tuple, storing
`(vector_hash, coords, assignments)`. `vector_hash` = md5 of the matrix bytes. A cache hit skips the
PCA + k-means recompute. A same-scope/same-model **vector change** yields a hash mismatch →
recompute + overwrite (self-invalidating, so no stale layout and the entry count stays bounded); a
changed placed-work set yields a new key.

**Axes are fixed** (`EMBEDDING_AXES` = Component 1 / Component 2); `axis_options` is `None` — this is
not the swappable-axis view (that's `temporal_map`). **Node cap + sampling:** over `MAX_NODES`
papers are deterministically **sampled** evenly across the title order (`_sample_works`, not
truncated) with a `"Sampled {cap} of {total} … node cap {cap}"` note.

## 2. Frontend

- `frontend/src/lib/viz/embeddingCluster.ts` — the renderer. Reuses the P2 ECharts scatter shape
  (`node/x/y/size/color`): one scatter series per cluster so ECharts renders a **cluster legend**,
  per-point `symbolSize` from `node.size`, unplaceable points (null x/y) excluded, axes named from
  `payload.axes` ("Component 1/2"), hover tooltip = **title + cluster**, inside-datazoom. Registers
  itself on import. No axis dropdowns. Reuses the shared `theme.ts` (`colorForGroup`) — no hardcoded
  colors.
- `frontend/src/pages/VisualizationsPage.svelte` — side-effect `import '../lib/viz/embeddingCluster'`
  registers the renderer; the view-type selector already lists it dynamically
  (`registeredViewTypes()` / `listVizViewTypes()`). When `viewType === 'embedding_cluster'` the
  axis / color / citation-edge controls hide (replaced by a one-line hint); only the size dropdown +
  node cap apply. Click-to-open-paper is the existing shared handler (point `name` = work id).

## Tests added

- `backend/tests/test_visualization.py` (+11, `@pytest.mark.slow`): registry has embedding_cluster;
  `_pca_2d` deterministic + three separated groups project to distinguishable coords; PCA single-
  component padding; fixed PCA axes + `axis_options is None` + cluster legend; un-indexed papers
  skipped + "reindex" note (D19); **SEE-filter hides a private-shelf work from a reader**; baseline
  embedder fallback + note (default provider path); **cache reuse** (a `_pca_2d` that raises proves
  a hit is served without recompute); **cache invalidation** on a vector change (same key, coords
  differ); node-cap **sampling** note; endpoint lists + builds the view.
- `frontend/src/lib/viz/embeddingCluster.test.ts` (+5): registry lookup; Component 1/2 axis names;
  one series per cluster + unplaceable-point exclusion; tooltip = title + cluster; single-series
  fallback when no clusters reported.

## Verification

- FULL backend suite: `docker compose exec -T api python -m pytest backend/tests -q` → **808 passed**
  (+11; all green).
- `ruff check backend agent && ruff format --check backend agent` → clean (host).
- `make frontend-check` → `npm ci` + vitest **103 passed / 1 skipped** (+5) + build green (echarts
  stays a separate lazy chunk; main bundle ~360 kB).
- `backend/openapi.json` — **unchanged**: `view_type` is a free-form path param and the view-type
  list is a runtime `list[str]`, so a new registered view adds no schema/enum churn. Nothing to
  regenerate/commit.

## Deviations / notes

- **Baseline-embedder fallback added** beyond the literal "skip un-indexed" brief so the view renders
  with the *default* hash-BOW provider (the prompt's open question) — hash-BOW vectors are dense 256-d
  and PCA-usable. The real-model path still honors D19 (skip + reindex note), never re-embedding
  stored-vector models inline.
- **Clustering runs per view over the dense vectors** (reusing `topic_modeling._kmeans` + the TF-IDF
  labeller) rather than reading persisted `TopicAssignment` rows — persisted assignments only exist
  after a `model_topics` run and not for ad-hoc scopes (selected_papers / search_result), so
  in-request clustering is scope-agnostic and always available. The PCA + k-means result is cached.
- Did not touch `AUDIT.md` / `DISCUSSIONS.md` / `WORKPLAN_2026-07.md` / `VISUALIZATION_DESIGN.md` /
  `DECISIONS.md`.

## Next recommended task

**Track C P4 — §8.11 textual citation summaries** on the same computed layer (most-cited local /
external, frequently-cited-but-missing, bridge / isolated papers, chronological distribution),
reusing `ScopeResolver` + the `_LAYOUT_CACHE`/metric-cache pattern established here.

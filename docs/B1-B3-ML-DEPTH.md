# B1 / B3 — ML depth decision (deferred)

> **Status:** awaiting your decision. Nothing here is built beyond the stand-ins already shipped.
> This document is the detailed brief for two related choices:
>
> - **B3 = text-embedding depth** — `services/embeddings.py`; powers **semantic search** + **related papers**.
> - **B1 = topic-modeling depth** — `services/topic_modeling.py`; powers the **Insights topic clusters** + **per-paper topic tags**.
>
> They're coupled: a real embedding model (B3) is what unlocks the cheap "middle-ground" topic
> option (B1.1), so it's cleanest to decide them together. Reconstructed from the actual code on
> 2026-07-01 (the original scratchpad copy was lost). Everything below was verified against the
> source, not memory.
>
> **Update (2026-07-01):** the search/embedding side has since been designed in detail — see
> [HYBRID-SEARCH-DESIGN.md](./HYBRID-SEARCH-DESIGN.md). That design **supersedes the
> storage / model-switching notes in §1.1 and §4.1 below**: with a multi-column, per-model,
> dimension-*constrained* pgvector layout, keeping several models is cheap (tens of MB), switching
> the active model needs **no re-embedding** (you query a different column), and old vectors are kept
> intentionally (not deleted). The passages below are left for context — read them with that
> correction in mind.

---

## Part 0 — How to read this

Every AI feature here is **opt-in and degrades silently**: the default install pulls **no ML
libraries** and runs pure-Python, deterministic baselines. Real providers are coded and wired
behind the same interfaces but their libraries are **not installed**, so the baselines are what
runs today. The question in B1/B3 is *whether and how far to turn the real ones on*.

The rest of this doc is:

- **Part 1** — deep dive on embeddings (B3): data model, when work happens, exact inputs/outputs, a worked example.
- **Part 2** — deep dive on topics (B1): same treatment, plus a plain-English explanation of "embeddings → k-means".
- **Part 3** — the options for each, side by side.
- **Part 4** — the impact analysis you asked for: **storage/DB growth, performance & complexity, quality/accuracy, user experience, system requirements** — each as its own section with numbers.
- **Part 5** — scenario-based recommendations.
- **Part 6** — the exact questions I need answered to write the workplan.

---

## Part 1 — Embeddings (B3), in detail

### 1.1 What an "embedding" is here, and how it's stored

An embedding is a fixed-length list of floats (a *vector*) that represents a paper's text, so that
"similarity between two papers" becomes "cosine similarity between two vectors." Storage
(`models/ai.py`, table `embeddings`):

| Column | Meaning |
|---|---|
| `entity_type` / `entity_id` | always `"work"` + the work's UUID |
| `model_name` | e.g. `hash-bow-v1`, `st:sentence-transformers/all-MiniLM-L6-v2`, `ollama:nomic-embed-text` |
| `dim` | vector length (256 for hash-BOW, 384 for MiniLM, 768 for nomic) |
| `vector` | the floats, as a **JSON array** (portable across SQLite/Postgres) |
| `vector_pg` | *(Postgres only, optional)* the same vector in a pgvector column — see §1.5 |

There is a **unique constraint on `(entity_type, entity_id, model_name)`**: one vector per paper
per model. This is the single most important structural fact for the decision:

- **Vectors from different models are never compared.** Every query filters by `model_name` first.
- **Switching models means re-embedding the whole library** to produce vectors under the new
  `model_name` — the old query can't rank against new vectors and vice-versa.
- **Old-model rows are not auto-deleted** when you switch (the "ensure" path only *adds* missing
  ones). So repeatedly switching models accumulates stale rows until a cleanup — relevant to the
  storage math in §4.1.

### 1.2 When embeddings are built (the lifecycle) — this never happens during a search

Embeddings are built **off the read path**. Search only *reads* them. They're written at these
moments (`workers/jobs.py`, `services/semantic_search.py`, `endpoints/search.py`):

1. **On import** — after metadata enrichment settles a work's title/abstract, `enqueue_embedding`
   fires an `embed_work_job` RQ background job → `index_one_work` embeds that one paper.
2. **On demand** — `POST /search/reindex` (owner/editor) or the `reindex_embeddings_job` calls
   `ensure_work_embeddings`, which embeds every work that lacks a vector for the active model.
3. **Never inside `POST /search/semantic`** — a search embeds only the *query string* in memory and
   reads stored vectors; it performs **zero DB writes** (unless `auto_index=True`, used only in
   tests). This is why enabling a heavy model doesn't slow down individual searches — it shifts cost
   to background indexing.

The text that gets embedded is `canonical_title + " " + abstract` (`_work_text`). **Body text is
not embedded.** (Worth noting: this means semantic search matches on title+abstract only, on any
model.)

### 1.3 Semantic search — exact inputs and outputs

**Where the query comes from:** the free-text box on the search UI. Request (`endpoints/search.py`):

```jsonc
POST /api/v1/search/semantic
{ "q": "graph neural networks for molecular property prediction",
  "limit": 10,
  "mode": "embedding" }   // or "lexical"
```

**What happens (embedding mode):**
1. Resolve the active provider (config-driven; silent fallback to hash-BOW if the real one is unavailable).
2. `query_vector = provider.embed(payload.q)` — embed the query **in memory**, no storage.
3. Load all `embeddings` rows for that `model_name`, compute `cosine_similarity(query_vector, row.vector)` for each, sort desc.
4. Access-control: over-fetch (up to `limit*5`, capped 250), then trim to papers the caller may SEE, then cut to `limit`.

**Response:**

```jsonc
{ "query": "...", "mode": "embedding",
  "embedding_provider_used": "hash-bow-v1",          // what actually ran
  "embedding_provider_requested": "sentence_transformers",
  "degraded": true, "degraded_reason": "No module named 'sentence_transformers'",
  "items": [ { "work_id": "…", "title": "…", "year": 2021, "score": 0.83 }, … ] }
```

`score` is cosine similarity in `[0,1]` (higher = more similar). The `degraded*` fields are the
Phase-B2 honesty layer: if you *asked* for a real model but it wasn't installed, the UI can say
"requested X, using Y."

**How the output changes with a bigger model:** the *shape* is identical — same endpoint, same
fields, same `score` range. What changes is **which papers rank at the top and how sensible the
ranking is**. Concretely, for the query above:

- **hash-BOW / lexical (today):** ranks papers that literally contain the tokens "graph", "neural",
  "networks", "molecular", "property", "prediction". A paper titled *"GNNs for predicting compound
  bioactivity"* scores **near zero** — no shared tokens — even though it's the best match. Acronyms,
  synonyms, and paraphrases are invisible.
- **all-MiniLM / nomic (upgrade):** the vector encodes *meaning*, so "GNN" ≈ "graph neural network",
  "compound bioactivity" ≈ "molecular property", and that paper ranks near the top. This is the
  entire point of the upgrade: search stops being keyword-match and starts being concept-match.

### 1.4 Related papers — exact inputs and outputs

Same machinery, different query source (`related_works`, surfaced on the paper detail page):

- **Query vector = the *target paper's own stored embedding*** (or, if it has none yet, its
  title+abstract embedded on the fly).
- Rank all other works' vectors by cosine, drop the paper itself, return top-N as
  `[{work, score}]` → the "Related papers" list with a similarity score and (in the UI) a reason.
- With hash-BOW: "related" = "reuses the same words." With a real model: "related" = "about the same
  thing," which is what users expect when they click *Related*.

### 1.5 The pgvector path (already exists, but is *not* an ANN index)

There **is** an optional Postgres acceleration path (migration `0019_pgvector`, gated by
`pgvector_enabled`, default off):

- It adds an `embeddings.vector_pg` column and ranks with the pgvector `<=>` cosine operator **inside
  Postgres** instead of Python.
- **Crucially, the column is *unconstrained* (`vector`, no fixed dimension)** so it can hold any
  provider's output. That flexibility has a cost: **you cannot build an ivfflat/HNSW ANN index on an
  unconstrained pgvector column.** So even with `pgvector_enabled`, ranking is still an **exact,
  O(N) sequential scan** — just executed in C in the database rather than in Python. It's a
  constant-factor speedup, **not** approximate-nearest-neighbour.
- A *true* ANN index (sub-linear search for very large libraries) would require committing to **one
  fixed model + one fixed dimension** (a constrained column + schema change). That's the "B3.3"
  option and only matters at tens of thousands of papers.

---

## Part 2 — Topics (B1), in detail

### 2.1 Two different features under one module

- **Scope topic clusters** — `POST /ai/topics` (Insights page): "cluster this library / shelf / rack
  into N topics." This is real clustering (k-means).
- **Per-paper topic tags** — `topic_work_job` → `extract_paper_topics`: "give this one paper a few
  topic words." A single document can't be clustered, so this is just a **frequency term-ranker**
  over title + abstract + latest TEI body.

### 2.2 How scope clustering works today (TF-IDF + k-means)

1. Gather the scope's works; build each document's text from **title + abstract only** (`_doc_text`
   — body text is not used here).
2. **TF-IDF vectorize**: each doc becomes a *sparse* map `{term: weight}` where weight = term
   frequency × inverse-document-frequency. Distinctive words get high weight; common words get low.
3. **Deterministic k-means** (`k = min(max_topics, #docs)`, seeded from the title-sorted order, 15
   iterations, cosine distance): assign each doc to the nearest of k centroids, recompute centroids,
   repeat.
4. **Label** each cluster by the top TF-IDF terms of its centroid → the keyword chips you see.
5. Persist `TopicAssignment` rows `(topic_model_id, work_id, topic_id, score)`.

**The honesty caveat (already surfaced in the UI):** selecting the `embedding` or `bertopic` backend
today runs the **same TF-IDF + k-means** internals — it just adds representative-work IDs, a
coherence score, optional outliers, and a minimal hierarchy to the response. **BERTopic / UMAP /
HDBSCAN are not installed.** So all three current backends cluster *lexically*.

Response shape (`POST /ai/topics`):

```jsonc
{ "model_id": "keyword-kmeans:library:all", "backend": "tfidf", "work_count": 128,
  "topics": [ { "topic_id": 0, "keywords": ["reinforcement","reward","policy","agent","control","exploration"],
                "work_count": 22, "representative_work_ids": [...], "coherence_score": 0.41 }, … ],
  "outlier_work_ids": [], "hierarchy": null }
```

### 2.3 What "embeddings → k-means" (option B1.1) actually means — and why it's a saving

Right now k-means clusters the **sparse TF-IDF term vectors** from step 2. "Embeddings → k-means"
means: **replace the TF-IDF vectors with the dense semantic embedding vectors** — the *same vectors
already produced by B3 and stored in the `embeddings` table* — and cluster *those* instead. Keyword
labels are still generated from TF-IDF terms (you can't read human-readable words off a 384-float
centroid), so only the *clustering input* changes, not the labels.

**Why it "reuses the model you're already downloading":** if you pick a real embedding model for B3,
every paper *already has* a dense semantic vector sitting in the DB (built by the import/reindex
jobs). B1.1 just reads those vectors and runs the existing k-means over them. So:

- **No new dependency, no new model, no new download** beyond what B3 already installed.
- **Almost no new compute** — the expensive part (embedding each paper) is already done and cached;
  clustering a few hundred dense vectors is milliseconds.
- **Real quality gain:** clusters group papers by *meaning* rather than shared vocabulary. Two RL
  papers that use totally different wording land in the same cluster — which lexical TF-IDF k-means
  can't do.

**Is it really better / a saving?** Yes, *conditional on B3 being upgraded*. It's the highest
quality-per-effort option because it piggybacks entirely on the B3 investment. If B3 stays on
hash-BOW, then B1.1 has nothing meaningful to reuse (hash-BOW is lexical too), and the honest choices
collapse to "keep TF-IDF k-means" vs "install the full BERTopic stack." **This coupling is the whole
reason to decide B1 and B3 together.**

Full **BERTopic** (B1.2) is a different tier: it brings UMAP dimensionality reduction + HDBSCAN
density clustering + c-TF-IDF labelling. It auto-discovers the topic count, handles true outliers,
and produces the best clusters — but it needs a real corpus (dozens+ papers) to shine and the
heaviest dependency stack of any option here.

---

## Part 3 — The options, side by side

### B3 — embedding depth

| Option | What runs | Semantic quality | New dependency |
|---|---|---|---|
| **B3.0 Keep hash-BOW** | lexical bag-of-words, dim 256 | keyword overlap only | none |
| **B3.1 sentence-transformers** (`all-MiniLM-L6-v2`, dim 384) | real embeddings, **in-process** | good general semantic retrieval | PyTorch stack in the app image |
| **B3.2 Ollama** (`nomic-embed-text`, dim 768) | real embeddings via **local daemon** | good/better, longer context | a running Ollama container (no Python dep) |
| **B3.3 pgvector ANN** (on top of 3.1/3.2) | sub-linear DB search | (ranking speed only, not quality) | fixed model+dim commitment + schema change |

### B1 — topic depth

| Option | What runs | Cluster quality | New dependency |
|---|---|---|---|
| **B1.0 Keep TF-IDF + k-means** | lexical clustering | groups by shared distinctive words | none |
| **B1.1 embeddings → k-means** | dense semantic vectors + existing k-means | groups by **meaning**; reuses B3 | **none beyond B3** |
| **B1.2 full BERTopic** | UMAP + HDBSCAN + c-TF-IDF | best; auto topic count + outliers | sentence-transformers **+ umap-learn + hdbscan** |

---

## Part 4 — Impact analysis (all axes)

### 4.1 Storage & database growth

**Per-vector size.** Vectors are stored as a JSON array of floats:

| Model | dim | ~JSON bytes/paper | pgvector bytes/paper (if enabled) |
|---|---|---|---|
| hash-BOW | 256 | ~2.5–3.5 KB | — (default off) |
| all-MiniLM | 384 | ~6–7 KB | ~1.5 KB (4 B × 384) |
| nomic-embed | 768 | ~12–14 KB | ~3 KB |

**Library-scale totals** (one model, one vector per paper):

| Papers | hash-BOW | all-MiniLM | nomic |
|---|---|---|---|
| 1,000 | ~3 MB | ~7 MB | ~13 MB |
| 10,000 | ~30 MB | ~70 MB | ~130 MB |
| 100,000 | ~300 MB | ~700 MB | ~1.3 GB |

**Takeaways:**
- For a realistic personal/lab library (hundreds to low thousands of papers), embedding storage is
  **negligible** on any model — single-digit MB.
- Growth is **linear in (papers × dimension)**, plus a small overhead per stored model.
- **Watch item:** switching models leaves the old model's vectors behind (they're not auto-pruned),
  so N model switches ≈ N× the single-model footprint until cleaned. A model switch should be paired
  with a "delete old-model embeddings" step in the workplan.
- The topic feature stores small `TopicAssignment` rows (one per work per run) — trivially small and
  unchanged by any B1 option.

### 4.2 Performance — computational complexity & latency

**Two very different cost centres:** *indexing* (background, one-time-ish) and *query* (interactive).

**Indexing** (per paper, in the RQ worker — never blocks a user request):

| Model | Cost per paper | 1,000 papers | Where it runs |
|---|---|---|---|
| hash-BOW | microseconds | < 1 s | in-process |
| all-MiniLM (CPU) | ~10–50 ms | ~10–50 s | in-process (PyTorch) |
| all-MiniLM (GPU) | ~1–5 ms | ~1–5 s | needs CUDA + VRAM |
| Ollama nomic (CPU) | ~20–100 ms (incl. IPC) | ~20–100 s | Ollama daemon |

Indexing happens on import (one paper at a time, invisibly) and in bulk on a reindex / model switch.
The **only** time a user feels it is the **initial reindex after enabling or switching a model** —
minutes for a large library — during which search results are partial until the job finishes.

**Query latency** (interactive `POST /search/semantic`):

- Cost = *embed the query once* + *rank N stored vectors*.
- Embed-query: hash-BOW ≈ microseconds; MiniLM ≈ 10–50 ms CPU (model stays warm after first use);
  Ollama ≈ one network round-trip (~20–100 ms).
- Ranking is **O(N × dim)** cosine over all N stored vectors (pure Python today):

| Library size | Python cosine (dim ~384) | With `pgvector_enabled` (exact scan in C) |
|---|---|---|
| 1,000 | a few ms | sub-ms |
| 10,000 | ~30–60 ms | few ms |
| 100,000 | ~0.3–0.6 s | tens of ms |

- **Complexity note:** ranking is **linear** in library size on *every* current path — including
  pgvector, because the column is unconstrained and can't carry an ANN index (§1.5). Only **B3.3**
  (fixed dim + ivfflat/HNSW) makes it sub-linear, and that only pays off in the 10k–100k+ range.
- **Net:** for a personal library, query latency stays comfortably interactive on any option; the
  larger model adds a fixed ~10–50 ms for query inference, not a scaling problem.

**Topics compute:** k-means is `O(iterations × k × #docs × features)`. Switching from sparse TF-IDF
features to dense embeddings (B1.1) is *cheaper per iteration* (dense 384-dim vs sparse
term-dictionaries) and the vectors are already computed — so B1.1 is **not** more expensive than
today. BERTopic (B1.2) adds UMAP+HDBSCAN, which is heavier (seconds on a few hundred docs) and needs
enough docs to converge well.

### 4.3 Quality & accuracy

This is the axis where the upgrade actually earns its keep.

**Embeddings / search & related:**
- **hash-BOW / lexical (today):** high precision on exact keyword matches, **poor recall on
  meaning**. Misses synonyms ("car"/"automobile"), acronyms ("GNN"/"graph neural network"),
  paraphrase, and cross-phrasing. Fine as a keyword search; disappointing as "semantic" search.
- **all-MiniLM (B3.1):** strong general-purpose retrieval quality — the standard "it found the right
  paper even though I used different words" behaviour. 384-dim is a good quality/size balance.
- **nomic-embed (B3.2):** comparable or slightly better for retrieval, 768-dim, larger input context
  (helpful if we ever embed more than title+abstract).
- All real models are **deterministic given fixed weights**; results are stable across runs.

**Topics:**
- **TF-IDF k-means (today):** clusters are coherent *by vocabulary*; papers on one concept using
  different words scatter across clusters. Labels are real, readable terms (a genuine strength).
- **embeddings → k-means (B1.1):** clusters cohere *by meaning*; noticeably tighter, more intuitive
  groupings. Labels still come from TF-IDF terms, so readability is retained.
- **BERTopic (B1.2):** best cluster quality, automatically picks the number of topics, isolates
  genuine outliers instead of forcing every paper into a cluster, and supports hierarchy — at the
  cost of determinism, corpus-size sensitivity, and the heaviest deps.

**Accuracy caveat that applies to *every* option:** search and topics currently see **title +
abstract only** (not full body). A better embedding model improves matching *within that text*, but
if you want the model to reason over full papers, that's a separate scope change (embed body /
chunking) — worth flagging but out of scope for B1/B3 as posed.

### 4.4 User experience

**If you upgrade (B3.1/B3.2, and B1.1):**
- Search and Related-papers get dramatically more useful — the headline win. Users can search by
  concept, not keyword.
- Topic clusters look more sensible on the Insights page.
- **First-run friction:** enabling or switching a model triggers a full reindex. For a large library
  that's minutes of background work during which results are mixed/partial. Worth a UI progress
  indicator (the reindex status endpoint already exists).
- **Ongoing ops (B3.2 Ollama only):** a daemon must be running; if it's down, search silently
  degrades to hash-BOW and the UI shows "requested Ollama, using hash-BOW" (already built). That's
  graceful but means quality can quietly drop if the daemon dies.
- No change to any API shape or frontend contract — purely better rankings.

**If you keep the baselines (B3.0/B1.0):**
- Zero ops burden, zero image growth, fully deterministic and offline.
- But "semantic search" remains essentially fuzzy keyword search, which may under-deliver against
  user expectations set by tools like Semantic Scholar.

### 4.5 System requirements

| Option | Image size impact | Model download | RAM (runtime) | GPU / VRAM | Extra ops |
|---|---|---|---|---|---|
| **B3.0 hash-BOW / B1.0 TF-IDF** | none | none | negligible | none | none |
| **B3.1 sentence-transformers** | **+~1–2 GB** (PyTorch + transformers in the app image) | ~90 MB (all-MiniLM, one-time, cached) | ~0.5–1.5 GB while indexing/serving | **none required**; GPU only speeds bulk indexing (~1 GB VRAM if used) | none (in-process) |
| **B3.2 Ollama** | **none to the app image** (separate container) | ~274 MB (nomic); LLM summary models ~2 GB+ each | in the Ollama container | optional; GPU strongly helps LLM summaries, not needed for embeddings | run/maintain the Ollama daemon (`make up-ai`) |
| **B3.3 pgvector ANN** | none | none | none | none | Postgres schema change (fixed dim) + reindex |
| **B1.1 embeddings → k-means** | **none beyond B3** | none beyond B3 | none beyond B3 | none | none |
| **B1.2 full BERTopic** | **+~2 GB** (sentence-transformers + umap-learn + hdbscan; compile-heavy) | model as B3.1 | ~1–2 GB | none required | none |

**Key clarifications on "system requirements":**
- **No GPU is needed** for a personal library on any option. GPU/VRAM only helps *bulk indexing
  throughput* or *LLM summary speed* — irrelevant at single-researcher scale.
- The real cost of **B3.1** is **image bloat (~1–2 GB)**, not compute. If image size matters more
  than avoiding a second container, **B3.2 (Ollama)** sidesteps the bloat entirely and throws in LLM
  summaries — at the price of running a daemon.
- **Everything stays local** on every option: sentence-transformers has no egress after the one-time
  weight download; Ollama is a local daemon. No paper text leaves the machine.

### 4.6 Install / activation mechanism (constraint)

Per the standing constraint, there is **no runtime web-UI pip installer**. The pattern (same as OCR
in B5) is **activate-when-present**: `model_management.detect_providers` already probes whether
`sentence_transformers` / `bertopic` are importable and whether the Ollama daemon is reachable, and
the AI & Models tab reports status + how to enable. So the mechanism per option is:

- **sentence-transformers / BERTopic:** ship an optional AI image build (`pip install` extra); the
  backend auto-detects and the admin toggles it on. Model *weights* can be pulled at runtime
  (`pull_model_job`); the *library* comes from the image, not a runtime install.
- **Ollama:** fully drivable from the UI today — start the profile (`make up-ai`), then
  detect/list/pull/delete models from the panel (no Python dependency in the app image).

---

## Part 5 — Recommendations (by scenario)

- **"Keep it lean and offline, image size matters, search-as-keyword is acceptable"** → **B3.0 +
  B1.0** (status quo). Honest, zero-cost, zero-ops. Accept that semantic search is really fuzzy
  keyword search.
- **"I want genuinely semantic search/related/topics, one self-contained image, no extra daemon"** →
  **B3.1 (sentence-transformers) + B1.1 (embeddings → k-means)**. Accept ~1–2 GB image growth and a
  one-time reindex. This is the best quality-per-effort combination for most single-node deployments.
- **"I also want LLM summaries and I'd rather not bloat the app image"** → **B3.2 (Ollama) + B1.1**.
  Accept running the Ollama container. Embeddings + LLM summaries share the daemon.
- **"Best-possible topic clusters and I have a large, diverse library"** → add **B1.2 (BERTopic)** on
  top of a real B3. Accept the heaviest dependency stack.
- **pgvector ANN (B3.3):** **defer** until the library is in the tens of thousands of papers. Below
  that, the existing Python/exact-scan path is fast enough and avoids locking to a fixed model/dim.

My default suggestion for this project: **B3.1 + B1.1**, with **B3.2** as the alternative if you want
LLM summaries or want to keep the app image small, and **B3.3 / B1.2 deferred**.

---

## Part 6 — What I need from you to write the workplan

1. **B3 embeddings:** hash-BOW (keep) / sentence-transformers (in-process, +image) / Ollama (daemon,
   +LLM summaries)? And pgvector ANN now or defer?
2. **B1 topics:** keep TF-IDF k-means / embeddings → k-means (needs a real B3) / full BERTopic?
3. **Reindex UX:** on enable/switch, is a background reindex with a progress indicator acceptable
   (results partial until it finishes), and should switching a model auto-prune the old model's
   vectors?
4. **Confirm the activation mechanism:** activate-when-present + documented `make` target + admin
   toggle (no runtime web-UI pip installer). Confirm that's what you want.

Answer these and I'll turn it into a concrete, testable workplan and implement it.

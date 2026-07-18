# Handoff: AI "Recommend categorization" (Part B) — COMPLETE

Plan: `docs/WORKPLAN_2026-07-18_rows-and-ai-recommend.md` Part B (decisions C5–C11). Depends on the
Rows layer (Part A). For a paper scope, an LLM (or embedding fallback) recommends TAGS or CATEGORIES
(rows/racks/shelves) per paper from its features (title/abstract/keywords/topics); reviewed + accepted
in a new Insights sub-tab.

## Landed (committed, tested)

- `e246e78` — `models/recommendation.py` `RecommendationRun` + migration `0079` (parity green);
  `services/recommendation.py` (core). `test_recommendation.py` (scoring math + fallback, fake ranker).
- `db00064` — `recommend_job` (`workers/jobs.py`) + `enqueue_recommend`/`RECOMMEND_JOB`
  (`workers/queue.py`); `api/v1/endpoints/recommend.py` mounted at `/recommend`. `test_recommend_api.py`.
- `a395c90` — `components/RecommendPanel.svelte` + Insights sub-tab bar (`pages/InsightsPage.svelte`);
  client `Recommend*` types + `createRecommendation`/`getRecommendation`.
- `df1e409` — e2e Journey 41 + reference docs.

## Design notes (as built)

- **Rankers are injectable** (`Ranker` protocol) so tests use a deterministic fake. Real: `OllamaRanker`
  (POST `/api/generate` `format:"json"`, parsed with a prose-tolerant `_extract_json`) when
  `ai_config.summary_provider == "local_llm"`; else `EmbeddingRanker` (cosine of paper vs
  name+description vectors, **no affinity**, `fallback=True`).
- **Scoring is pure** (`rank_points`, `combine`, `combine_categorization`): base = `K−p+1` (or the
  model's 0–100 affinity when `scoring="affinity"` and returned; else rank points + `fallback` flag),
  then `PARENT_FACTOR=0.5` × combine(picked parents) propagated row→rack→shelf. `parent_combine` ∈
  sum|median|max. Final ranking = shelves by combined score.
- **Caching** (`recommendation_runs`): find-or-create per (scope_type, scope-key, mode, params_hash,
  model, **creator**) with status running/done; `recompute` forces a new run. Scope-key = library
  sentinel / container id / hash of an explicit work-id set. The capped, resolved work-ids live in
  `run.params` so `recommend_job` needs no scope re-resolution.
- **Flow**: `POST /recommend` → run row (running) + enqueue; the frontend polls `GET /recommend/{id}`
  on `status` (no getJobs needed). Requester-gated read. Accept reuses `POST /shelves/{id}/works` /
  `POST /tags/{id}/links`.
- **Cap** default 100 (≤500 hard). **Prefilter** optional (embedding shortlist to keep prompts small).
- **Access**: candidates come from `visible_*_query` (a private shelf/row a viewer can't see is never
  suggested). Endpoint floor: contributor+ (accept actions separately gated).

## Verification

- Backend: `test_recommendation.py` (6) + `test_recommend_api.py` (3) green; migration parity green;
  ruff clean; `tsc` clean; affected frontend unit tests green; RecommendPanel + InsightsPage compile
  (dev-server transform). **Live end-to-end**: shelf-scope categorization run → worker → 5 papers
  ranked by combined shelf score, `fallback=True` (embedding:hash_bow — no LLM configured), raw LLM
  I/O captured. Full fast-tier regression: run after these commits (see PROGRESS/next).
- e2e Journey 41 written; NOT run here (Playwright needs the `.vite`-clear + restart dance — run via
  the `make` e2e target).

## Not done / follow-ups

- With a real generative model configured (Ollama `local_llm`), do a visual pass of affinity mode +
  the two popups + accept flows. Only the embedding fallback path was live-exercised (no LLM set).
- The "hover a shelf shows its racks/rows" ask is partially met (hover shows the shelf **description**
  from the catalog; the contributing racks/rows are visible in the per-kind "Scores" popup). Wiring
  the shelf→rack→row chain into the hover tooltip is a possible enhancement.
- Optional: expose the cap as an admin `app_config` value (currently request param, default 100).

## Security

- Untrusted paper text enters the LLM prompt (prompt-injection surface; blast radius = a misleading,
  human-reviewed suggestion — nothing is auto-applied). Results are requester-scoped + gated.

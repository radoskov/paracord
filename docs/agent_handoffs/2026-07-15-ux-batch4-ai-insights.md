# Handoff — 2026-07-15: UX batch 4 (scope-summary overhaul, topics UX, insights graphs)

## What shipped

| Commit | Area |
| --- | --- |
| `0c32ea6` | **Scope summaries → map-reduce** (`local-llm-v2-map-reduce`): per-paper digests via `summarize_work` (persisted → visible in the paper view, reused next run; a single LLM outage circuit-breaks further attempts), digests packed into `LLM_INPUT_CHAR_BUDGET` (11k chars) chunks, per-chunk condensation, collection-framed final synthesis (overview + problems/methods/datasets/findings; never "this paper"). `params` records `scope_label`/`method`/`chunks`. Topics: `work_ids` on every topic + `GET /ai/topics/latest` reconstructing stored job results (keywords recomputed deterministically from `TopicAssignment` members). |
| `e0a4497` | Insights UI: Summarize/Model-topics buttons busy for the entire run (incl. background jobs) then auto-load results — whole-library topics/summary no longer end empty; "Max topics" control (default 8); per-topic "Show papers" lists (titles, click → paper view); `onOpenWork` finally passed to the graph (node clicks did nothing before); topic-graph size (similarity links / citation count) + color (year) selects; `citation_count` on topic-graph nodes; ⓘ Help popup per graph type. |
| `b607d35`, `133c992` | ruff SIM fixes + `make fix` format deltas. |
| `33ec89c` | Future acceptance contracts graduated: 3 dropped as already enforced (covering tests cited in the file docstring); cross-scope topic stability implemented for real. |
| `66d2f78` | Web-find fetch realism: Mozilla-compatible UA (PaRacORD still identified), browser Accept headers, `Referer` on page-extracted links — publisher CDNs (Springer confirmed by the owner) 403 bare bot UAs even on entitled campus IPs. `manual_upload_needed` reason now lists the tried URLs. Springer "Download PDF" button markup pinned as a regression test. |

## Key context

- The owner's Springer failure was NOT link discovery (both the layer-1 rewrite and the anchor
  heuristics produce the right URL — see the regression test) but the fetch being refused for
  bot-looking headers. If it still fails after the header change, capture the response status —
  the tried-URLs reason now makes that visible. The PDF.js viewer "Save" button the owner found is
  browser chrome (blob save), not a scrapeable link.
- Topic modeling = TF-IDF (title+abstract) + deterministic cosine k-means, labels = top centroid
  terms; embedding backend clusters dense vectors, labels still TF-IDF. Topic-graph edges =
  embedding cosine kNN (k=6, min 0.30) — documented in the new Help popup.
- `pollJob` in InsightsPage polls the Jobs list 150×2s; busy flags are released in its `.finally`.
- No migrations this batch. Live stack: API hot-reloads; worker restart only needed if
  `workers/jobs.py` changes (not this batch — `summarize_scope_job` calls the reworked service).
  Actually: the WORKER runs `summarize_scope`/`model_topics` for large scopes — restart the worker
  so background jobs use the new code.
- Validation: `make ready-full` green (exit 0); e2e run at the end of the session (see PROGRESS).

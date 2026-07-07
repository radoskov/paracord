# Handoff — issue_batch_6 "all clear" items (2026-07-07)

**Task:** triage `issue_batch_6.md` into all-clear vs needs-discussion (workplan:
`docs/WORKPLAN_2026-07-07_batch6.md`) and implement the all-clear items. Six implemented; seven
deferred to discussion (features / product calls / an owner question). `issue_batch_6.md` and the
workplan are intentionally **not committed** (working docs).

## Files changed (per commit)
- `081852c` backend: `backend/app/services/semantic_search.py` (`reindex_status`) +
  `backend/tests/test_semantic_search.py` — issue 2a.
- `4e0d977` viz: `backend/app/services/visualization.py` + `backend/tests/test_visualization.py` —
  issue 1e.
- `e6f970d` viz: `frontend/src/lib/viz/registry.ts`, `frontend/src/lib/viz/temporalMap.ts`,
  `frontend/src/lib/viz/temporalMap.test.ts` — issues 1g + 1b.
- `4ccef25` agent: `agent/paperracks_agent/agent_ops.py` (`prune_selected`),
  `agent/paperracks_agent/web.py` (bulk handler + button + toast), `agent/tests/test_agent_ops.py` —
  issue 3.
- `d24aae7` frontend: `frontend/src/pages/LibraryPage.svelte` +
  `frontend/src/pages/LibraryPage.refresh.test.ts` — issue 7.

## What each fix does
- **2a** `reindex_status` counted raw `Embedding` rows for the model (incl. rows for deleted/merged/
  text-less works) vs a `total` of current works → impossible "7 / 3". Now both aggregates run over
  the same population (works with text, `merged_into_id IS NULL`) and `indexed` counts distinct
  works joined to an embedding, so `indexed <= total`.
- **1e** Reworded the similarity / topic-similarity axis-unavailable notes to plain, actionable
  "paper" language (pick a focus paper / run topic modeling / reindex) instead of exposing the raw
  axis key.
- **1g** `registeredViewTypes()` was alphabetical (co_citation first). Added an `order` hint to
  `VizRenderer`; temporal_map = 0 so it leads and is the page default.
- **1b** The temporal-map year axis rendered fractional, thousands-separated ticks (2,019 / 2,019.2).
  A year axis now uses `minInterval: 1` + an integer formatter → 2019, 2020, …
- **3** Agent bulk **Prune** now prunes only the *unwatched* rows among a selection (watched files
  kept — Forget/unwatch first), via new `agent_ops.prune_selected`; toast reports "kept N watched".
  **Forget** still removes all selected rows.
- **7** Library toolbar gained a **Refresh** button that re-fetches the current view + counts (keeps
  page/filters) so agent-pushed papers appear without a full browser reload.

## Assumptions
- 2a: merged shadows should not count as "papers" here (consistent with Batch D hiding them
  everywhere); excluded from both aggregates.
- 1e: the axis note is displayed verbatim to users (confirmed — `VisualizationsPage` renders
  `payload.notes` as list items) and nothing keys off the old string.
- 3: "skipped" in the bulk-prune response = selected rows that were not unwatched (watched/missing);
  surfaced to the user as "kept N watched".

## Tests added
- `test_reindex_status_counts_current_papers_not_stale_rows` (backend).
- Reworded assertion in `test_similarity_axes_unavailable_without_focus` (backend).
- Year-axis + ordering assertions in `temporalMap.test.ts` (frontend).
- `test_prune_selected_prunes_only_unwatched_leaves_watched` (agent).
- `LibraryPage.refresh.test.ts` (frontend).

## Security implications
None. No auth/scope/file-access boundaries touched. Agent prune stays local (no server contact);
`reindex_status` is admin-only and read-only.

## Deferred to discussion (see workplan §B)
1a viz help content/UX; 1c "needs reindex" vs "no PDF → attach&extract" messaging; 1d citation edges
on by default; 1f overlapping-point jitter/hover-list; 2b lexical-index staleness ("1 of 3");
4 "Scan & push" index-only server-entry semantics (owner question) + agent help tab; 5 per-paper
weighted reference citation graph (needs a data-availability check: do we store the *section* of
each citation mention?); 6 per-paper stored AI summary.

## Verification
Bare `pytest` (CI mirror, incl. `@safety`) + `make frontend-check` + `make e2e` — run before any
push. Nothing pushed; all commits local on `main`. Next recommended: get owner answers on §B
(especially 4, 5, 6) and turn them into the next workplan.

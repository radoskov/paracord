# Handoff: scope/Insights summary history + "set as current" (#22)

Owner report (2026-07-19): per-work summaries have history (per-model / reasoning LRU cache + a
History popup + "Set as current"), but the collective **scope/Insights summary**
(library/shelf/rack/row) has no such feature. Add it, mirroring the per-work pattern.

## What was already there vs. missing

Scope summaries already stored **one row per (scope, effort, model)** in the `Summary` table
(`entity_type` = scope_type, `entity_id` = scope_id, LRU-capped at 5 models per effort via
`_evict_stale_models`) — so the *history material* existed. What was missing:

- No list endpoint (the frontend only ever fetched the single "latest" cell).
- No promote / "set as current" (`promoted_at` was written by per-work only).
- Reasoning and normal runs of the **same** model+effort **collided** on one cell (scope didn't tag
  reasoning, unlike per-work), so they couldn't coexist as history.
- The response schema lacked `id` + `promoted_at`, so the UI had nothing to promote.

## Changes

### Backend — `services/summarization.py`
- `summarize_scope`: now tags reasoning runs `"<model> (reasoning)"` in the stored `model_name`
  (mirrors the per-work view) so reasoning and normal versions coexist as distinct history entries;
  the real model name is still what's sent to Ollama / used for per-paper digests (`call_model`).
- `latest_scope_summary`: orders by `COALESCE(promoted_at, created_at) DESC` so a promoted version
  wins (was `created_at` only).
- New `list_scope_summaries(db, *, scope_type, scope_id)` — all versions, current-first.
- New `promote_scope_summary(db, summary_id, *, scope_type, scope_id)` — stamps `promoted_at = now`,
  guarded by entity_type/entity_id. Both mirror `list_work_summaries` / `promote_work_summary`.

### Backend — `api/v1/endpoints/ai.py`
- `ScopeSummaryResponse` + `_scope_summary_response`: added `id` and `promoted_at`.
- `GET /ai/summaries/history?scope_type&scope_id` → list, current-first (access-guarded).
- `POST /ai/summaries/{summary_id}/promote` (body `{scope_type, scope_id}`) → set-as-current.
- **`GET /ai/summaries/latest` now returns the CURRENT version for an effort** = promoted-or-newest
  across any model/reasoning variant (was: exact (effort, configured-model) cell + fallback). This
  is what makes a promotion take effect in the reactive view. The non-force `create` cache
  short-circuit was aligned to the same effort-level lookup for consistency. (The Insights button
  always sends `force: true`, so this doesn't change the explicit-generate path.)

### Frontend — `api/client.ts`
- `ScopeSummaryResponse` gained `id?` + `promoted_at?`.
- `listScopeSummaries(scopeType, scopeId)` → `GET .../history`.
- `promoteScopeSummary(summaryId, scopeType, scopeId)` → `POST .../{id}/promote`.

### Frontend — `pages/InsightsPage.svelte`
- Loads `scopeHistory` alongside the cached summary and after each generate/poll.
- A **History (N)** button in the summary toolbar opens a popup listing the other versions
  (effort · provenance · "was current" · date) each with **Set as current**; promoting reloads and
  re-fetches the current summary. Mirrors `WorkDetail.svelte`.

## Tests
- `test_summarization.py::test_promote_scope_summary_makes_a_version_current` — service level.
- `test_summarization.py::test_scope_summary_history_and_promote_api` — endpoint level.
- Full `test_summarization.py` + `test_ai_admin.py`: **92 passed**. `ruff` clean.

## Notes / not done
- **No migration**: reuses the existing `Summary.promoted_at` column (added migration 0084 last
  session). Live DB already has it.
- Frontend typecheck (svelte-check) was **not** run in-container — `npm run` in the container breaks
  the live Vite dev server (see memory). Changes mirror the working WorkDetail history pattern and
  existing InsightsPage idioms; verify with `make ready-full` when convenient.
- No e2e journey added yet for scope history (per-work history has none either). Candidate follow-up.
- Not pushed — standing rule: ask before every push.

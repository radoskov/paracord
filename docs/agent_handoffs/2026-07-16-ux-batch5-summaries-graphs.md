# Handoff — UX batch 5 (summaries / jobs / graphs), 2026-07-16

Workplan: `docs/WORKPLAN_2026-07-16_summary-effort-tags-graphs.md` (all 12 design Qs answered inline).
This batch is **partially complete** — 5 of the planned groups shipped and are tested; 3 remain.

## Done (committed, tested, gate-green)

| Area | Commits | Notes |
|---|---|---|
| Jobs visibility/progress/stopping | `a4c64a9` | scope summaries always enqueue; detailed paper summary reports per-section progress + honors Stop; "stopping"→"stopped" |
| No-PDF honesty + footer | `11f9982` | title-only refused (400), abstract-only framed, scope groups them, footer breakdown + timestamp |
| Effort levels + cache matrix | `4db779b` `2164df7` + migration `0074` | fast/section/deep; `(entity, summary_type, model)` dedup + LRU(5); scope cache + Regenerate; radios + history popup |
| Format/openapi deltas | `0fb6cb9` `a45107f` | ruff-format + regenerated `backend/openapi.json` |
| Even-distribution external limit | `85dbc2f` | `_distribute_external_keep` (absolute A + relative R_i, largest-remainder) |
| Ref-graph polish + Build state | `4802c90` | 3 typed edge colours, title in header, scrollable tooltip + ctrl/middle-click append, build state |
| Insights pan/zoom freeze | `095fb3b` | restyle = merge setOption (no sim restart / roam reset) |

Migration **0074** (renames legacy `*_detailed` → `*_detailed_deep`) was applied to the live DB
(`docker compose exec api alembic -c backend/alembic.ini upgrade head`). Worker restart is needed for
the new job code to run on the live stack (not done in this session — do before testing detailed jobs).

## Remaining (not started)

1. **LaTeX plain/fancy rendering (§1.4).** DECIDED: prompt the LLM to delimit maths in `$…$`/`$$…$$`,
   bundle **offline KaTeX** (CSP-safe — inline, no CDN), render fancy by default with a plain
   fallback toggle, plus a light heuristic that only wraps *egregious* non-delimited cases (`\frac`,
   braced `^{}`/`_{}`, backslash commands) and leaves easy tokens (`N_i`) alone. Touch the summary
   prompts in `summarization.py` and the render spots in `WorkDetail.svelte` / `InsightsPage.svelte`.
   Adding KaTeX needs `make frontend-install` (npm in-container) + a package-lock update.

2. **Insights citing papers + external styling (§4.5–4.6).** DECIDED: include citing papers from the
   *already-fetched* citation data only (flag coverage so an empty citing half reads as "not
   fetched"); add a distinct edge type/colour for citing vs reference. Externals should participate in
   degree + citation-count + colour (backend must populate `citation_count` on external nodes in
   `citation_graph.py`), **keeping the diamond shape**; pagerank/betweenness stay local-only.

3. **Per-shelf/rack tags (§3).** DECIDED: new `tag_shelves`/`tag_racks` join tables (zero rows =
   global), a `GET /works/{id}/assignable-tags` union resolver (global OR shelf-scoped OR
   rack-scoped via any-shelf-in-rack), scope-management endpoints on `tags.py`, a scope multi-select
   in `TagsPage.svelte`, and filtering the add-tag dropdown in `WorkDetail.svelte`. The Library-view
   scope-aware tag filter is an explicit lower-priority follow-up. Needs an Alembic migration.

## Gotchas confirmed this session
- Backend tests run in-container: `docker compose run --rm --no-deps api python -m pytest …`. The
  `test_citation_graph.py` module is `@slow` (drop `-m "not slow"` to run it).
- Host vitest/svelte-check can't write the root-owned `node_modules/.vite`; run frontend checks via
  `make frontend-check` or `docker compose run --rm --no-deps frontend npx vitest run <file>`
  (an ephemeral container, so it does NOT disturb the live dev server's .vite).
- The unit suite runs Redis-less; the autouse `_scope_summary_runs_inline` fixture pins scope
  summaries to the inline fallback so 202-vs-201 isn't environment-dependent.
- `make ready-full` aborts at `openapi-check` if the schema drifted — run `make openapi` after any
  API model change and commit `backend/openapi.json`.

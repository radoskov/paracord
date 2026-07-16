# Handoff — UX batch 5 (summaries / jobs / graphs), 2026-07-16

Workplan: `docs/WORKPLAN_2026-07-16_summary-effort-tags-graphs.md` (all 12 design Qs answered inline).
This batch is **complete** — all planned groups shipped and tested. The post-batch follow-ups are
also done: the **Library-view scope-aware tag filter**, **per-paper Notes** (`Work.notes`), and
**per-scope Insights Notes** (`ScopeNote`, migration 0076). Nothing outstanding.

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
| LaTeX plain/fancy rendering | (batch 5) | `lib/renderMath.ts` + bundled KaTeX + heuristic; prompt-delimiting; fancy/plain toggle in WorkDetail + Insights; `katex` dependency added |

Migration **0074** (renames legacy `*_detailed` → `*_detailed_deep`) was applied to the live DB
(`docker compose exec api alembic -c backend/alembic.ini upgrade head`). Worker restart is needed for
the new job code to run on the live stack (not done in this session — do before testing detailed jobs).

## Remaining (not started)

1. **Library-view scope-aware tag filter (§3 Q7, lower-priority).** The backend already returns the
   right set via `GET /tags?shelf_id=&rack_id=` (globals + scoped). Wire `LibraryPage.svelte`'s tag
   filter dropdown to call `client.listTags({shelfId, rackId})` when `shelfFilter`/`rackFilter` are
   narrowed (they're at LibraryPage.svelte ~72-74), so the tag options match the chosen shelf/rack.
   Everything else in the tag feature (Tag-tab scope editor + filter, WorkDetail assignable dropdown)
   already shipped.

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

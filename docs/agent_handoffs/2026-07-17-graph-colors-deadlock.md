# Handoff: graph color-by shelf/rack/tag (color-wheel nodes) + event-loop deadlock fix

## Files changed

- `backend/app/services/graph_color.py` (new) — `membership_groups(db, work_ids, kind)`: ALL
  privacy-filtered membership names per paper (non-private shelves/racks only; racks resolved
  via the paper's shelves; both shelf AND rack must be non-private for a rack name to surface).
  Every work id maps to at least `[unshelved|unracked|untagged]`.
- `backend/app/services/citation_graph.py` — `ColorBy` gains `rack`; nodes carry `color_groups`
  (full list; `color_group` stays the first for back-compat); shelf/tag/rack resolve through the
  shared helper (the old first-membership-only queries are gone).
- `backend/app/services/reference_graph.py` — base + local nodes carry
  `memberships: {shelf|rack|tag: [names]}` (client-side coloring like kind/venue).
- `backend/app/services/topic_graph.py` — nodes carry the same `memberships` map.
- `backend/app/services/visualization.py` — `_membership_map()`; temporal map + co-citation
  nodes carry `color_groups`; legends union ALL memberships.
- Endpoint read models (`graph.py`, `visualization.py`) expose the new fields — NOTE: these
  pydantic models silently DROP unknown fields, which ate the first attempt; if you add payload
  fields to a service, check the endpoint model too. `openapi.json` regenerated.
- `frontend/src/lib/graphPie.ts` (new) — `pieSymbol(colors)`: SVG pie (equal segments,
  clockwise from 12 o'clock) as an ECharts `image://` data-URI symbol.
- `frontend/src/components/CitationGraph.svelte` — citation graph gains Color: rack; topic graph
  gains shelf/rack/tag (client-side, memberships always in the payload); multi-group nodes get
  pie symbols; legend chips hide a node only when ALL its groups are hidden; distinct-group and
  hover-index bookkeeping iterate all groups; tooltips list every membership.
- `frontend/src/lib/viz/referenceGraph.ts` + `ReferenceGraphModal.svelte` — Shelf/Rack/Tag
  color-by: one scatter series per name (node plots once, in its first group's series),
  multi-membership markers as per-datum pie symbols, "external / no data" series for the rest.
- `frontend/src/lib/viz/temporalMap.ts` / `coCitation.ts` / `VisualizationsPage.svelte` —
  Shelf/Rack/Tag options; membership kinds REBUILD server-side (meta carries no membership
  data), and `restyleTemporalMap` preserves server-computed groups on a size-only restyle.
- `backend/app/main.py` + `backend/app/db/session.py` — the deadlock fix (see below).

## The deadlock (separate, pre-existing bug)

Symptom: the whole API wedged mid-e2e (even `/health`; container "unhealthy"; ~0% CPU).
py-spy on the uvicorn child: MainThread (event loop) parked in SQLAlchemy POOL CHECKOUT inside
`_rate_limit` → `rate_limit.check` → `effective_rate_limit_per_client_per_min` → `db.get` —
the middleware's cache-expiry path does sync DB I/O ON the loop. Under a 16-worker burst the
pool (5+10) was momentarily empty → the loop blocked forever → all in-flight threadpool
requests froze mid-response holding their 15 connections (Postgres: 15 idle-in-transaction,
all frozen the same instant) → the pool could never refill. Fixes: `run_in_threadpool` for the
check + explicit `pool_timeout=30` and `max_overflow=20` on the engine (exhaustion now raises,
surfaced descriptively by the error envelope). Recovery for a wedged instance:
`docker compose restart api`.

## Assumptions made

- Multi-membership nodes belong to their FIRST group's legend series/category (they plot once);
  the pie makes the other memberships visible, chips/tooltips make them actionable. ECharts has
  no per-node multi-category concept.
- Topic-graph membership coloring is client-side (memberships always attached — cheap), so
  switching it needs no rebuild; the citation graph keeps its server-side color_by contract.

## Tests added or skipped

- +2 backend (all-membership groups incl. rack privacy chain; helper defaults) — the trimmed
  `test_citation_graph.py` fixture gained Rack/RackShelf tables. Frontend: existing suites pass
  (321+); pie rendering verified visually (two-segment wheels in the live Insights graph,
  screenshot in the session). Full battery green: backend 1264, frontend 324, safety 161,
  migrations 4, `E2E_ONLINE=1` e2e 37/37 0-flake (after the deadlock fix; the first attempt
  wedged the API and was how the deadlock was found).

## Security implications

- Membership names respect the existing privacy rule in ONE place (`graph_color`): private
  shelves/racks never surface as colors, and a rack name requires both the shelf and the rack
  to be non-private. Tag names were already unrestricted.
- The deadlock fix removes a trivially-triggerable full-API denial condition (any request burst
  coinciding with the rate-limit cache expiry could wedge the service).

## Next recommended task

- The embedding-cluster viz keeps its k-means cluster coloring (color_by is ignored there);
  extending membership colors to it would follow the same `_membership_map` pattern.

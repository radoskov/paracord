# Handoff: access-aware graph coloring — owner's private racks colored "unracked"

## Task

Graph/visualization node coloring by **rack** rendered almost everything as "unracked" (shelf
coloring worked). Rack membership is inferred from a paper's shelves (a paper is never directly on a
rack). Reported on a deployment with two real racks and papers on shelves inside them.

## Investigation (how the real cause was found)

- Proved the inference logic is correct: on the live DB, adding a `rack_shelves` link (rolled back)
  immediately colored the works by that rack, end to end through `build_citation_graph`,
  `reference_graph`, and `membership_groups`. Open/visible racks color correctly in every graph.
- The live DB happened to have **zero** real rack links (all 216 racks were archived E2E artifacts),
  so the symptom wasn't reproducible there — but the owner confirmed their rack link exists and it
  still showed "unracked". Since open/visible racks provably work, the only remaining cause was the
  **access-level filter** dropping a `private` rack (or a private shelf on the path). Reproduced:
  `access_level="private"` rack → `membership_groups(..., "rack")` returns `["unracked"]`.

## Root cause

`graph_color.membership_groups` filtered both shelf and rack to `NON_PRIVATE_LEVELS` (`open`/
`visible`) **unconditionally, ignoring the viewer**. The existing test even asserted a private rack
never surfaces. But the owner/admin (and, in a mostly single-user deployment, the only user) should
see their own private racks — `access.is_admin_or_owner` bypasses all content ACLs everywhere else.
So an owner's private rack (or a private shelf providing the only path to an open rack) was dropped →
"unracked", while shelf coloring still worked via the paper's open shelf.

## Files changed

- `backend/app/services/graph_color.py` — `membership_groups(db, work_ids, color_by, actor=None)`.
  New `_visibility_condition(db, model, target_type, actor)` mirrors `access.visible_racks_query`/
  `visible_shelves_query`: admin/owner → no filter (sees private); other actor → non-private OR
  granted; `actor=None` → conservative non-private-only (no private-name leak to an unknown viewer).
  Applied to the shelf and rack branches (tag has no access level — unchanged).
- `backend/app/services/citation_graph.py` — `actor` param on `build_citation_graph`,
  `build_citation_neighborhood`, `_attach_node_metrics`, `_color_groups`, threaded to
  `membership_groups`. `User` imported at module scope (file has no `from __future__ import
  annotations`, so the annotation is evaluated at runtime).
- `backend/app/services/reference_graph.py` — `actor` param on `build_reference_graph`, passed to the
  membership loop.
- `backend/app/services/topic_graph.py` — `actor` param on `build_topic_graph` + `_attach_memberships`.
- `backend/app/services/visualization.py` — `actor` param on `_membership_map`; both call sites
  (`temporal_map`, `co_citation`) pass their `actor` (already in scope via `get_viz`).
- `backend/app/api/v1/endpoints/graph.py` — citation + topic endpoints pass `actor=actor`.
- `backend/app/api/v1/endpoints/works.py` — reference-graph + neighborhood endpoints pass `actor`.
- `backend/app/workers/jobs.py` — RQ analysis job (citation + topic) passes its loaded `actor`.
- `docs/reference/03_backend_services.md` — new `graph_color.py` row documenting the shared,
  access-aware resolver and the rack-from-shelves inference.

## Assumptions made

- `actor=None` keeps the old conservative behavior on purpose: if any un-migrated caller colors by a
  membership kind without an actor, it drops private names rather than leaking them (safe fallback).
  Verified every real membership-coloring call site now passes an actor.
- Rack surfaces only if the viewer may see BOTH the shelf on the path and the rack (generalizes the
  previous "both must be non-private" rule to per-viewer visibility).
- The reported deployment's account is owner/admin (both accounts on the live DB are) — the fix makes
  their private racks color. A librarian who created a private rack but holds no grant still won't see
  it (correct per the access model; separate from this bug).

## Tests added

- `test_citation_graph.py::test_color_by_rack_owner_sees_private_rack` — direct `membership_groups`
  (owner vs reader vs None) + full `build_citation_graph` for owner/reader.
- `test_reference_graph.py::test_reference_graph_rack_membership_respects_viewer` — HTTP endpoint.
- `test_topic_graph.py::test_topic_graph_rack_membership_respects_viewer` — HTTP endpoint (fake dense
  embedding provider).
- `test_visualization.py::test_temporal_map_color_by_rack_respects_viewer` — viz provider.
- Green: test_citation_graph, test_reference_graph, test_topic_graph, test_visualization,
  test_access_control, test_citation_summary, test_external_citations, test_saved_filters,
  test_reference_resolution_repair (173 total). Ruff clean. mypy not installed in the api container.

## Security implications

- Tightens correctness without loosening privacy: private shelf/rack names still never surface to a
  viewer who can't see them (non-owner without grant, or `actor=None`). RQ job results are
  requester-gated and built with the requester's `actor`, so no cross-user leak via cached graphs.

## Next recommended task

- Optional data hygiene (offered, not done): purge the 216 archived `E2E …` racks polluting the
  Racks page. Also consider surfacing a rack's access level in the Racks UI so an owner understands
  why a private rack behaves differently elsewhere.

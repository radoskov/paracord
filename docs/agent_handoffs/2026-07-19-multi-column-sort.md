# Handoff: multi-column sort + DOI/shelves/racks/rows sort columns

Owner request (2026-07-19): add multi-column sorting to the library table; add sorting by DOI
(lexical), shelves, racks, and rows; and add a **Rows** column (it was missing entirely, even though
the row data was already returned per work).

## Backend (`c1f064c`)

`list_works` (`backend/app/api/v1/endpoints/works.py`):
- **Multi-column sort spec.** `sort` is now a comma-separated list where each entry is `key` or
  `key:asc|desc`; priority = list order. `_parse_sort_specs(sort, order)` parses it (entries without
  their own direction fall back to the `order` param). A bare legacy `sort=title`+`order=asc` is a
  one-element list, so old callers are unaffected. The ORDER BY is built as a list and applied with
  `Work.id` as the final tiebreaker. Defensive: a non-str `sort` (the unresolved `Query` marker from
  direct, non-HTTP test calls) → no specs → default column.
- **New sort keys.** `doi` (Work.doi, lexical, NULLs last). `shelves`/`racks`/`rows` are correlated
  `min(name)` scalar subqueries over the actor's SEE-filtered containers
  (`_visible_container_name_sort`, built per request because visibility is actor-dependent) —
  work→shelf[→rack[→row]], mirroring `_batch_shelf_rack_refs` so the sort key matches the displayed
  names. NULLs (no visible container) sort last.
- Each subquery sort is added as a **uniquely-labelled** select column (`sort_value_{i}`) — the
  Postgres "DISTINCT ORDER BY must be in the select list" rule, now handling several at once.

Preferences schema (`preferences.py`): `LibraryColumnPrefs.sort` accepts `list[LibraryColumnSort] |
LibraryColumnSort | None` (multi-column list, still tolerates a legacy single object).

Tests: `backend/tests/test_library_sort_extended.py` (DOI incl. NULLs-last; shelves min-name incl.
alphabetically-first-of-many; racks+rows chain; multi-column priority; shared-order fallback +
unknown-key skip) + a multi-sort prefs round-trip in `test_library_sort_and_preferences.py`. Full
library-sort suite (30 incl. the m1 direct-call tests) green; ruff clean.

## Frontend (`753538e`)

- **columns.ts:** added the `rows` ColumnId + registry entry (opt-in); gave `doi`/`shelves`/`racks`/
  `rows` a `sortKey`. `ColumnPrefs.sort` is now `ColumnSort[]` (prefs **version 2**);
  `normalizeColumnPrefs` reads both the v2 array and the v1 single-object shape, dedupes keys, drops
  unknown/invalid, and guarantees ≥1 entry.
- **client.ts:** `WorkSortKey` gained the 4 keys; new `WorkSortSpec` + `WorkQuery.sorts?`; `listWorks`
  encodes `sorts` as one `key:order,…` param (falls back to single `sort`/`order` when absent).
- **PaperTable.svelte:** props `sortKey`/`sortOrder` → `sorts: ColumnSort[]`; `onSort(key, additive)`;
  header shows the direction arrow + a 1-based priority number when >1 column is sorted; new `rows`
  render branch.
- **LibraryPage.svelte:** `handleSort(key, additive)` — plain click = single sort (toggles direction
  if already sole), **Shift-click = add/flip a lower-priority level**. `loadWorks` sends
  `sorts: columnPrefs.sort`. `sortWorksClientSide` (semantic-result path) is now a multi-key compare
  incl. the new keys (min shelf/rack/row name).

Tests: `columns.test.ts` (multi-sort list normalize + dedupe, new sort keys, rows column),
`PaperTable.test.ts` (renders shelves/racks/rows, priority badge), `client.ext.test.ts` (multi-sort
param). 28 directly-relevant vitest tests pass.

## Verification gaps / notes

- **Frontend katex-importing suites** (LibraryPage.*, WorkDetail.*, App, Insights…) could NOT be run
  here: `katex`/`echarts`/`pdfjs-dist` aren't in the host `node_modules` (container-only), and running
  vitest in the container would disrupt the live Vite dev server (memory: container npm breaks it).
  The core logic is covered by the 28 passing unit tests; **run `make ready-full` to exercise the
  LibraryPage integration + svelte-check** (type check not run for the Svelte edits — manually
  reviewed, no dangling `sortKey`/`sortOrder`).
- UX: Shift-click to build a multi-sort is discoverable via the header tooltip; consider a small
  hint in the Columns dialog if users miss it.
- Not pushed — standing rule: ask before every push.

# Handoff — Batch L: library / insights UX (2026-07-07)

Four owner-facing UX fixes from `docs/WORKPLAN_2026-07-06.md` Batch L. Committed on `main` (not
pushed), one commit per item.

## L1 — Exclude the default/Inbox shelf from Put-into menus

Moving a paper *into* the Inbox (the fallback home for loose papers, created by migration 0037)
makes no sense. The default shelf has no marker on the `Shelf` row itself — its id lives on the
`access_settings` singleton (`default_shelf_id`, resolved by `default_shelf.get_default_shelf_id`).
So I surfaced a robust flag rather than matching the name string:

- `ShelfRead` gained `is_default: bool` (defaulted; populated in `list_shelves` by comparing each
  shelf id to `get_default_shelf_id(db)`). `openapi.json` regenerated.
- Frontend `Shelf` type gained `is_default?`; `ShelfPicker` gained an `excludeDefault` prop that
  drops the default shelf from its options. Applied to the single **Put into…** (`WorkDetail`) and
  batch **Put all into…** (`LibraryPage`) menus only — import/other pickers still show Inbox.

## L2 — Remove the redundant Insights-tab search

Search has its own tab now, so the bottom-of-Insights search box was redundant. Removed the whole
`Search` card, its state/handlers (`semanticSearch`, `semanticQuery/Results/Degraded`, `searchMode`,
the `warmSearch` prefetch), the now-unused `HybridSearchItem`/`SearchMode` imports, the dead styles
(`.modes/.mode/.results/.badge*/.passage/.degraded-hint`), and the obsolete
`InsightsPage.semantic.test.ts`. The tab hint in `App.svelte` no longer says "semantic search". The
rest of the Insights tab (Scope, citation graph, topics, scope summary, export) is untouched.

## L3 — Jump-to-open button in the library list

A small **Jump to open** button in the library toolbar scrolls the currently-open paper's row into
view and briefly flashes it. `PaperTable` rows now carry `data-work-id` and a `flash` class (a
1.5 s pulse, disabled under `prefers-reduced-motion`); `LibraryPage` queries the row inside the
list-scroll container and calls `scrollIntoView({block:'center'})`, then sets `flashWorkId` for
~1.6 s. **Off-page case:** if the open paper isn't in the current result set (opened from another
tab, or filtered/paginated out), the button shows a message — "The open paper isn't on this page —
clear filters or change page to find it." — rather than silently doing nothing (simplest correct
behaviour; no auto page-hunt).

## L4 — Scope summary uses the configured AI model + indicates the fallback

The Insights scope summary was hardcoded extractive. The endpoint (`POST /ai/summaries`) now leaves
`summary_type` unset by default and resolves it from the admin AI config the same way per-work
summaries do: `local_llm` when `ai_cfg.summary_provider == "local_llm"`, else `extractive`. The
existing `summarize_scope` local_llm path (with its `provider_used`/`fallback`/`fallback_reason`
provenance) does the rest. The frontend `ScopeSummaryResponse` type gained those provenance fields,
and the Insights UI shows a hint whenever `provider_used !== 'local_llm'`:

- no model configured → "Extractive summary — set an AI summary model in Admin → AI to enable
  model-based summaries."
- model configured but unavailable (fallback with a reason) → "Extractive summary — the configured
  AI model was unavailable (<reason>)."

## Files touched

- Backend: `endpoints/shelves.py` (L1), `endpoints/ai.py` (L4), `openapi.json` (L1+L4),
  `tests/test_default_shelf.py` (L1), `tests/test_summarization.py` (L4).
- Frontend: `api/client.ts` (Shelf.is_default, ScopeSummaryResponse provenance),
  `components/ShelfPicker.svelte` + `.test.ts` (L1), `components/WorkDetail.svelte` (L1),
  `components/PaperTable.svelte` (L3), `pages/LibraryPage.svelte` + `LibraryPage.jump.test.ts`
  (L1+L3), `pages/InsightsPage.svelte` + `InsightsPage.summary.test.ts` (L2+L4), `App.svelte` (L2),
  deleted `InsightsPage.semantic.test.ts` (L2).

## Verification

- Full backend suite (docker): **881 passed**. New backend tests: shelf list flags `is_default`
  (L1); scope summary uses the configured provider when set + honestly reports extractive otherwise
  (L4, `_ollama_summarize` monkeypatched).
- `ruff check` + `ruff format --check` on `backend agent`: clean. `openapi-check`: current.
- `make frontend-check` (vitest + build): **176 passed / 1 skipped**, build clean. New vitest:
  Inbox excluded from the put-menu (L1), Insights search gone (L2), jump scrolls + flashes and the
  off-page message (L3), scope-summary extractive/fallback/model-based indication (L4).
- Screenshots (Playwright, admin/paperracks, 1440×900 @2x, **not committed**) in
  `/home/zednik/paracord-theme-shots/`: `putmenu_no_inbox.png` (Put-into list expanded, no Inbox —
  also asserted programmatically: 44 options, `contains Inbox: false`), `insights_no_search.png`
  (Insights ends at Topics/Scope summary; 0 "Search" headings). Jump-to-open is behavioural,
  covered by vitest.

## Notes / deviations

- The default shelf genuinely has no per-row marker; the reliable identifier is
  `access_settings.default_shelf_id`. Surfacing it as `is_default` on the read schema (no migration
  needed) is the robust filter the workplan asked for.
- Commits are per item. Shared files (`client.ts`, `openapi.json`, `LibraryPage.svelte`,
  `InsightsPage.svelte`) were hunk-split so each item's commit is self-contained.

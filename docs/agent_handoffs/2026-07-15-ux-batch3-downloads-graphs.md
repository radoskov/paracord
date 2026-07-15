# Handoff — 2026-07-15: UX batch 3 (zen reader, graph UX, download overhaul, Elsevier API)

Workplan: `docs/WORKPLAN_2026-07-15_ux-batch3.md` (includes the superseded auto-fetch proposal and
the owner's clarifications). PROGRESS.md carries the commit-by-commit log for the batch and its
follow-up rounds.

## What shipped

| Area | Commits | Summary |
| --- | --- | --- |
| Reader zen mode | `754985e`, `6780d1d` | Viewport takeover on a dark backdrop; only paging/zoom/view-mode/reading-mode + Exit zen (Esc). **Portals to `<body>`** while active — the paper-view modal's panel is a CSS containing block, so plain `position:fixed` was clipped (first attempt's bug). Comment anchor restores the DOM spot on exit. |
| Reference graph | `e36dce6`, `62f5c04`, `72515eb`, `6780d1d` | Max-external base 500, persisted per user (`reference_graph_max_external` in the prefs blob). Resolved refs display the WORK's canonical title/year (graph + references panel overlay). Edge-snapped wheel zoom (cursor near a plot edge pins the zoom window to that data edge). Ctrl-click focus on nodes and legend entries. Show all / Reset view / Refresh. |
| Insights citation/topic graph | `3fdf38d`, `6780d1d` | Button trio; encoded-channel tooltip lines; ctrl-click focus (nodes + chips). "Show all" on force layouts merges `{series:[{zoom:1, center:null}]}` — never repaints (repaint restarts the simulation and nodes spring out of view); `scheduleForceFit()` auto-fits ~1.6 s after genuine rebuilds. |
| Visualizations | `72515eb`, `c1e5baf` | Manual X/Y min/max inputs (broken) replaced by two-handle slider dataZoom bars (end stops = auto) on temporal map + embedding cluster; Show all / Reset view; ctrl-click focus on co-citation + temporal map via payload-level node/edge filtering in `renderChart` (works through the pure renderers untouched). |
| Badges column | `b5ba0ed` | `likely_refs` ("refs to review") + `citers_in_library` ("cited locally") tokens; the m1 trimmed SQLite fixture needed the external-citation tables (`6802a94`). |
| Insights export | `e36dce6` | Folded `<details>` card at the bottom. |
| Download overhaul | `5eb79a8`, `0e139c3` | Manual triggers + host policy unchanged; the ATTEMPT got smarter. `services/pdf_link_finder.py` (pure): publisher URL rewrites → `citation_pdf_url`/alternate metas → scored "Download PDF" anchors → embedded-JSON sniffing (ScienceDirect `pdfft?md5&pid` reconstruction). Wired into `download_and_attach` as bounded fallbacks (≤5 URLs, ONE landing-page read, no recursion); every URL re-gated (deny/SSRF/policy); refused hosts skipped, never auto-confirmed. Fallbacks only run on the real network path (`streamer is None or html_fetcher is not None`) so injected-streamer tests keep single-attempt semantics. |
| Elsevier API | `0e139c3`, `60cfd6c` | For `10.1016/` DOIs, tries the Article Retrieval API (`api.elsevier.com`, in DEFAULT_ALLOWED_HOSTS) with `X-ELS-APIKey`. Key sources: AppConfig (write-only via admin PATCH; GET exposes only `elsevier_api_key_set`) else yaml `web_find.elsevier_api_key` / env `PARACORD_ELSEVIER_API_KEY`. **Three gates** (`usable_elsevier_api_key`): key ∧ `elsevier_api_enabled` master switch (NULL→True) ∧ `users.elsevier_api_allowed` per-user flag (NULL→False; `POST /admin/users/{id}/elsevier-api`, button in Admin → Users). |

Migrations this batch: **0072** (`app_config.elsevier_api_key`), **0073** (`elsevier_api_enabled`,
`users.elsevier_api_allowed`). Live DB is at 0073 (applied via
`docker compose exec api alembic -c backend/alembic.ini upgrade head` — the API hot-reloads code
but never re-runs its migrate-on-start entrypoint).

## Gotchas for the next agent

- **The owner must still create the Elsevier key** (free, dev.elsevier.com) AND flip the per-user
  toggle on their own account — the per-user default is OFF, so the API route is dormant until
  then. Entitlement follows the campus IP range.
- ScienceDirect anti-bot can still defeat the JSON-sniffed `pdfft` URL; the API route is the
  reliable path. JS-only pages elsewhere still end `manual_upload_needed`; a Playwright sidecar
  (optional compose profile) remains the documented future step.
- Zen-mode Esc swallows the event (`stopImmediatePropagation`) so the modal beneath doesn't close
  too — listener-order dependent; if it misbehaves, wire an explicit flag instead.
- `pdf_link_finder` returns candidate URLs only — callers MUST keep policy-gating each one.
- Reference-graph legend ctrl-click reads node ids from the clicked series' plotted data, so it
  works for kind AND venue coloring without knowing series names.
- Docs updated: reference 02/03/07, viz About texts, reference-graph Help popup.

## Open / deferred

- Headless-browser fetcher (Playwright compose profile) — only if the current layers prove
  insufficient in practice.
- Co-citation/temporal focus relies on payload edges; on the temporal map with the edge overlay
  suppressed by the node cap, ctrl-click focuses the lone point (documented in the About text).
- Double-click a slider handle to reset just that limit — not supported by the native ECharts
  slider; Reset view covers it.

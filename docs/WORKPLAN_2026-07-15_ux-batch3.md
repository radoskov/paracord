# Workplan — 2026-07-15, UX batch 3

Owner request: reader zen mode; reference-graph 500 limit (persisted); refs/citations must reflect
imported papers' metadata; overhaul/automate file download (PROPOSE first); graph UX improvements
(standard buttons, hover encodings, ctrl-click neighborhood focus, edge-snapped zoom); citation/ref
badges in the Library badges column; move Insights "Export this library" out of the way.

## Items

1. **Zen mode for reading** — reader fills (nearly) the whole viewport over a dark backdrop; only
   the scroll/size controls, the reading-mode (normal/dim/dark) control and an exit-zen button
   remain. Combinable with any reading mode. Frontend-only (PdfReader).
2. **Reference graph limit** — base/default citation limit raised to 500; the user's chosen limit
   persists (localStorage) across sessions. Check the backend cap allows 500.
3. **Ref/citation metadata after import** — a reference/citing entry resolved to a library work
   should display the WORK's metadata (year/title) when its own extracted metadata is missing —
   today an unknown-year reference stays unknown forever even after the paper is imported.
   Approach: surface resolved-work fields in the read models / graph nodes (display-side, no
   provenance-polluting backfill of Reference rows).
4. **Download automation** — PROPOSAL ONLY this round (see "Proposal: automatic PDF retrieval"
   below + discussion points). No code changes until the owner picks a direction.
5. **Graph UX** (shared helpers where possible):
   - Standard buttons on every graph: **Show all** (fit-to-content), **Reset view** (fit +
     clear every filter incl. legend solo/ctrl-click focus), **Refresh** (recompute data, then
     reset).
   - Tooltip: append the encoded channels, e.g. `size = <metric>: <value>` / `color = <group>`.
   - **Ctrl-click** a legend chip → show that category + direct neighbors only; ctrl-click a
     node → show that node + direct neighbors only. Ctrl-click again / Reset view to clear.
   - Edge-snapped cursor zoom — investigate feasibility in ECharts without bloating rendering;
     may land as a discussion point if it requires reimplementing roam.
6. **Badges column** — add per-paper reference/citation badges (e.g. "likely matches to review",
   "refs in library") to the Library badges column; requires cheap aggregate fields on the works
   list read model.
7. **Insights export widgets** — move to the right column / bottom of the tab.

## Sequencing

7 → 2 → 3 → 6 → 1 → 5 (buttons → tooltips → ctrl-click → snap-zoom last/optional) → 4 (proposal
text only). Each lands as its own commit with tests where the repo has precedent.

## Item 4 — RESOLVED after owner clarification (2026-07-15, same day)

The owner does NOT want auto-triggered fetch jobs — triggers stay manual (Find-on-web download,
imports) and the host policy stays. The pain is that an attempt only succeeded on a direct PDF
URL; an HTML landing page dead-ended. Implemented (`5eb79a8`): `services/pdf_link_finder.py` +
`download_and_attach` fallbacks — (1) deterministic publisher PDF-URL rewrites (ACM, Springer,
Wiley, IEEE, Nature, MDPI, arXiv, ACL, OpenReview, bioRxiv/medRxiv, PLOS), (2) one policy-gated
read of the landing page for `citation_pdf_url` / `<link rel=alternate type=application/pdf>`,
(3) scored "Download PDF" anchor heuristics (href/.pdf, class/id hints like `xpl-btn-pdf`
`action-downloadPdf`, link text; supplements/samples penalized). Bounded: ≤5 fallback URLs, one
page fetch, no recursion; every URL passes the same denylist/SSRF/policy gates; refused hosts are
skipped, never auto-confirmed. Known limitation: pages that only render the download affordance
via JavaScript (notably ScienceDirect) still fail → `manual_upload_needed`; a headless-browser
fetcher (optional compose profile) remains a possible future step — see discussion point 4b.

The original staged auto-fetch proposal below is kept for reference only.

## (superseded) Proposal: automatic PDF retrieval (item 4 — awaiting owner decision)

Current state (verified): PDF download is strictly user-initiated (WorkDetail → Find on web →
pick → download). Enrichment *sees* open-access PDF URLs (OpenAlex `best_oa_location.pdf_url`,
Unpaywall `url_for_pdf`, S2 `openAccessPdf`) but **discards them** — `ExternalMetadata` has no
OA-URL field. `web_find.download_and_attach` already validates hosts (denylist + policy modes),
streams with `%PDF` magic + size checks, attaches, dedups and enqueues extraction — it is fully
reusable from a background job.

Staged plan:

1. **Capture OA links during enrichment** (cheap, no downloads): add `oa_pdf_url` to
   `ExternalMetadata` + the OpenAlex/Unpaywall/S2 parsers; persist on the Work (nullable column).
   UI affordance: "open-access PDF available".
2. **Auto-fetch job**: new `fetch_pdf_job(work_id)` (RQ, coalescing key `pdffetch-{work_id}`):
   runs when a work has NO file; tries the stored OA URL first, else `find_candidates` and takes
   the top candidate only when its score clears a threshold (e.g. 0.9); downloads via
   `download_and_attach` under the configured download policy with a system actor.
   `needs_confirmation` (unknown host in unrestricted mode) is **never auto-confirmed** — skipped
   with a per-paper note. Failure marks the paper (like extraction) and respects an attempt cap.
3. **Triggers (opt-in)**: admin toggle "auto-fetch PDFs after import" wired into
   enrichment-completed (identifier/BibTeX/batch/citation imports all funnel through enrichment),
   plus a Library batch action "Fetch PDFs" for existing papers.

Guardrails: OA/allowlisted hosts only under `restricted`/`careful`; existing size caps; per-sweep
download cap; jobs visible in the Jobs tab.

### Discussion points (owner input needed)

1. Default for "auto-fetch after import": ON (recommended for identifier/DOI imports under the
   `careful` policy — it only ever touches OA/known-publisher hosts) or OFF?
2. Store the OA URL as a `Work` column (simple, recommended) or a MetadataAssertion (provenance)?
3. One-time backfill sweep over the existing library, or new imports only?
4. Auto mode never auto-confirms unknown hosts — acceptable, or do you want a "pending downloads"
   review queue for those?
5. Graph edge-snapped zoom (item 5d): requires replacing ECharts' built-in roam with a custom
   wheel handler that clamps the view center to the content bounding box. Feasible but adds
   bespoke zoom code per chart family — implement for the reference graph only, all graphs, or
   drop it?
6. Visualizations page: the same Show all / Reset view / Refresh trio can be added, but the
   temporal map's manual X/Y range inputs overlap with "Show all" (blank = auto is today's
   reset). Unify or leave as is?

## Status (2026-07-15, end of session)

Landed: items 1, 2, 3, 6, 7 fully; item 5 partially — standard buttons + tooltip encodings on the
citation/topic graph and the reference graph, ctrl-click neighborhood focus (nodes + chips) on
the citation/topic graph. Deferred: edge-snap zoom, ctrl-click on the reference-graph scatter and
Visualizations (see discussion points), item 4 (proposal above).

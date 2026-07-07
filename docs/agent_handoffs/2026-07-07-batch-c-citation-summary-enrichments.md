# Handoff — Batch C: citation-summary enrichments (2026-07-07)

Five enrichments to the Citation-summary tab (`docs/WORKPLAN_2026-07-06.md` Batch C). Committed on
`main` (not pushed), one commit per item (shared files bundled where a per-hunk split wasn't clean).

## C2 — Open-in-paper-view icon for internal items

Each internal (in-library) item in the ranked blocks (most-cited-local, most-cited-external, bridge,
isolated) gets a small icon button that opens the paper directly in the in-app paper view — the same
flow the Search tab uses: `client.getWork(id)` → a `WorkDetail` inside a `Modal`. The title click
still jumps to the Library tab (`pendingLibraryOpen`); the icon is the new, distinct action.

## C1 — External-citation preview

`GET /api/v1/citations/external-preview?doi=…|arxiv=…|reference_id=…` returns a compact preview
(`available`, title, authors, year, venue, abstract, doi, arxiv_id, sources, message). New service
`app/services/external_preview.py` composes the existing identifier-based enrichment connectors
(arXiv/Crossref/OpenAlex/Semantic Scholar), querying in priority order and merging (first non-empty
field wins), stopping early once title+abstract+authors are filled to keep egress polite.

- **Egress policy:** identical to enrichment — only the identifier leaves the server (percent-encoded
  into the API path via the SSRF-hardened `_get`). No identifier ⇒ no network I/O, returns "no
  preview available"; each source is guarded so one flaky API never aborts the preview.
- **Caching:** in-process TTL cache (`PREVIEW_TTL_SECONDS = 600`) keyed by `doi|arxiv`; a remembered
  miss is cached too. Repeated opens of the same reference don't re-hit upstream.
- **`reference_id`:** the reference's citing work must be visible to the actor (404 otherwise); the
  reference's DOI/arXiv id is then used.
- **Frontend:** a `Preview`/`Hide preview` toggle next to the missing item's `Import`; the panel
  shows title/authors/year·venue/abstract/sources, or a graceful "no preview available" message.

## C3a — Import/ignore worklist for cited-but-missing works

**Persistence:** new table `missing_work_decisions` (migration `0052`), **per-user**, keyed by the
**stable normalized missing-work key** (`doi:…`/`arxiv:…`/`title:…`) from `citation_summary`, so a
decision survives a summary recompute / re-extraction (it is not tied to a reference id). Model
`app/models/citation_worklist.py`; service `app/services/citation_worklist.py`
(`list_decisions`/`set_decision`/`clear_decision`). Endpoints: `GET/PUT/DELETE
/api/v1/citations/worklist`.

**UX:** each missing item offers **Queue** (records `import` — a "queued" acquisition marker, stays
visible with a badge) and **Ignore** (records `ignore` — item moves to an `Ignored (n)` collapsible
`<details>` with a **Restore**). Decisions load with the summary (`getWorklist`) so they persist
across visits; the actual **Import** button (unchanged) still creates the work immediately.

## C3b — Export the missing-but-cited list

`GET /api/v1/citations/missing-export?scope_type=…&format=bibtex|csv` computes the summary for the
same scope family (with a large limit to include the full missing set) and renders
`summary.frequently_cited_missing`. New renderers in `export_service.py`
(`render_missing_works`, `MISSING_EXPORT_FORMATS`):

- **BibTeX** — `@misc{firstwordYEAR, title, year, doi, eprint+archivePrefix (arXiv), note="Cited by N
  paper(s)…"}`, deduped keys.
- **CSV** — `key,title,authors,year,doi,arxiv,cited_by_count,mention_count` (authors are blank —
  aggregated references carry none). Frontend BibTeX/CSV buttons trigger a Blob download.

The entries are aggregated reference strings, so only what's known is emitted (no author data). This
is separate from the existing `missing_references` export scope, which renders raw per-citing-work
unresolved-reference *strings* rather than the identifier-aggregated summary list.

## C3c — Library-coverage metric

`CitationSummary` gained `coverage_held` / `coverage_total` / `coverage_pct`, computed in the same
resolution pass as the missing-works aggregation (reusing the citation-graph `_local_work_index` /
`_resolve_reference`). **Formula:** over the scope's outgoing references,
`coverage_total` = distinct *resolvable* cited works (those that resolve local **or** external);
`coverage_held` = the subset resolving to a local (held) work; `coverage_pct = round(100·held/total,
1)`, `None` when total is 0. References that resolve to nothing (no identifier / unresolvable) are
excluded from both counts. Surfaced as a prominent headline at the top of the tab: "You hold **X%**
of the works your library cites (held / total)" with a progress bar.

## Files touched

- Backend: `services/citation_summary.py` (coverage + `arxiv_id` on `MissingWork`),
  `services/external_preview.py` (new), `models/citation_worklist.py` (new), `models/__init__.py`,
  `alembic/versions/0052_missing_work_decisions.py` (new), `services/citation_worklist.py` (new),
  `services/export_service.py` (missing-list renderers), `api/v1/endpoints/citations.py` (preview,
  worklist, missing-export endpoints + coverage/arxiv response fields), `openapi.json`,
  `tests/test_citation_enrichments.py` (new).
- Frontend: `api/client.ts` (types + `externalPreview`/`getWorklist`/`setWorklistDecision`/
  `clearWorklistDecision`/`exportMissingWorks`), `pages/CitationSummaryPage.svelte` +
  `pages/CitationSummaryPage.test.ts` (new).

## Verification

- **FULL backend suite (docker):** `pytest backend/tests -q` → **909 passed**. New tests: coverage
  math on a fixture; external-preview merge / no-identifier / all-sources-failing (injected
  connectors) + endpoint auth/no-id/by-reference (mocked); worklist persist + survive-recompute +
  upsert/clear + bad-decision 400 + endpoint roundtrip; missing-list BibTeX/CSV shape + endpoint.
- **`make test-migrations`:** **4 passed** (incl. autogenerate no-drift → the new table matches the
  model; parity clean).
- `ruff check` + `ruff format --check` on `backend agent`: clean. `openapi-check`: current
  (regenerated + committed).
- **`make frontend-check` (vitest + build):** **188 passed / 1 skipped**, build clean. New vitest:
  coverage display, open-in-paper-view icon action, preview fetch/render + graceful no-preview,
  worklist mark→collapsible + persist-on-load, BibTeX/CSV export buttons.
- **Screenshot** (Playwright, admin/paperracks, 1440×900 @2x, **not committed**):
  `/home/zednik/paracord-theme-shots/citation_summary_enriched.png` — coverage header (83.3%, 10/12),
  open-icons on every internal item, missing block with BibTeX/CSV + Preview/Queue/Ignore/Import and
  one preview panel open. Captured via the (untracked) `e2e/capture-citation-summary.mjs` helper.

## Notes / deviations

- **Migration must be applied to the running DB.** The live api container 500'd on `/worklist` until
  `alembic upgrade head` was run (done). Deployers must migrate `0052`.
- **Preview on the demo data shows "no preview available (no identifier)"** — the seeded "Demo:"
  cited-but-missing references carry no DOI/arXiv id, so on-demand fetch has nothing to query. The
  graceful message is the required behaviour; a real preview (title/authors/abstract rendered) is
  exercised by the vitest + backend tests with mocked connectors.
- **C1's mention of "most-cited-external"** for preview: those are in-library works (they have a
  `work_id` and no Import button), so they receive the C2 open-in-paper-view icon like the other
  internal lists; the preview is wired to the frequently-cited-but-missing items (the only external,
  not-in-library entries carrying identifiers).
- Coverage counts *distinct* cited works (local by resolved work id, external by normalized key), so
  a work cited by many scope papers counts once.
- The chart shows "Chart unavailable in this environment" in the headless screenshot only (a Vite
  echarts dep-optimization artifact under Playwright); it renders normally in the browser.

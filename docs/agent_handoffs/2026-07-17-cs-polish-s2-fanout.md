# Handoff: citation-summary polish, Semantic Scholar downloads, graph overlap fan-out

## Files changed

- `frontend/src/pages/CitationSummaryPage.svelte` (+test) — Preview passes title/year (fallback);
  "Send N queued to Import"; Create (record-only, was "Import") + new Import (routes to the
  Import tab: Identifier with DOI/arXiv, else citations box — same as external graph nodes);
  `ol` padding 2.4rem (multi-digit markers were clipped by the scroll container).
- `backend/app/services/external_preview.py` + `endpoints/citations.py` — title/year params;
  `_resolve_doi_by_title` (Crossref bibliographic search, SequenceMatcher ≥0.9 on normalized
  titles, year ±1; refuses ambiguity). EGRESS NOTE: title now leaves the machine on the explicit
  Preview click (like find-on-web); still behind enrichment_enabled.
- `backend/app/services/web_find.py` — `api_pdf_candidates()` (S2 Graph API → DBLP hop →
  Unpaywall), 429 backoff (0/2/5/10s; S2 sends no Retry-After so `_get`'s polite retry never
  fired), `_API_DISCOVERY_CACHE` (128 entries/1h, non-empty results only), fallback cap 5→8.
  `pdf_link_finder.py` — NeurIPS rewrites (papers.nips.cc +.pdf; proceedings.neurips.cc
  -Abstract.html → -Paper.pdf).
- `frontend/src/lib/viz/referenceGraph.ts` / `temporalMap.ts` (+tests) — cross-series co-location
  fan-out (see PROGRESS). Same-series overlaps keep the count-badge collapse.

## Verification

Backend 1270, frontend 327 (69 viz), safety 161, e2e 37/37 clean (one earlier flake was my own
mid-run HMR edit racing the battery — reran clean). Live: TransE SS-page download attached the
real NeurIPS PDF end-to-end (with hosts allowed; config reverted after). Title-fallback preview
live-resolved "Deep Residual Learning…" → 10.1109/cvpr.2016.90; correctly refused the
near-miss "Is Attention All You Need?" (different paper).

## Assumptions made

- The user's download policy stays `restricted`; NeurIPS hosts were allowed only for the test
  and REVERTED. To let such downloads through: allow the hosts in Admin → find-on-web, or
  switch the policy to `careful` (owner-only setting).
- Unauthenticated S2 API remains flaky under load; an S2 API key setting would remove the 429s
  entirely (not added — needs an owner decision on configuring keys).

## Next recommended task

- Optional `semantic_scholar_api_key` setting threaded into `_get` headers for the S2 calls.
- The graph pies could also be offered in the co-citation force layout legend hover.

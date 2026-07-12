# Handoff — Citation/reference matcher improvements (2026-07-12)

On `main`, **not pushed**. Commits: `be35fc2` (matcher F1), `6055185` (incoming direction +
count sync), `9aff7b2` (frontend badge). Full backend suite green in-container (1107 passed,
4 skipped, `-m "not safety"`); frontend vitest 274 passed + build green. Migration `0064`
validated against a scratch pgvector/pg17 container — **not yet applied to the live DB** (the
API entrypoint applies it on the next `docker compose up`/restart; also rebuild images first,
see below).

## Why

Audit of the reference/citation subsystem (owner request): improve the local matcher's precision
and recall, make it cover BOTH directions (references *and* incoming citing papers), verify shared
canonical storage + add/delete/merge lifecycle, and stop the citation-count snapshot drifting from
the fetched citing-papers list.

## What changed

### 1. Matcher F1 (`be35fc2`)

- **rapidfuzz is now a required dependency** (`backend/requirements.txt`). It was optional and NOT
  installed in the deployed image, so `similarity_pct` silently ran on the difflib fallback — no
  token-based scoring at all, i.e. the fuzzy matcher had materially worse recall in production than
  in any test that installed rapidfuzz. **Rebuild api+worker images when deploying.**
- New `title_similarity_pct` (`app/utils/normalization.py`) used by the matcher: compares
  `normalize_title`-normalized strings (dash/colon/punctuation variants score 100), combines plain
  ratio + token_sort (word order) + a token_set containment ratio that only applies when the
  shorter title has ≥5 tokens — truncated subtitles still match, but "Deep learning" can no longer
  score 100 against every "Deep learning for …" (precision guard).
- **arXiv-DOI bridging** everywhere: `10.48550/arXiv.<id>` ⇔ bare arXiv id in the exact identifier
  stage, the D2 gate (an arXiv DOI is compared as an arXiv id, so preprint-vs-published DOI pairs
  no longer disqualify fuzzy), reference dedup keys (`reference_links.reference_dedup_key` now keys
  arXiv DOIs as `arxiv:<base>`, with a legacy `doi:10.48550/…` lookup so live rows keep
  consolidating), and the citation-graph identifier index.
- **Stopword-tolerant blocking** (`_relaxed_blocking_key`/`_block_conditions` in
  `reference_matching.py`): leading a/an/the/on/of/in/for/to/toward(s) no longer put "The X…" and
  "X…" in different blocks (they were never even compared before). Applied to both matcher and
  reverse-rescan directions; reverse-rescan also matches by arXiv id now, not just DOI/title.
- **Year gate ±1** (config `reference_matching.year_tolerance`, default 1, 0 restores strict):
  preprint vs published year drift.

### 2. Incoming direction + lifecycle + count sync (`6055185`)

- Migration `0064_external_paper_local_match`: `external_papers.resolved_work_id` (FK works,
  SET NULL, indexed) + `external_papers.arxiv_id`. Additive/nullable — safe on live data; existing
  rows backfill lazily on the next fetch or the library-wide rescan job.
- `citing_papers.resolve_external_paper` runs every fetched citing paper through the SAME matcher
  (`MatchFields` adapter in `reference_matching.py`); self-match guarded (a paper can't cite
  itself). Exposed as `resolved_work_id` on `GET/POST /works/{id}/citing-papers*` and on
  reference-graph `kind="citing"` nodes (visibility-clamped).
- **Reverse-rescan now runs on the main pipeline**: `store_parsed_extraction` and `enrich_work`
  (when it promotes fields) rescan still-external references + cached citing papers against the
  work that just gained its title/DOI. Previously only manual create / import-from-reference did
  this, so uploaded papers were never linked as targets until a full library rescan.
- Lifecycle fixes: **merge** repoints `ExternalCitationLink` rows (dedup-aware) and
  `ExternalPaper.resolved_work_id` source→base, recorded in `merge_record` and exactly reversed by
  unmerge; **delete** prunes external papers whose only citer link was the deleted work and also
  re-resolves references whose *soft suggestion* (`suggested_work_id`) pointed at it (previously
  left as a stale `likely_match`); the **rescan-all job** also rematches external papers.
- **Count sync**: `fetch_citing_papers` returns `(papers, source, total)`; `store_citing_papers`
  refreshes `work.citation_count/_source/_fetched_at` from the provider total (OpenAlex
  `meta.count`, S2 via one cheap `?fields=citationCount` follow-up), so the "as of" of the list and
  the count can no longer diverge.
- `citing_papers._bare_doi` now delegates to `normalize_doi` — `dx.doi.org/`/`doi:` decorated DOIs
  no longer dedup the same citing paper into a second `external_papers` row.

### 3. Frontend (`9aff7b2`)

Citing-papers panel: an "in library" badge (same style as the references list) that opens the
matched paper; the Import button is hidden for already-local citers. `CitingPaper` TS type gained
`resolved_work_id`.

## Deploy checklist (live instance)

1. `docker compose build api worker frontend` (rapidfuzz + new code).
2. Restart the stack — the API entrypoint applies migration 0064.
3. Optionally hit `POST /works/references/rescan-all` (or enable the startup-rescan toggle) once:
   it now also backfills `external_papers.resolved_work_id` and picks up matches enabled by the
   new blocking/scoring/arXiv bridging.

## Verified

- `pytest -m "not safety"` in-container: 1107 passed / 4 skipped (includes new tests:
  arXiv-DOI bridge, stopword blocking, ±1 year, containment guard, external-paper matching,
  self-match guard, delete GC, merge/unmerge of external links, count-snapshot refresh, legacy
  dedup-key lookup).
- Full alembic chain `base→0064` on a scratch pgvector:pg17 container.
- Frontend `npm test` (274) + `npm run build` green.
- Trimmed-fixture test files (`test_duplicate_merge`, `test_enrichment`, `test_extraction`,
  `test_duplicates_api`) gained the `external_papers`/`external_citation_links` (+`AppConfig`,
  `Reference` where needed) tables their code paths now touch.

## Known follow-ups (NOT done, deliberately)

- `docs/reference/*.md` sections for the matcher/citing papers were updated in the working tree
  but left uncommitted — they sit alongside earlier uncommitted doc edits I didn't want to sweep
  into a commit. Review + commit the docs folder as one piece.
- Consolidation job for pre-existing duplicate `Reference` rows sharing a dedup key (batch 12
  Phase 1b) still doesn't exist; the arXiv-DOI key unification adds one more legacy-key shape it
  should handle (`doi:10.48550/arxiv.<b>` → `arxiv:<b>`).
- Structure audit found (not citation-related, reported to owner): duplicated `_scope_works` in
  summarization/topic_modeling; `duplicates/scan` loads whole tables in the request handler when
  only one selector is given; no RQ retry policy on enrich/embed/chunk/topic/keyword jobs; a third
  arXiv parser in `metadata_enrichment._arxiv_base`; synchronous library-scope topic modeling.

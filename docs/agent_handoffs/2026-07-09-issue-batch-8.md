# Handoff — issue_batch_8 (2026-07-09)

**Task:** implement the 8 owner-reported items in `docs/WORKPLAN_2026-07-09_batch8.md` (triage doc,
intentionally uncommitted). All 8 were built; owner decisions on the open questions are folded in.

## Files changed (by commit)
- `backend: silence pydantic alias + starlette 413 deprecation warnings` — `backend/app/api/deps.py`,
  `backend/app/api/v1/endpoints/auth.py`/`agents.py`/`imports.py`/`works.py`, `pyproject.toml` (issue 1)
- `agent: dedup manifest index_only stubs by file hash` — `backend/app/services/agent_files.py`,
  `backend/tests/test_agents.py` (issue 6, CRITICAL)
- `frontend: library filter panel UX + hybrid search mode` — `frontend/src/pages/LibraryPage.svelte`,
  `frontend/src/api/client.ts`, `backend/app/schemas/saved_filter.py`,
  `frontend/src/pages/LibraryPage.savedfilters.test.ts` (issues 2, 5, 7)
- `backend: name offending DOI + existing paper on collision; close 4 unguarded endpoints` —
  `backend/app/services/doi_conflict.py` (new), `backend/app/workers/jobs.py`,
  `backend/app/api/v1/endpoints/works.py`, `backend/tests/test_doi_conflict.py` (new),
  `backend/tests/test_d7_extraction_recovery.py`, `ROADMAP.md` (issue 3)
- `frontend: add live status dot + queued count to the Jobs nav tab` — `frontend/src/lib/jobsHealth.ts`
  (new), `frontend/src/lib/jobsHealth.test.ts` (new), `frontend/src/App.svelte` (issue 4)
- `backend: overhaul keyword extraction (YAKE + RAKE fusion, filter/trim/boost/dedup)` —
  `backend/app/services/keyword_extraction.py`, `backend/app/services/extraction.py`,
  `backend/app/workers/jobs.py`, `backend/requirements.txt`, `backend/tests/test_keyword_extraction.py`
  (issue 8)

## Owner decisions folded in
- **Issue 3 (DOI collision):** owner kept batch7's *fail-closed* behavior (it prevents duplicate
  accumulation) — the change is only diagnostic clarity: the message now names the offending DOI and
  the paper that already holds it. Also closed the 4 other endpoints that previously 500'd
  (`update_work`, `select`/`bulk-apply`/`delete` metadata assertion, find-on-web apply) → clean 409.
  The cross-visibility-permission edge case is documented as future work in `ROADMAP.md`.
- **Issue 6:** forward fix only — no retroactive cleanup of already-duplicated Works (matches
  batch7's precedent for the sibling `offer_teleport` bug).
- **Issue 8:** owner asked to go beyond TF-IDF-rescored RAKE — added YAKE, plus phrase filtering
  (>4 words / no content word / mostly-stopword), boundary stop-word trimming, and title/abstract/
  heading boosting.

## Assumptions / decisions worth knowing
- **Issue 1 (pydantic alias warning):** the `UnsupportedFieldAttributeWarning` is a FastAPI-internal
  artifact — `analyze_param` sets `field_info.alias = param_name` for *every* non-`Annotated`
  body/query/header param, tripping it for virtually every `payload: Schema` endpoint. Converting the
  whole app to `Annotated` would be a huge, disproportionate refactor, so it's narrowly ignored by
  exact category in `pyproject.toml` (the 4 `authorization` headers were still converted, since that
  was the one the owner pointed at). The 413 rename is a true fix.
- **Issue 8 (YAKE):** `yake>=0.4.8` added to `requirements.txt` (light pure-Python deps:
  click/jellyfish/networkx/numpy/segtok/tabulate — no ML frameworks/model downloads). The import in
  `keyword_extraction._yake_ranking` is **guarded**: if YAKE is ever absent the module degrades to
  RAKE-only, never hard-failing. **api + worker images were rebuilt** so the baked copies carry YAKE
  (both run keyword extraction). YAKE + RAKE rankings are combined by Reciprocal Rank Fusion (same
  idea as `hybrid_search`), which avoids their incomparable score scales.
- **Issue 8 (corpus TF-IDF):** implemented as an *optional* `corpus_idf` param on `extract_keywords`
  plus a `build_corpus_idf(texts)` helper. It is **not** wired into the per-paper extraction hot path
  (that would force a full-corpus scan per paper; at library scale that's a real cost). YAKE is
  corpus-free and already targets distinctiveness, so the per-paper default is YAKE+RAKE+boost. A
  future library-wide batch keyword pass can build corpus IDF once and pass it in.
- **Issue 8 ("noun-like" filter):** true POS tagging needs nltk/spacy (deliberately avoided). "Content
  word" is approximated as a non-stopword token of length ≥ 3; dedup folds trivial plurals
  (`network`/`networks`) via a crude trailing-`s` stem.
- **Issue 4 (Jobs badge):** a lightweight 20s poll in `App.svelte` (independent of the Jobs page's
  own poll, which pauses when that tab is hidden) drives a dot next to the Jobs tab. Colors mirror the
  Jobs-page semaphore (red/yellow/green) + **blue** when jobs are running or queued, with a `[N]`
  queued badge. Poll only runs when signed in and the role can see the Jobs tab.

## Tests
- Backend: new `test_doi_conflict.py` (8), new keyword tests in `test_keyword_extraction.py`, updated
  `test_d7_extraction_recovery.py`, new `test_index_only_manifest_reuses_existing_file_by_hash` in
  `test_agents.py`. Full bare `pytest backend/tests`: 1149 passed / 5 skipped, with the **one known
  environmental flake** `test_queue_cap_not_bypassed_by_concurrency` (shared in-memory SQLite under
  true ThreadPool parallelism — SQLAlchemy e3q8; documented in PROGRESS.md's gate-gap lesson). It
  passes in isolation and in the full `make test-safety` battery (160 passed), and is unrelated to
  this batch's changes.
- Frontend: new `jobsHealth.test.ts` (6), new hybrid-mode test in `LibraryPage.savedfilters.test.ts`.
  Full frontend suite: 241 passed / 4 skipped.
- Safety suite: 160 passed / 1 skipped, **0 warnings** (was 3).

## Security implications
- Issue 6 closes a real data-integrity gap (duplicate Works), no new surface.
- Issue 3 turns unhandled 500s into 409s; the enriched message can name a paper title — see the
  documented cross-visibility caveat in `ROADMAP.md` before shelves/racks gain visibility perms.
- Issue 1 `Annotated` header conversion is behavior-preserving (verified by the safety suite).

## Next recommended task
- Wire corpus IDF into a library-wide batch keyword pass (issue 8's optional path) if keyword quality
  still reads generic on a large corpus.
- Revisit the cross-visibility DOI-collision policy when shelf/rack visibility permissions land.

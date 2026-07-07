# Handoff — Batch P: paper view / metadata (2026-07-07)

Three owner-facing items from `docs/WORKPLAN_2026-07-06.md` Batch P. Committed on `main` (not
pushed), one commit per item (P1 split into a + b).

## Commits

- `e8bddd5` frontend: live-refresh open paper when its background job finishes (P1a)
- `a3213e3` frontend: keep library and search listings consistent after delete (P1b)
- `8ace3e7` backend: add match % to metadata-conflict review; show it in the paper view (P2)
- `c735102` backend: backfill arxiv id and DOI on web-sourced and enriched papers (P3)

## P1a — live-refresh the open paper after a background job

`WorkDetail.svelte` gained a lightweight job watcher: a 4 s `setInterval` poll of `getJobs(60)`
that runs **only while a relevant job is in flight** and self-terminates when everything settles.

- Correlation (`jobMatchesOpenWork`): work-targeted jobs match `target_id === work.id`;
  extraction is file-targeted, so file jobs match the ids in `workFileIds` (derived from the
  paper's `files`).
- Watching starts from the action handlers (`enrich`/`extract`/`topic`/`keywords`/`reextract`/
  `forceOcr`/`upload`) passing the returned job id(s) so a job that finishes before the first poll
  is still caught, and auto-detects on open (`loadDetail` → `watchWorkJobs()`) so a job already
  running (e.g. import extraction) is picked up.
- When the watched jobs settle, `refreshOpenWork()` refetches via `getWork` → `onUpdated` →
  `loadDetail(fresh, false)` so metadata/keywords/files update in place. `onDestroy` clears the
  timer; changing the open paper resets the watch. A 90-tick (~6 min) cap bounds it.
- A successful find-on-web download also refetches the work (so P3's backfilled identifiers show
  immediately) and re-arms the watch for the queued extraction.

Test: `WorkDetail.refresh.test.ts` (fake timers) — refetches once the in-flight job finishes; no
spurious refetch (and polling stops) when nothing is pending.

## P1b — mutations must refresh their view

Audit result: shelves, racks, tags, duplicates, and **library batch-delete** already refetch or
splice in place. Two real gaps fixed:

- **Library single-delete** (`LibraryPage.onDeleted`): the row was already spliced out, but
  `totalWorks` (the "{n} papers" counter) and `totalPages` stayed stale. Now decrements the count
  and recomputes pages from a captured `perPage` (derived from the last `listWorks` envelope) — no
  refetch, so the row still disappears immediately.
- **Search results** (`SearchPage`): the `WorkDetail` `onDeleted` only closed the modal; it now
  also filters the deleted work out of the `results` list.

Test: `LibraryPage.delete.test.ts` — opening a paper and deleting it drops the row and decrements
the counter with no reload.

## P2 — metadata-conflict "match %"

- `utils/normalization.py`: `normalize_for_similarity` (join `infor-\nmation` line-break
  hyphenation, collapse whitespace, lowercase) + `similarity_pct(a, b)` → 0–100 using rapidfuzz
  `max(ratio, token_set_ratio)` (difflib fallback). Identical-modulo-formatting text → 100.
- `works.py` `FieldReview.match_pct: float | None`, computed in `get_work_metadata` as the lowest
  pairwise similarity among the distinct conflicting values (`_conflict_match_pct`); `None` when no
  conflict.
- Frontend: `FieldReview.match_pct` in `client.ts`; a `N% match` badge (`.match-pct`) beside each
  conflicting field header in `WorkDetail.svelte`.

Tests: `backend/tests/test_metadata_match_pct.py` (formatting-only → 100, different → low, no
conflict → None, plus normalize/similarity unit checks); `WorkDetail.metadata.test.ts` (badge shown
for a conflict, hidden without one).

## P3 — arXiv id / DOI not filled from web/enriched papers (bug)

**Broken path (root cause):** the find-on-web download flow dropped identifiers end to end —
`WebCandidate` had no `arxiv_id` (search_arxiv computed it only to build the PDF URL);
`WebFindDownloadItem` didn't transmit doi/arxiv_id; `download_and_attach` never wrote either onto
the work. Enrichment separately could never set `arxiv_id` (absent from `ExternalMetadata`,
`PROMOTABLE_FIELDS`, `_apply_field`). The identifier-**import** endpoint always worked, which is why
only these paths dropped the id.

**Fix:**

- New `services/identifiers.backfill_identifiers(work, *, doi, arxiv_id)` — fills an **empty**,
  **unlocked** field only (normalizes doi via `normalize_doi`, sets `arxiv_id` + `arxiv_base_id`);
  respects `user_confirmed` / `confirmed_fields` (SPEC §8.12). Returns the fields it set.
- Find-on-web: `WebCandidate.arxiv_id` populated in `search_arxiv`; carried through
  `WebCandidateRead` → `WebFindDownloadItem` (client sends `doi`/`arxiv_id`) →
  `download_and_attach(doi=…, arxiv_id=…)`, which backfills on the attach/dedup success path and
  audits `identifiers_filled`.
- Enrichment: `ExternalMetadata.arxiv_id` populated from the arXiv Atom `id` (`parse_arxiv_atom`)
  and Semantic Scholar `externalIds.ArXiv` (`parse_semantic_scholar`); `enrich_work` calls
  `backfill_identifiers(work, arxiv_id=meta.arxiv_id)` per source (DOI is still handled by the
  existing assertion-promotion machinery).

Confirmed via tests: `test_web_find.py::test_download_backfills_arxiv_id_and_doi` (empty work gets
both) and `::test_download_respects_locked_identifier` (locked doi kept, empty arxiv_id filled);
`test_identifier_backfill.py` (helper unit, arXiv-atom parse, enrichment fill + lock-respect).

**Not fixed (deliberately, out of scope):** parsing an arXiv id from PDF text during GROBID
extraction (path 3 in the brief) — GROBID/`ParsedPaper` carry no arXiv id and the primary reported
bug was the web path. Noted for a follow-up if desired.

## Files changed

- Backend: `app/utils/normalization.py`, `app/services/identifiers.py`,
  `app/services/web_find.py`, `app/services/metadata_enrichment.py`,
  `app/api/v1/endpoints/works.py`, `backend/openapi.json`.
- Frontend: `src/api/client.ts`, `src/components/WorkDetail.svelte`, `src/pages/LibraryPage.svelte`,
  `src/pages/SearchPage.svelte`.
- Tests: `backend/tests/test_metadata_match_pct.py`, `backend/tests/test_identifier_backfill.py`,
  `backend/tests/test_web_find.py`, `frontend .../WorkDetail.refresh.test.ts`,
  `LibraryPage.delete.test.ts`, `WorkDetail.metadata.test.ts`, `WorkDetail.findweb.test.ts`.

## Verification

- Full backend suite: **893 passed** (`docker compose exec -T api python -m pytest backend/tests -q`).
- `make frontend-check`: **181 passed / 1 skipped**, build OK.
- `ruff check backend agent` + `ruff format --check` clean; `make openapi-check` current.
- No migration added.

## Assumptions / notes

- P1a polling matches the JobsPage `setInterval(4000)` pattern; it does not use a `visible` gate
  (the detail panel unmounts/`{#key}`-remounts per paper, and the watch self-terminates on settle).
- P2 `match_pct` is field-level (lowest pairwise among distinct values), not per-assertion — matches
  the owner's "similarity between the conflicting values" for the common two-value case.
- Screenshot `metadata_match_pct.png`: the badge is covered by backend + frontend tests; not
  re-captured via Playwright this pass (no seeded near-dup-abstract conflict in the demo data).

## Security implications

- P3 backfill never overwrites a user-confirmed/locked identifier; doi/arxiv_id are normalized
  before storage. Find-on-web download security gates (denylist, SSRF, policy modes, confirmation)
  are unchanged — identifiers are only persisted on the existing attach success path.
- P1a adds read-only `getJobs`/`getWork` polling, bounded and self-terminating; no new endpoints.

## Next recommended task

If desired: parse `arXiv:NNNN.NNNNN` from extracted PDF text and backfill via
`backfill_identifiers` in `store_parsed_extraction` (path 3), and add a Playwright capture of the
match-% badge from a seeded near-duplicate-abstract conflict.

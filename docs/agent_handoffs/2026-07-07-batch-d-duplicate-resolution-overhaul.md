# Batch D — Duplicate-resolution overhaul (2026-07-07)

## Task
Replace the old duplicate Merge/Link (both merely moved the PDF, leaving one entry full + one empty)
with true consolidation, a reversible single-level Unmerge, flatten-on-re-merge, and a bidirectional
Link — plus a reworked review UI. Owner-approved spec: `docs/WORKPLAN_2026-07-06.md` Batch D.

## Files changed
- **Model**: `backend/app/models/work.py` — `Work.merged_into_id` (self-FK, SET NULL; shadow marker),
  `Work.merge_record` (JSONB with `none_as_null=True` so `IS NULL` reliably means "not reversible"),
  new `WorkLink` table; `backend/app/models/__init__.py` exports `WorkLink`.
- **Migration**: `backend/alembic/versions/0053_work_merge_shadow.py` (adds the two columns + FK +
  index and the `work_links` table). Applied to the live api DB via `alembic upgrade head`.
- **Service**: `backend/app/services/duplicate_resolution.py` — `merge_works`, `unmerge_work`,
  `redirect_references` (reusable link-fixing), `link_works`/`linked_work_ids`, `merge_preview`,
  `has_reversible_shadow`, `_finalize_reversible_shadows` (flatten), `_transfer_identifiers`,
  `_fill_and_conflict_fields`, `_move_owned_entities`, reversal helpers. The old file/shelf/tag
  move helpers were replaced by recording-based movers.
- **Visibility clamp** (shadows hidden everywhere): `services/access.py` (`visible_works_query` +
  `_visible_work_condition`), `services/citation_graph.py` (`_scope_works`, `_local_work_index`,
  neighborhood focus + `_direct_citation_neighbors`), `services/export_service.py` (`_filter`),
  `services/semantic_search.py` (index build + related + lexical + semantic fetch),
  `services/bm25_index.py` (corpus + fetch), `services/chunk_search.py` (`_rollup`),
  `services/topic_graph.py` + `services/topic_modeling.py` (`_scope_works`),
  `services/summarization.py` (`_resolve_works`), `services/duplicate_detection.py` (skip shadows +
  never re-propose them), `workers/jobs.py` (dedup sweep), `api/.../shelves.py` (`list_shelf_works`),
  `api/.../works.py` (`search_annotations`).
- **Endpoints**: `api/v1/endpoints/works.py` — `WorkRead.merged_into_id` + computed
  `has_reversible_shadow`; `GET /works/{id}/related-links`; `POST /works/{id}/unmerge`.
  `api/v1/endpoints/duplicates.py` — `GET /duplicates/{id}/merge-preview?base_work_id=`.
  `backend/openapi.json` regenerated.
- **Frontend**: `frontend/src/api/client.ts` (Work.merged_into_id/has_reversible_shadow,
  `MergePreview`, `getMergePreview`/`unmergePaper`/`getRelatedLinks`); `pages/DuplicatesPage.svelte`
  (base/merge-from + ⇄ swap + live preview, Merge/Link); `components/WorkDetail.svelte` (Unmerge
  button + "Linked papers" section).
- **Tests**: new `backend/tests/test_duplicate_merge.py` (14); updated `backend/tests/test_duplicates_api.py`
  (new tables in fixture; Link test rewritten to bidirectional; resolved-guard test uses an owner);
  new `frontend/src/pages/DuplicatesPage.test.ts` (4) + `frontend/src/components/WorkDetail.merge.test.ts` (2).

## How it works
- **Merge (base ← source)**: guards (no self-merge, neither side a shadow) → flatten any prior
  reversible shadow of the base → fill empty base fields (+ provenance assertion) / add differing
  values as `conflict` assertions (reusing MetadataAssertion; ≥2 distinct values → the existing
  metadata-review UI shows a conflict) / never touch locked fields → *transfer* doi + arXiv (clear on
  the shadow, flush, set on base — avoids the `uq_works_doi`/`uq_works_arxiv_base_id` collision) →
  move file links, shelf/tag memberships, outgoing refs+mentions, annotations, versions, non-field
  (authors) assertions → `redirect_references` repoints INCOMING refs/mentions to the base → source
  gets `merged_into_id`+`merge_record`, `work_type="merged"`. All in the single transaction the
  endpoint commits.
- **Unmerge** reads `merge_record`, moves every recorded entity back, un-redirects refs, hands the
  transferred identifiers back (base-cleared-then-flush-then-shadow-set to dodge the unique index),
  clears the filled fields, deletes the added conflict assertions, and restores the shadow to a
  standalone visible paper.
- **Flatten-on-re-merge**: `_finalize_reversible_shadows` nulls the prior shadow's `merge_record`
  (kept hidden + redirected, now permanent) before the new merge, so Unmerge is always single-level.
- **Link** records one order-normalized `work_links` row (idempotent) — no move/hide/delete.
- **Shadows hidden**: `merged_into_id IS NULL` clamp in the two access primitives + every raw
  `select(Work)` display source (admin's `visible_work_ids → None` sentinel leaked otherwise).

## Transaction boundaries
One transaction per action: the service mutates + `flush`es; the endpoint `commit`s. A failure
mid-merge leaves nothing committed (verified by `test_merge_failure_mid_way_rolls_back_cleanly`).

## Assumptions / deviations
- Source's own promotable-field assertions (title/abstract/…) stay on the source (its provenance);
  the base gets fresh `source="merge"` assertions. Non-field assertions (authors) are moved.
- Derived AI rows (embeddings/chunks/summaries/topics) are NOT moved — the shadow is excluded from
  every search/index path instead, which keeps unmerge exact without regenerating derived data.
- `place_on_default_if_loose(base)` after merge is not reversed by unmerge (base is the survivor).
- A resolved duplicate candidate that now touches a shadow is hidden from non-admins (the candidate
  list already hides candidates touching a hidden work); owners still see it.

## Verification
- FULL backend suite green (1079 passed / 1 skipped). `make test-migrations` 4 passed (no drift).
- Frontend 194 passed / 1 skipped. `ruff check`/`format --check` clean; `check_secrets` clean.
- Live-Postgres smoke (rolled back): merge fills+transfers doi, hides shadow, redirects incoming ref;
  unmerge restores both papers + identifiers + ref. No unique-constraint violation.
- Screenshot: not re-captured — staging a live duplicate candidate for `duplicate_review.png` needs
  seeded near-duplicates; the flow is covered by the vitest DuplicatesPage/WorkDetail tests.

## Security implications
Shadows are now excluded from every work-returning path for ALL users (incl. admin/owner) — the new
clamp closes the admin `None`-sentinel leak across search/graph/viz/export/annotations. No new
unauthenticated surface; the unmerge endpoint requires contributor + per-paper modify.

## Next recommended task
Optional: re-capture `duplicate_review.png` after seeding a candidate; consider surfacing a "merged
into" hint if a user deep-links a shadow id (currently shadows just never appear in listings).

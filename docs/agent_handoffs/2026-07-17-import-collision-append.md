# Handoff: PDF-import collision resolution (append-to-existing, DOI editing)

## Files changed

- `backend/app/services/import_staging.py` —
  - `append_item_to_work()`: links the staged file to an existing paper; applies the stored
    preview TEI ONLY when the paper has no `RawTeiDocument` yet (PDF-less record → full
    extraction; extracted paper → alternate file only, references untouched); owes a fresh
    extraction when the item has no TEI.
  - `commit_staging()`: new `append` decision (ACL via `access.can_modify_work`, savepoint-
    contained); accepted items get a DOI pre-check with precise refusals — naming the owning
    paper ("choose Attach PDF to it") or the same-DOI sibling file created earlier in the same
    commit (book-vs-chapter); summary gains `appended` / `appended_work_ids`.
  - `set_item_doi()` + `_item_parsed_paper()`: preview-time DOI override (edit/clear) with
    collision re-detection; the override supersedes GROBID's original in the stored TEI at
    commit time (both create and append paths re-parse through `_item_parsed_paper`).
- `backend/app/services/extraction.py` — `store_parsed_extraction` promotes a parsed DOI into an
  empty `work.doi` only when no OTHER live work owns it; otherwise the DOI is recorded as a
  non-canonical assertion. Kills the unique-violation 500 class for append/re-extract.
- `backend/app/api/v1/endpoints/imports.py` — `StagingDecision.action` gains `append` (+
  `target_work_id`); `StagingCommitResponse.appended`; new
  `PATCH /imports/staging/{batch_id}/items/{item_id}` (`{doi}`; extracted/extract_failed items
  only, 409 otherwise). `backend/openapi.json` regenerated.
- `frontend/src/pages/ImportPage.svelte` — per-item decision map (`accept`/`skip`/
  `append:<workId>`); collision rows render an action dropdown (Create-new disabled for
  same_doi/same_pdf; append candidates = DOI matches then title matches, deduped; same_pdf
  candidates excluded — that PDF is already on those papers); inline DOI edit/clear; client-side
  intra-batch same-DOI warning on both rows; commit summary reports attached count.
- `frontend/src/api/client.ts` — `StagingDecision` type, `appended` on the commit result,
  `patchStagingItemDoi()`.

## Assumptions made

- Append to an ALREADY-extracted paper deliberately does not apply the new TEI (references would
  be replaced wholesale by the second PDF's bibliography). The file attaches as an alternate;
  a manual per-file "Re-extract" in the paper view remains available if the user wants the swap.
- `same_pdf` collisions stay non-creatable and offer no append target (the PDF is already
  attached to those papers); dropdown offers skip only unless title/DOI matches add candidates.
- Direct-mode ("Import directly") behaviour is unchanged: blocked items are skipped with a note —
  collision resolution is a preview-mode feature.

## Tests added or skipped

- +9 backend (append applies/preserves extraction, ACL refusal, sibling-DOI precise warning,
  library-owner hint, clear-DOI-then-create, no-DOI-stealing on append, PATCH endpoint, commit
  append HTTP round-trip), +3 frontend (dropdown contract for DOI vs. title matches, append
  commit payload, intra-batch warning + inline clear). Full battery green: backend 1254,
  frontend 324, safety 161, `E2E_ONLINE=1` e2e 37/37, 0 flaky. Live-verified with real GROBID:
  knowrow.pdf appended via the UI to a PDF-less same-DOI record (100 references extracted).

## Security implications

- Append is ACL-checked with the standard modify guard (contributor own-only); a refusal is a
  warning, not an exception. The DOI PATCH is scoped to the caller's own staging batch
  (`_require_own_staging_batch`).

## Next recommended task

- Consider surfacing append candidates in "Import directly" summaries ("skipped — same DOI as X,
  use Preview & choose to attach"), and a work-picker fallback in the dropdown for attaching to
  an arbitrary paper (today's candidates are the detected matches only).

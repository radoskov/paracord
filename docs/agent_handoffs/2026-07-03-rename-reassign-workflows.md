# Handoff — everyday rename/reassign workflows (shelves, racks, tags, applied tags)

## Task
Close the user-facing gaps the E2E pass surfaced: rename a shelf, rename a rack, rename + delete a
tag, and show a paper's applied tags (and have it lose a deleted tag). Full-stack, per item.

## What already existed vs what was added
- **Rename shelf / rename rack** — backend `PATCH /shelves/{id}` and `PATCH /racks/{id}` already
  existed and already emitted `shelf.modified` / `rack.modified` (D31.1). Only the UI was missing.
  Added an inline rename control to each page.
- **Rename tag / delete tag** — entirely missing. Added `PATCH /tags/{id}` (contributor+) and
  `DELETE /tags/{id}` (editor+) with audit events, plus Tags-page Edit/Delete controls.
- **Applied tags on a paper** — missing. Added `GET /works/{id}/tags` and chips in `WorkDetail`.

## Files changed
- `backend/app/api/v1/endpoints/tags.py` — `TagUpdate`, `update_tag` (PATCH, 409 on normalized-name
  clash, `tag.modified`), `delete_tag` (DELETE, cascades `TagLink`, `tag.deleted`).
- `backend/app/api/v1/endpoints/works.py` — `WorkTagRead` + `GET /works/{id}/tags` (SEE-guarded).
- `backend/tests/test_org_rename_and_tags.py` — new suite (rename shelf/rack/tag + role gates +
  audit events, tag-delete link cascade, applied-tags endpoint SEE-safety).
- `backend/openapi.json` — regenerated (`make openapi`).
- `frontend/src/api/client.ts` — `updateTag`, `deleteTag`, `listWorkTags`, `AppliedTag` type.
- `frontend/src/lib/session.ts` — `isEditor` store (tag-delete gate floor).
- `frontend/src/pages/ShelvesPage.svelte`, `RacksPage.svelte` — inline rename control.
- `frontend/src/pages/TagsPage.svelte` — inline Edit (name/colour/description) + Delete.
- `frontend/src/components/WorkDetail.svelte` — applied-tag chips (colour + per-chip remove),
  refreshed on apply/remove; loads via `listWorkTags`.
- `frontend/src/components/WorkDetail.*.test.ts` — added `listWorkTags` mock to each mock client.
- `e2e/helpers.ts` — `apiCreateTag`, `apiDeleteTagsByName`, `apiListWorkTags`.
- `e2e/tests/25-rename-shelf.spec.ts`, `26-rename-rack.spec.ts`, `27-tag-rename-delete.spec.ts`,
  `28-paper-applied-tags.spec.ts` (new); `22-tags.spec.ts` (updated to the chip-based remove).

## Assumptions
- Tag rename is a low-bar content action (contributor+, like tag creation); tag delete detaches the
  tag across all entities so it is editor+ (this also used the already-defined-but-unused
  `EDITOR_DEP`). Rename-onto-an-existing-name is rejected (409), never a silent merge.
- Tag `TagLink`s are removed explicitly in the endpoint (the FK also has `ondelete=CASCADE`); the
  tagged entities are untouched.

## Tests
- Backend full suite: 864 passed. E2E: 27 passed, 2 skipped (GROBID import + arXiv online).
  Frontend: 124 passed, 1 skipped; build clean. `ruff check`/`format` clean; `check_secrets` clean.

## Security implications
- `GET /works/{id}/tags` is guarded exactly like `get_work` (404 if the caller can't see the paper),
  so a paper's tags never leak. Tag mutations keep the role floors above; per-entity tag
  attach/detach guards are unchanged.

## Next recommended task
- Consider surfacing applied tags on shelves/racks detail views too (same endpoint pattern), and a
  "merge tags" action (the rename 409 currently blocks accidental merges — an explicit merge could
  reassign links then delete the source tag).

# Handoff — D31.4 search operators + D31.5 export formats/targets (2026-07-03)

Implemented the second D31 batch (items 4 and 5). All work committed on `main` (not pushed).

## Commits (on `main`)

- `bee6371` — `search: add abstract/summary/fulltext/file/duplicate/version/warning + has:grobid|ocr operators (D31.4)`
- `c55e0d4` — `export: add LaTeX \cite + Pandoc-Markdown formats and import-batch/missing-references targets (D31.5)`

(A follow-up docs commit updates `PROGRESS.md` + this handoff.)

## D31.4 — additional search operators (§14.2)

Parser (`backend/app/services/search_query.py`): added the keys to `_KNOWN`, fields to
`ParsedQuery`, and dispatch in `parse_search_query`; `has:grobid`/`has:tei`/`has:ocr` handled in
`_apply_has`; a `_as_bool` helper interprets the review-state operator values. Query mapping
(`backend/app/api/v1/endpoints/works.py` `build_works_query`) — every condition is appended to the
`access.visible_works_query` base, so none can widen visibility:

| Operator | Maps to |
| --- | --- |
| `abstract:<text>` | `Work.abstract ILIKE %text%` |
| `summary:<text>` | EXISTS `Summary` (entity_type=work, entity_id=Work.id) with `text ILIKE %text%` |
| `fulltext:<text>` | EXISTS `WorkChunk` (work_id=Work.id) with `text ILIKE %text%` (extracted body) |
| `file:<name>` | EXISTS `FileWorkLink`→`File` with `original_filename ILIKE %name%` |
| `has:grobid` (`has:tei`) | EXISTS `RawTeiDocument` (work_id=Work.id) |
| `has:ocr` | EXISTS linked `File` with `text_layer_quality == "ocr_added"` |
| `duplicate:<yes\|no>` | (¬)EXISTS OPEN work-type `DuplicateCandidate` referencing the work (entity A or B) |
| `version:<yes\|no>` | (¬) `Work.version_group_id IS NOT NULL` OR EXISTS `WorkVersion` (work_id=Work.id) |
| `warning:<text\|*>` | EXISTS `FileWorkLink` with `warning_state != "none"` (for `*`/`any`) or `warning_state ILIKE %text%` |

Value semantics: `duplicate:`/`version:` read as boolean — explicit negatives (`no`/`false`/`none`/
`0`) are False, everything else (`yes`/`open`/`any`/…) True. `warning:*`/`warning:any` = any
warning; a literal is a `warning_state` substring match.

### Backing-data notes (no invented schema)

- `fulltext:` searches `work_chunks.text` (the extracted-body passages), not raw TEI XML — chunks
  are the clean body text; TEI XML would match markup. Works with no chunks simply don't match.
- No sub-filter is a no-op — every operator has a real backing model. `duplicate:` intentionally
  matches only **work↔work** open candidates (not the file-level `multiwork_file` candidates, whose
  entity is a `File`); the file-level multiwork warning is reachable via `warning:`.

Tests: `backend/tests/test_search_query.py` — parser tests
(`test_parses_field_scoped_text_operators`, `test_parses_extraction_state_has_values`,
`test_parses_review_state_operators`) + endpoint tests
(`test_list_works_abstract_and_summary_operators`, `..._fulltext_and_file_operators`,
`..._extraction_state_operators`, `..._review_state_operators`).

## D31.5 — export formats + targets (§8.13)

`backend/app/services/export_service.py`:

- **`latex`** renderer (`_render_latex` + `_latex_reference` + `_escape_latex`): a leading
  `\cite{key1,key2,...}` (ready-to-paste multi-cite) then a `\begin{thebibliography}{99}` block with
  one `\bibitem{key} Authors. Title. \emph{Venue}, Year. DOI: x.` per work. LaTeX specials
  (`& % $ # _ { } ~ ^ \`) are escaped. Media: `.tex` / `application/x-tex`.
- **`pandoc`** renderer (`_render_pandoc`): a leading `[@key; @key]` combined citation then a
  `# References` Pandoc-Markdown list (`- [@key]: <inline reference>`, reusing `_entry_inline`).
  Media: `.md` / `text/markdown`.
- **`import_batch`** scope (in `_resolve_works`): works with `Work.import_batch_id == scope_id`,
  visibility-clamped like every other scope.
- **`missing_references`** target: handled early in `export_bibliography` via
  `_resolve_unresolved_references` (`Reference.resolved_work_id IS NULL`, optional `scope_id` narrows
  to one citing work, clamped to references whose *citing* work is visible) +
  `_render_missing_references`. These have no local work/citation-key, so output is raw
  reference strings (`raw_citation`, else composed title/year/DOI/arXiv): a Markdown bullet list for
  `markdown`/`pandoc`, a JSON array (`[{"raw": ...}]`) for `csl-json`, one-per-line otherwise. Audit
  event carries `reference_count` instead of `work_count`.

Frontend: `frontend/src/api/client.ts` — `ExportFormat` gains `latex`/`pandoc`, `EXPORT_FORMATS`
gains the two labelled options (so they appear in `ExportDialog` automatically), and
`ExportScopeType` gains `import_batch`/`missing_references`. Updated the format-set assertion in
`client.additional.test.ts`.

Tests: `backend/tests/test_export_formats.py` — `test_latex_cite_and_thebibliography`,
`test_latex_escapes_specials`, `test_pandoc_citations_and_reference_list`,
`test_export_import_batch_target`, `test_export_missing_references_target`.

## Verification

- Full backend suite: `docker compose exec -T api python -m pytest backend/tests -q` → **773 passed**.
- `ruff check backend agent && ruff format --check backend agent` → clean (run on host).
- `backend/openapi.json` regenerated + committed (the only schema-visible change is the `list_works`
  docstring; export format/scope strings are plain-string request fields, not enums, so no enum
  churn — the new values are validated at the service layer).
- Frontend: typecheck + `npm run test` → **88 passed / 1 skipped**.

### Deviation — frontend build write permission

`make frontend-check`'s **build** step fails with `EACCES` writing to `frontend/dist/assets`: that
directory is owned by `root` from a prior in-container build, while the current build runs as the
`zednik` uid and cannot overwrite it (and even `--user root` in the compose run can't remove it —
looks like userns remap). This is a pre-existing environment/permissions artifact, **not** a code
issue: Vite reports `170 modules transformed` (TypeScript/Svelte compilation of the change
succeeds) and fails only at the file-write step. Typecheck + all Vitest tests pass. Fix out of band
by `chown`-ing / removing the root-owned `frontend/dist` on the host.

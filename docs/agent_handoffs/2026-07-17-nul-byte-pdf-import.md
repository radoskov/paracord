# Handoff: NUL-byte PDF text layer broke every import path

## Files changed

- `backend/app/services/storage.py` — public `sanitize_extracted_text()` + shared
  `CONTROL_CHARS` regex (C0 controls except `\t \n \r`, plus DEL); applied inside
  `_extract_pdf_preview` so the sanitized preview reaches BOTH ingest call sites (folder scan +
  upload/attach) and, transitively, the agent-file preview copy.
- `backend/app/services/chunking.py` — its private `_CONTROL_CHARS` (added earlier for the same
  Postgres `DataError` through GROBID section labels) now imports the shared constant.
- `backend/tests/test_pdf_control_char_text.py` — sanitizer unit test + two ingest-path
  regressions using a synthesized PDF whose text layer round-trips `\x00`/`\x02` through
  PyMuPDF (verified that `insert_text` → `get_text` preserves them).

## Root cause

`test_data/95-ont-ijcai95-ont-method.pdf` (IJCAI-95, custom font encoding) extracts its first
page with raw control codes incl. NUL. `files.preview_text` is Postgres TEXT, which rejects NUL
with `DataError` → unhandled 500 → the browser surfaces "NetworkError when attempting to fetch
resource." The PDF's embedded file ("High quality print job") was a red herring — embedded
attachments are inert here (stored byte-for-byte, never parsed or served individually), so
nothing needs stripping.

## Assumptions made

- Stripping to a space (not deleting) mirrors the chunking sanitizer, so both stay consistent
  and word boundaries survive.
- GROBID output is XML and cannot carry NUL; `/files/{id}/text` returns text live (never
  persisted) — both out of scope.

## Tests added or skipped

- 3 added (see above). SQLite accepts NUL, so the regressions assert the stored value is clean
  rather than that the INSERT succeeds. Full battery green: backend 1240, frontend 305, safety
  161, `E2E_ONLINE=1` e2e 37/37, 0 flaky. Live check: the real file imports end-to-end
  (extract → enrich → chunk → embed all Job OK; title "Towards a Methodology for Building
  Ontologies", year 1995).

## Security implications

- None negative; removes a way to make the API 500 with crafted input. Embedded PDF attachments
  remain stored as-is — if serving them individually is ever added, revisit (content scanning /
  stripping would belong there).

## Next recommended task

- The generic 500 → "NetworkError" experience hides real causes; a DataError-to-400 handler (or
  a global exception envelope with a request id) would make the next such failure diagnosable
  from the UI.

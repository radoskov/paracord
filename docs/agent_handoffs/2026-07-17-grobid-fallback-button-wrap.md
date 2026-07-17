# Handoff: GROBID full-text crash fallback + Files-panel button wrap

## Files changed

- `backend/app/services/grobid_client.py` — `merge_header_and_references()` (module-level,
  testable) + degraded path in BOTH `process_fulltext_document_sync` and the async twin: on a
  5xx from `processFulltextDocument`, call `processHeaderDocument` (Accept: application/xml) and
  `processReferences`, splice the references `listBibl` into the header TEI's `<text>` (same
  default TEI namespace, so a string splice stays well-formed). `parse_tei` finds references via
  `.//listBibl/biblStruct`, so downstream (staging preview, extract job, enrichment chain) needs
  no changes. Header-also-fails → the ORIGINAL full-text error is raised (unchanged failure
  semantics). Connection errors still map to `GrobidUnavailableError` before any of this.
- `backend/tests/test_grobid_client.py` — merge splicing, no-references passthrough, the
  degrade flow (URL call order asserted), original-error-when-header-fails.
- `frontend/src/components/WorkDetail.svelte` — `.file-actions` wraps (`flex-wrap: wrap`,
  `justify-content: flex-end`, dropped `flex-shrink: 0`).

## Root cause (open_ease.pdf)

`TEIFormatter.toTEITextPiece: fromIndex(9) > toIndex(8)` — a GROBID-internal indexing bug in
full-text body formatting for this document's structure. Reproduced with every parameter
combination (segmentSentences on/off, no coordinates) and after a PyMuPDF re-save
(garbage=3/clean) — content-driven, not fixable client-side. Header and references endpoints
return 200 for the same file.

## Assumptions made

- A degraded extraction (metadata + full bibliography, no body sections) beats a hard
  `extract_failed`: the paper becomes searchable, enrichable, and its reference graph works;
  the reader is unaffected (it renders the PDF directly). Chunking sees title+abstract only.
- The fallback triggers ONLY on 5xx from the full-text endpoint; 4xx (bad request shapes) and
  unavailability keep their existing behaviour.

## Tests added or skipped

- +4 client tests. Full battery green: backend 1258, frontend 324, safety 161,
  `E2E_ONLINE=1` e2e 37/37, 0 flaky. Live-verified: open_ease.pdf imports end-to-end (title,
  authors, abstract, 40 references; file `extracted`; worker logs the degrade warning; enrich/
  chunk/embed all Job OK). Button wrap verified at a 1100px viewport (Remove button inside the
  page, previously pushed out).

## Security implications

- None: the fallback talks to the same GROBID service with the same inputs; no new egress
  (consolidation flags are passed through unchanged).

## Next recommended task

- Surface "degraded extraction (no body sections)" on the file/work in the UI — today the only
  trace is the worker-log warning; a file badge would tell the user why citation contexts are
  missing for such papers.

# Workplan — Batch 10 (2026-07-10)

Five owner-requested features. Each is grounded against current code with file:line anchors.
Items **1**, **4**, and **5** carried a design fork — **RESOLVED by owner 2026-07-10** (see each item).

Sequencing: 3 → 4 → 2 → 5 → 1 (cheapest/most-isolated first; the multi-import staging
work is the large one and lands last so the smaller wins ship independently).

---

## Feature 1 — Multi-PDF import with extraction preview · **DECIDE (big one)**

**Ask:** import many PDFs at once, each → its own paper (unless a duplicate is detected).
Prefer to **extract before storing DB records**, show a **preview**, let the user choose which
records to create/accept, surface collisions (existing paper with same PDF / DOI / title).
One bad PDF must not fail the batch (skip it). Also an **"import directly"** mode: no preview,
create records immediately; every error/warning just skips that paper with a message.

**Current reality (why this is non-trivial):**
- `POST /imports/upload` (`backend/app/api/v1/endpoints/imports.py:208-269`) accepts exactly **one**
  file and immediately mints `File` + `Work` + `ImportBatch`, then enqueues **async** extraction.
- Extraction (`extract_and_store`, `backend/app/services/extraction.py:233-334`) is hard-wired to an
  **already-existing** `File`+`Work` via `FileWorkLink` (raises `ValueError` if absent, lines 249-254).
- Import-time dedup is **sha256 only**, at the `File` level (`_ensure_managed_file`,
  `backend/app/services/storage.py:446-513`): a byte-identical re-upload is silently deduped and no
  new `Work` is minted. Richer **DOI / arXiv / fuzzy-title** candidate detection exists
  (`backend/app/services/duplicate_detection.py:60-114, 166-244`) but only runs on a **manual** global
  scan, never on import.
- The reusable **record-free** primitives already exist: `GrobidClient.process_fulltext_document_sync`
  (path→TEI XML) and `parse_tei` → `ParsedPaper` (TEI→struct, no DB writes). The closest existing
  preview→commit UI template is citation-text `BatchImport.svelte` + `/imports/batch/preview` +
  `/imports/batch/commit` (`imports.py:479-536`).

### DECISION → which architecture?

**Option A — Staging model (true "extract before store", matches the ask). RECOMMENDED.**
New tables + a record-free extraction job:
- **Migration:** `ImportStagingBatch` (id, created_by, mode, status, counts) and `ImportStagingItem`
  (batch_id, filename, sha256, stored content-addressed path, probe status, extraction status,
  raw TEI, parsed-metadata JSON {title, authors, year, doi, venue, abstract}, duplicate-candidate JSON
  {existing work_ids + reason: same_pdf | same_doi | fuzzy_title}, error message, decision).
- **`POST /imports/upload-multi`** (multipart, N files): per file — content-type/magic/size checks +
  `probe_pdf_openable`; store PDF **content-addressed immediately** (dedup-safe, reused on commit);
  detect same-PDF collision up front; create a staging item; enqueue one **record-free extraction job**.
  Never fails the batch — a bad file becomes a `failed` item with a message.
- **Record-free extraction job:** GROBID fulltext → `parse_tei` → store TEI + parsed JSON on the item;
  compute DOI/title duplicate candidates now that metadata exists; set item `extracted`/`extract_failed`.
- **`GET /imports/staging/{batch_id}`:** batch + items (status, parsed metadata, warnings) for the preview.
- **`POST /imports/staging/{batch_id}/commit`:** body `[{item_id, action: accept|skip, merge_target?}]`.
  Accepted → mint `Work`+`File` (reuse the stored content-addressed file), apply the **stored TEI** via
  `store_parsed_extraction` (**no GROBID re-run**), enqueue enrichment. Return a per-item summary.
- **"Import directly"** = `upload-multi?auto_commit=true`: after extraction, auto-accept every item with
  no blocking error/collision; the rest are skipped with messages. Same pipeline, no manual gate.
- **Frontend:** rework the Import page's single-PDF card into a multi-file drop + a preview table
  (mirror `BatchImport.svelte`), with a "Preview" vs "Import directly" toggle; poll the staging batch;
  per-row accept/skip; commit. New client methods `uploadPdfsMulti`, `getStagingBatch`, `commitStaging`.

**Option B — Create-then-review (cheaper, ~40% the work, but stores records during preview).**
Loop the existing single-upload per file (records + async extraction created immediately), add a batch
review screen that shows each created paper's extraction status + sha256-dedup outcome, and let the user
**reject (=delete)** unwanted ones. "Import directly" = skip the review screen. Reuses almost everything;
**does not** satisfy "extract before storing records," and rejected papers briefly exist + emit
create/delete audit events.

> **RESOLVED → Option A (staging / extract-first).** Direct-import mode blocks (skips with a warning)
> on **same-PDF (sha256) OR matching normalized DOI**; fuzzy-title matches do **not** block (they still
> create a paper and are surfaced later by the normal duplicate scan). In preview mode all three signals
> are shown as warnings but the user decides per row.

---

## Feature 2 — Duplicates: fix "Loading preview…" + open both papers in paper view

**Files:** `frontend/src/pages/DuplicatesPage.svelte`, reusing `components/WorkDetail.svelte` + `Modal.svelte`.

**2a — "Loading preview…" that never resolves.** The line at `DuplicatesPage.svelte:287` shows
`mergeSummary(previews[c.id])`; `mergeSummary` returns `'Loading preview…'` while the value is `undefined`
(lines 81-88). `loadPreview` (lines 73-80) calls `client.getMergePreview` which passes **no timeout**
(`client.ts:1689-1696`) — a stalled request hangs on "Loading…" forever; and on any error it stores
`null`, which silently **hides** the line (no explanation).
- Fix: add a timeout/abort to `getMergePreview`; on error/timeout show an explicit
  "Preview unavailable — open the papers to compare" note instead of a bare vanishing line.

**2b — Open each paper in the paper view (the real ask).** Make **both** works of a duplicate pair
clickable to open the full `WorkDetail` in a `Modal`, so the user can inspect differences and pick the
merge target. Pattern already proven in `CitationSummaryPage.openInPaperView` (lines 117-124 + Modal at
564-576): `client.getWork(id)` → `<Modal wide><WorkDetail .../></Modal>`.
- Add `openInPaperView(workId)` + modal state to DuplicatesPage; render the base and source work labels
  (lines 271-296) as buttons; keep the existing `⇄` swap (`swap()`, lines 69-72) to choose which survives.

---

## Feature 3 — "Set metadata from best source" → add an "all" option · **easy**

**Files:** `frontend/src/pages/LibraryPage.svelte`, `frontend/src/api/client.ts`,
`backend/app/api/v1/endpoints/works.py`.

Today it's a batch action over selected rows for **one** field. The field picker
(`LibraryPage.svelte:824`) is hardcoded `['title','abstract','year','venue','doi']`; `batchApplyMetadata`
(546-556) sends a single `field` via `client.bulkApplyMetadata` (`client.ts:1463-1471`) →
`POST /works/bulk-apply-metadata` (`works.py:2462`), whose `BulkApplyMetadataRequest.field_name` is a
**single** string validated against `_PROMOTABLE_FIELDS` (`works.py:119, 2476-2480`).

- **Backend:** accept `field_name == "all"` as a sentinel that expands to all `_PROMOTABLE_FIELDS` and
  wraps the existing per-field promote loop (2493-2521). Per-field helpers (`_choose_best_assertion`,
  `_apply_assertion_to_work`) are already field-parameterized — reused as-is. Locked/`confirmed_fields`
  and no-assertion fields keep being skipped. Return per-field applied/skipped tallies.
- **Frontend:** add an **"All fields"** entry to the picker; `batchApplyMetadata` sends `"all"`; adjust the
  toast text (`LibraryPage.svelte:552`) for the all-fields case.

---

## Feature 4 — Authors in the "Details" panel · **DECIDE (small)**

**File:** `frontend/src/components/WorkDetail.svelte`.

There is **no `authors` column** on `Work` — authors live only as a `MetadataAssertion` with
`field_name == "authors"`. The Details panel (lines 999-1062) binds to real Work columns
(title/year/venue/doi/arxiv/abstract/reading_status). A ready-made display string already exists:
`searchedAuthors` (lines 108-113, rendered in the find-on-web header at 1436).

- **Recommended:** show authors as a **read-only** row in the Details panel using `searchedAuthors`.
  Zero backend change; matches "add the authors field to the details panel."
- **Editable** would require a new Work column (migration) or writing an `authors` assertion on save +
  extending the Details `save` path — meaningfully more work.

> **RESOLVED → Editable.** Implement without a schema change by treating authors as a **user-sourced
> `authors` MetadataAssertion**: editing writes/updates a `source_type=user` (manual) assertion, marks
> it canonical + confirmed (locked, per AGENTS rule #5 so external metadata can't silently overwrite it),
> and the Details panel reads it back via the existing `searchedAuthors`/field-review path. This reuses
> the metadata-assertion + `confirmed_fields` machinery rather than adding a `Work.authors` column.
> Confirm the exact write path against the existing manual-correction endpoint during implementation.

---

## Feature 5 — Library columns: file count, topics, badges, tags · **DECIDE (medium)**

**Files:** `backend/app/api/v1/endpoints/works.py` (`WorkRead` + `list_works`),
`frontend/src/lib/columns.ts`, `frontend/src/components/PaperTable.svelte`,
`frontend/src/api/client.ts` (`Work` type).

Columns are a registry (`columns.ts:35-45 LIBRARY_COLUMNS`) + a hard-coded render chain
(`PaperTable.svelte:117-161`); a new column needs **both** a registry entry and a render branch.
`normalizeColumnPrefs` (`columns.ts:92-135`) auto-appends new columns for existing users. There is a
**soft cap of 6** visible columns (`ColumnPicker.svelte SOFT_COLUMN_CAP`) — a warning only, not a block.

Data availability in the paginated list endpoint (`list_works`, `works.py:640-718`):
- **topics — already on `WorkRead`** (`works.py:209`). Column is purely frontend: registry entry +
  render branch (chips, reuse the keywords rendering). **No backend change.**
- **file count / tags / badges — NOT on `WorkRead`.** Add them via **batched** enrichment over the page's
  work ids (same pattern as `_batch_shelf_rack_refs`, `works.py:709`) — never per-row N+1:
  - **file_count:** one `GROUP BY` count over `FileWorkLink` for the page ids.
  - **tags:** one batched `TagLink` query (`entity_type='work'`, `entity_id IN (...)`) → `[{name,color}]`.
  - **badges:** aggregate per work from its files — `extracted`, `extraction failed`
    (any file `status=='extract_failed'`), `not extracted` (stub, `canonical_metadata_source=='agent_index_only'`),
    and text-layer quality (`poor`/`none`/`ocr_added`) from `File.text_layer_quality`.

> **RESOLVED → Include conflicts.** Add one batched `MetadataAssertion` query per page that flags works
> with an unresolved conflict (a field with ≥2 distinct un-confirmed values), rendered as a `conflicts`
> badge alongside the file-derived ones. All four new columns ship **hidden by default** (opt-in via the
> Columns picker) so they don't push existing users past the 6-column soft-cap warning.

Reusable badge rendering: **none shared** exists — badges are ad-hoc `.badge`/`.fstatus` CSS per page
(closest set: `JobsPage.svelte:236 + 467-490`). Plan adds a small local badge renderer in `PaperTable`.

---

## Cross-cutting (per AGENTS.md)

- Tests for every new service boundary: staging upload/preview/commit, `bulk-apply-metadata` "all",
  batched column enrichment, duplicate-preview open-in-paper-view (frontend). (`test_batch_import*.py`,
  `test_duplicates_api.py`, `test_library_sort_and_preferences.py` are the nearest existing homes.)
- Bulk/destructive actions emit audit events (staging commit, "import directly" skips, all-fields promote).
- Update `PROGRESS.md` + `CHANGELOG.md` + a `docs/agent_handoffs/` note; commit per logical chunk
  (`area: description`, no Co-Authored-By).

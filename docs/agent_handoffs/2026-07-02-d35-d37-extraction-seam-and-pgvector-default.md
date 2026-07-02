# Handoff: D35 drop dead ML-extraction seam + D37 pgvector on by default (2026-07-02)

## Task name
D35 — remove the dead Nougat/Marker extraction seam (keep only `ocrmypdf` + `pymupdf` OCR backends;
GROBID stays the structured TEI extractor). D37 — flip `pgvector_enabled` default False→True so a
registered real embedding model gets HNSW ANN search out of the box, with `hash_bow` still working.

## Commits (all on `main`)
1. `backend: drop dead Nougat/Marker extraction seam and full_ml OCR backend` — 53903a4
2. `tests: drop full_ml/nougat extraction tests; assert OCR backends + legacy degradation` — 76a8331
3. `frontend: remove full_ml/ML-extraction options from AI settings` — 8641960
4. `backend: enable pgvector by default so a real model gets ANN search` — 86a5bfa
5. (docs) this handoff + PROGRESS

## Files changed

**Commit 1 — D35 backend:**
- `backend/app/core/config.py` — removed the `extraction_backend` Setting and the
  `processing.advanced_extraction` YAML block (which mapped `nougat_enabled`/`marker_enabled` onto
  `ocr_backend="full_ml"`); updated the `ocr_backend` comment to `none|ocrmypdf|pymupdf`.
- `backend/app/services/ai_config.py` — `OCR_BACKENDS` now `("none","ocrmypdf","pymupdf")`;
  `get_ai_config` degrades any out-of-range stored `ocr_backend` (e.g. legacy `full_ml`) to
  `settings.ocr_backend`.
- `backend/app/services/model_management.py` — dropped `nougat_available`/`marker_available`
  detection and the `full_ml`/`nougat`/`marker` entries + `*_installed` flags from
  `detect_providers`; the `extraction` group now reports `none/ocrmypdf/pymupdf/grobid`.
- `backend/app/services/extraction.py` — removed the `elif ocr_backend == "full_ml"` route, the
  `hard_text`/`extra_text` enrichment plumbing (+ `_WEAK_BODY_THRESHOLD`), and the now-unused
  `contextlib` import. `store_parsed_extraction` lost its `extra_text` param.
- `backend/app/services/ocr.py` — removed `_ML_BACKEND_MODULES`, `ml_extraction_available`, and
  `run_ml_extraction`; updated the module docstring. `pymupdf_ocr`/`pymupdf_extract_text` stay
  (`pymupdf_extract_text` is still used by `files.py`).
- `backend/app/models/ai.py` — comment: `ocr_backend` is `none|ocrmypdf|pymupdf`.
- `backend/alembic/versions/0031_ai_config_ocr_backend.py` — docstring comment only.
- `backend/alembic/versions/0047_drop_full_ml_ocr_backend.py` — NEW migration (revises
  `0046_max_queue_len`): `UPDATE ai_config SET ocr_backend = NULL WHERE ocr_backend = 'full_ml'`.

**Commit 2 — D35 tests:**
- `backend/tests/test_config.py` — removed `test_settings_advanced_extraction_selects_full_ml`.
- `backend/tests/test_ocr.py` — removed the two `run_ml_extraction`/`ml_extraction_available` tests
  and the `run_ml_extraction` assertion in the native-text test; dropped the now-unused
  `import pytest`.
- `backend/tests/test_extraction.py` — removed `test_extract_full_ml_enriches_with_pymupdf_hard_text`.
- `backend/tests/test_ai_admin.py` — `detect_providers` assertions now check `pymupdf`/`grobid`
  present and `full_ml`/`nougat`/`marker` absent; `allowed.ocr_backend` == `{none,ocrmypdf,pymupdf}`.
  Added `test_ocr_backends_no_longer_include_full_ml` and
  `test_legacy_full_ml_ocr_backend_degrades_to_default`.

**Commit 3 — D35 frontend:**
- `frontend/src/components/AiModelsPanel.svelte` — `ocrBadge()` now handles `ocrmypdf`/`pymupdf`
  generically (no `full_ml` branch); removed the `ocrMlUnavailable` reactive + the ML-extraction
  install banner; updated the backend `<select>` title.
- `frontend/src/api/client.ts` — `ocr_backend` doc comment now `none|ocrmypdf|pymupdf`.
- `frontend/src/components/AiModelsPanel.test.ts` — `allowed`/`providers.extraction` fixtures use
  `pymupdf`+`grobid` (no `full_ml`); the ex-`full_ml`-guidance test now checks a `pymupdf`-missing
  backend shows the rebuild reason (via `findAllByText`, since the note renders in both the badge
  reason and the select hint) and no install/pip button.

**Commit 4 — D37:**
- `backend/app/core/config.py` — `pgvector_enabled` default `False → True` (+ comment).
- `backend/app/services/semantic_search.py` — `_pgvector_rank` returns `None` on an empty result
  (nothing mirrored into `vector_pg` yet) so the caller falls back to the JSON+Python path instead
  of reporting a spurious empty search.

## How hash_bow stays working with pgvector on
`_pgvector_on(db)` gates the ANN path on `pgvector_enabled AND dialect == 'postgresql'`, so on
SQLite (the whole unit suite) it is always False → JSON + Python-cosine, unchanged. On Postgres the
document-level `embeddings.vector_pg` column is an **unconstrained** `vector` type (migration 0019),
so hash_bow's 256-dim vectors are storable and rankable by `<=>` just like a real model's — both
write paths (`index_one_work`, `_store_embedding`) call `_write_pgvector`, so a fresh install
populates `vector_pg` from the first index. If a pre-existing install has vectors with `vector_pg`
NULL (indexed before the flag flip), `_pgvector_rank` now returns None on the empty result and the
JSON+Python fallback runs — correct results, no regression. The per-model HNSW chunk path
(`chunk_search`/`embedding_registry`) is independent of this flag (gated on a provisioned per-model
column) and hash_bow has no such column, so it is unaffected. `test_pgvector_ranking_when_enabled`
(pg-only) exercises hash_bow through the ANN path and stays green.

## How a legacy `full_ml` value degrades
Two layers: (1) `get_ai_config` resets `cfg.ocr_backend` to `settings.ocr_backend` (default
`ocrmypdf`) whenever the stored value is not in `OCR_BACKENDS` — covers `full_ml` and any other
stale enum, and never surfaces an out-of-range value to the extraction pipeline or the admin UI.
(2) Migration `0047` rewrites any stored `full_ml` to NULL (which already meant "use the Settings
default"). Covered by `test_legacy_full_ml_ocr_backend_degrades_to_default`.

## Tests added / skipped
Added: `test_ocr_backends_no_longer_include_full_ml`,
`test_legacy_full_ml_ocr_backend_degrades_to_default` (both in `test_ai_admin.py`). Removed the
`full_ml`/`nougat`/`marker` tests listed above. None skipped.

## Verification
- Full backend suite: `docker compose exec -T api python -m pytest backend/tests -q` — **720 passed**.
- Migration parity: `make test-migrations` — **4 passed** (chain applies through 0047,
  autogenerate-clean).
- Frontend: `make frontend-check` — green + build (vitest 88 passed, 1 skipped across 21 files).
- `ruff check backend agent && ruff format --check backend agent` — clean.
- `docker compose exec -T api python scripts/dump_openapi.py` — `backend/openapi.json` **unchanged**
  (the removed config fields are not part of the API schema; `ocr_backend` allowed-values are
  runtime data, not the OpenAPI surface).
- `scripts/check_secrets.py` — clean before each commit.

## Notes / deviations
- Committed D35 and D37 as separate slices even though both touch `backend/app/core/config.py`
  (the two edits were interleaved in one diff hunk): committed the D35 config changes with the
  pgvector line temporarily at its old value, then flipped it in the D37 commit — so each commit's
  config diff is scoped to its decision.
- Removed the `extra_text`/weak-GROBID-body keyword enrichment along with the `full_ml` route: it
  was only ever fed by `full_ml`, so it was dead once the route was dropped. Keyword extraction now
  runs on abstract + GROBID body only (as before `full_ml` existed).
- `docs/AUDIT.md` and `docs/DISCUSSIONS.md` left untouched.

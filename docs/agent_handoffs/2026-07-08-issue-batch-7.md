# Handoff — issue_batch_7 (2026-07-08)

**Task:** implement the "ready to build" items from `docs/WORKPLAN_2026-07-08_batch7.md` (triage doc,
intentionally uncommitted). 12 owner-reported issues; the buildable subset was shipped, the rest
deferred by owner decision.

## Deferred (not built — see the workplan for why)
- **4** agent "reconcile" — current code already matches by content hash; not reproducible. Revisit
  with repro steps + agent/server build info.
- **7** OCR-scanned papers show no text in the reader — bigger, its own task (quality classifier
  samples page 0 only; `save_derived_ocr_pdf` swallows errors; `--skip-text` semantics).
- **8 Tier 2** venue abbreviation/alias matching ("ICRA" ↔ full name) — separate future project.
- **5i** co-citation plain-string hash-on-hover — couldn't locate the rendering path statically
  (ruled out the shared missing-title fallback, the custom tooltip, and SVG-native tooltip; chart
  uses canvas). Needs a live inspector repro. Low priority.
- broader **1a** "extraction unstable" — only the confirmed `offer_teleport` root cause was fixed;
  no cleanup of pre-existing orphaned stubs (owner: "don't bother with the cleaning").

## Files changed (by commit)
- `agent: reuse index_only stub Work on agent-initiated teleport` — `backend/app/services/agent_files.py`, `backend/tests/test_agents.py` (1a)
- `extraction: skip duplicate identical metadata assertions` — `backend/app/services/extraction.py`, `metadata_enrichment.py`, `backend/tests/test_extraction.py`, `test_enrichment.py` (1b)
- `queue: deterministic per-work job ids` — `backend/app/workers/queue.py`, `backend/tests/test_queue.py` (1c)
- `jobs: handle duplicate-DOI IntegrityError` — `backend/app/workers/jobs.py`, `backend/tests/test_d7_extraction_recovery.py` (6)
- `extraction: mine primary-paper venue+year from TEI` — `backend/app/services/tei_parser.py`, `extraction.py`, `backend/tests/test_extraction.py`, `backend/tests/fixtures/minimal_grobid_tei.xml` (11)
- `viz: keyword-sim axis, venue/year colour, year size, ref-graph venue/doi` — `backend/app/services/visualization.py`, `reference_graph.py`, `backend/tests/test_visualization.py`, `test_reference_graph.py` (5b/5d/5h/5j)
- `api: find-on-web apply-metadata, bulk best-source, batch keyword/topic` — `backend/app/api/v1/endpoints/works.py`, `ai_admin.py`, `backend/app/services/metadata_enrichment.py`, `backend/tests/test_web_find_api.py`, `test_metadata_assertion_delete.py`, `test_ai_admin.py` (9/3/12)
- `paper view: staleness guard, apply-metadata, clickable refs` — `frontend/src/components/WorkDetail.svelte`, `frontend/src/api/client.ts`, `WorkDetail.findweb.test.ts`, `WorkDetail.citations.test.ts` (2/9/10)
- `library: bulk-action dropdown + Go` — `frontend/src/pages/LibraryPage.svelte`, `client.ts`, `LibraryPage.batch.test.ts` (3)
- `viz(frontend): lanes, confine, venue colour, ticks, ranges, year, click-to-import` — `VisualizationsPage.svelte`, `temporalMap.ts`, `embeddingCluster.ts`, `referenceGraph.ts` (+test), `ReferenceGraphModal.svelte`, `BatchImport.svelte`, `selection.ts`, `client.ts` (5a/5c/5d/5e/5f/5g)

## New API surface
- `POST /works/{id}/find-on-web/apply-metadata` → `list[FieldReview]` (contributor).
- `POST /works/bulk-apply-metadata` `{work_ids, field_name}` → `{applied, skipped}` (contributor).
- `GET /admin/ai/keyword-topic-status`; `POST /admin/ai/keywords/batch`, `/admin/ai/topics/batch`
  `{scope: "all"|"missing"}` (admin/owner).

## Assumptions / decisions worth knowing
- **1a** mirrors `complete_teleport` exactly (does not set `AgentFile.work_id` when creating a fresh
  Work), to stay consistent with the reviewed path.
- **6** narrows the catch to `orig.diag.constraint_name == "uq_works_doi"` so other constraint
  violations still surface. `uq_works_doi` is a **migration-only** partial unique index (not on the
  model), so the DOI-conflict path can't be exercised end-to-end under SQLite — tests inject a
  synthetic `IntegrityError`.
- **9** uses source `web_find:<source>` which is *not* in `TRUSTED_SOURCES`, so applied values stay
  non-canonical candidates (user promotes). doi stays a candidate; only arXiv id is backfilled
  (it has no metadata-review row).
- **3** bulk "set metadata" skips fields already user-confirmed (locked) to honor the no-silent-
  overwrite rule; tie-break: GROBID > current canonical > most-recent.
- **5f** min/max is a pure ECharts view-range override (merge setOption, `null` = auto), temporal
  map only — no backend/data change; does not filter data.
- **12** "topics" = per-paper `Work.topics` terms (existing RQ job), NOT the scope-wide
  `TopicAssignment` clustering (`topic_model_job` is still a stub).

## Tests
Added/updated: `test_agents` (offer-teleport stub reuse), `test_extraction` (assertion dedup +
venue/year), `test_enrichment` (row-identity idempotence), `test_queue` (deterministic ids),
`test_d7_extraction_recovery` (DOI-conflict handling), `test_visualization` (kw axis, year size,
venue/year colour), `test_reference_graph` (venue/doi in payload), `test_web_find_api`
(apply-metadata), `test_metadata_assertion_delete` (bulk best-source), `test_ai_admin` (batch
kw/topic + status). Frontend: `WorkDetail.findweb`/`.citations`, `LibraryPage.batch`,
`referenceGraph` (lanes/confine/formatter/venue).

## Security implications
None new. All new endpoints keep the existing gates (contributor floor + `_guard_modify_work` for
work mutations; admin for AI batch). Find-on-web egress unchanged (apply-metadata takes the
candidate values from the request body — no new fetch). Bulk metadata respects field locks.

## Verification
Backend fast tier (`make test-api`) green; touched slow suites (`test_extraction`,
`test_visualization`, `test_reference_graph`) green; `ruff check`/`format` clean; full frontend
suite (233 passed, 4 skipped) + `npm run build` green. **Nothing pushed** (commit-only per repo
rules). Before pushing: run `make ready-full` / `make ci` (full tier incl. migrations + e2e).

## Next recommended
Get owner repro for **4** and **7**; decide **8 Tier 2** scope; live-repro **5i**.

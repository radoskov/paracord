# Handoff — issue_batch_6 §B build-out (all 8 items) (2026-07-08)

**Task:** implement the eight "needs discussion" items from `issue_batch_6.md` after validating each
decision with the owner. Working docs (uncommitted, per owner): `issue_batch_6.md`,
`docs/WORKPLAN_2026-07-07_batch6.md`, `docs/PAPER_REFERENCE_GRAPH_DESIGN.md`,
`docs/B6_SPEC_index_only_stub_and_agent_help.md`. All commits are local on `main`; **nothing pushed**.

## Commits (in build order)
- `02a73a2` B8 — paper-view Summarise/Regenerate (+ `summary_type='auto'`).
- `ce6d149` B5 — lexical index self-refresh + Rebuild button.
- `081852c`/`4e0d977`/`e6f970d`/… (earlier round: 2a/1e/1g/1b) — see prior handoff.
- `40677b7` B2 — reindex-vs-no-PDF split + openable list.
- `71e95b9` B3 — temporal-map edges default ON + configurable edge limit.
- `582b5d8` B4 — overlap count-badge markers + enterable tooltip.
- `04f50aa` B1 — visualization help (description + About popup + types overview).
- `4832fd4` B6 backend — index_only → server paper stub (migration `0054_agentfile_work_id`).
- `3be5015` B6 agent — create-stubs toggle, reconcile server-deleted stubs, Help tab.
- `0ab853d` B6 frontend — "not extracted" stub badge.
- `8d4b785` B7 backend — `/works/{id}/reference-graph` + section classifier.
- `d7fd2d5` B7 frontend — reference-graph modal + Profile section weights.

## Assumptions / decisions worth noting
- **B6 stub marker** uses `Work.canonical_metadata_source == "agent_index_only"` (Work has no
  `source` column — that's on `WorkVersion`); extract/teleport clear it. `AgentFile.work_id` links
  the stub; `delete_work` deletes the linked `AgentFile` so a deleted stub leaves the server view and
  reverse-sync Reconcile un-indexes it locally. The agent reconcile treats `index_only` rows as
  server-known **only when the create-stubs toggle is on** (matches the Batch-A "never drop purely
  local" guarantee when off).
- **B7 weights are applied client-side** from the per-user preferences blob
  (`citation_section_weights`), so a Profile weight change re-sizes the graph with no server recompute.
  The endpoint returns raw per-section mention counts.
- **B7 v1 scope:** Y axis is the section-weighted mention count only. The design's *selectable* Y
  (topic similarity / citation count) is a deliberate follow-up — it needs the endpoint to also emit
  per-node citation_count/topics (external refs would sit on an "n/a" lane). Flagged in PROGRESS.

## Tests added
- Backend: `test_summarization` (auto), `test_bm25_index`/`test_ai_admin` (cache_info + rebuild),
  `test_visualization` (reindex_hint + edge limit), `test_agents` (stub create/idempotent/delete),
  `test_reference_graph` (classifier + endpoint + ref→ref edges).
- Frontend: `WorkDetail.summary` (Summarise + stub badge), `AiModelsPanel` (Rebuild), `temporalMap`
  (year axis, ordering, overlap), `vizHelp`, `referenceGraph`, `LibraryPage.refresh` (prior round).

## Migrations / images
- `0054_agentfile_work_id` applied to the live compose Postgres. api/worker/frontend images were
  rebuilt in the prior (ready-full) round (pgvector + echarts 6); no further rebuild needed for these
  code-only changes (bind-mounted).

## Security implications
None new. All new endpoints are SEE/role-gated and read-only except the stub creation (agent-token
gated, owner-scoped) and the admin-only lexical rebuild. Agent prune/reconcile semantics unchanged
except the documented stub-eligibility widening.

## Verification
Per-item gate after each commit; final full gate = bare `pytest` (incl. `@safety`) +
`make frontend-check` + `make e2e`. **Next recommended:** owner review, then push (with approval) and
watch CI; consider the B7 selectable-Y follow-up.

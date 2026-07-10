# Handoff — issue_batch_9 (2026-07-10)

**Task:** implement the 4 owner-reported items in `docs/WORKPLAN_2026-07-10_batch9.md` (triage doc,
intentionally uncommitted). All 4 built; owner decisions on the two forks are folded in.

## Files changed (by commit)
- `frontend: count only attached/deduped as downloaded in find-on-web` —
  `frontend/src/components/WorkDetail.svelte`, `frontend/src/components/WorkDetail.findweb.test.ts`
  (issue 3)
- `frontend: enlarge + glow the Jobs nav semaphore, distinguish green/blue` —
  `frontend/src/App.svelte` (issue 2a)
- `frontend: harden the Jobs tab against freeze / stuck-loading` —
  `frontend/src/pages/JobsPage.svelte`, `frontend/src/pages/JobsPage.test.ts`,
  `frontend/src/api/client.ts` (`getJobs` 15s timeout) (issue 2b)
- `backend: move a PDF between papers + merge arbitrary papers` —
  `backend/app/api/v1/endpoints/works.py`, `backend/openapi.json`,
  `backend/tests/test_work_file_move_and_merge.py` (new) (issue 4, backend)
- `frontend: Move-file and Merge-paper UI in WorkDetail` — `frontend/src/api/client.ts`,
  `frontend/src/components/WorkPicker.svelte` (new), `frontend/src/components/WorkDetail.svelte`,
  `frontend/src/components/WorkDetail.merge.test.ts` (issue 4, frontend)
- `agent,backend: content-aware reconcile so deleting a duplicate keeps the file` —
  `backend/app/api/v1/endpoints/agents.py`, `backend/openapi.json`, `backend/tests/test_agents.py`,
  `agent/paperracks_agent/agent_ops.py`, `agent/paperracks_agent/client.py`,
  `agent/tests/test_agent_ops.py` (issue 1)

## Owner decisions folded in
- **Issue 1 (reconcile):** chose **content-aware diff** over guard-and-warn or auto-sync. reconcile
  cross-checks candidate hashes against the new `POST /agents/files/known-hashes` (File exists AND is
  linked to a paper) and drops any whose content survives — so deleting a duplicate *record* no
  longer un-indexes the still-present local file. Purely-index_only content (no `File` row) is still
  flagged (acknowledged limitation). Degrades to the raw server-view diff if the endpoint is
  unavailable / the call errors (never *more* aggressive than before).
- **Issue 4:** owner asked for **both** move-file and merge (since the backend `merge_works` already
  existed). Move re-points one `FileWorkLink`; merge exposes `merge_works` for any two papers (not
  only duplicate-scan candidates) and is reversible via the existing `/unmerge`.

## Assumptions / decisions worth knowing
- **Move-file semantics:** "move", not "copy" (owner's preference). If the file is already linked to
  the target the source link is just dropped; the source's `main_file_id` is cleared if it pointed at
  the moved file, and the target adopts it as `main_file_id` only if it had none. The work-specific
  `version_id` on the link is cleared; the file-scoped `segment_id` is kept.
- **Merge permission:** requires modify rights on *both* papers (`_guard_modify_work` on each). The
  arbitrary-merge preview mirrors the duplicate-scan `MergePreview` shape (files moved, fields
  filled/conflicting, incoming refs, flatten warning).
- **Jobs freeze:** the most likely real-world trigger was an unguarded `status.counts[...]` /
  `status.jobs.length` render throw on a partial payload; that is now impossible (payload normalised
  to always-safe shapes). Kept `status` on refresh error so the tab never blanks back to "Loading…".
- **Semaphore colours:** lightened via `color-mix(... white)` scoped to the `.jobs-dot` classes — no
  global theme-token change (so `theme.test.ts` and every theme are untouched). Eyeball-and-iterate;
  ask if the exact shades want tweaking.
- **known-hashes leak surface:** the endpoint only confirms the server holds content the agent
  already has locally (it supplies the hashes), so it reveals nothing new to that agent.

## Verification
- Backend `test_work_file_move_and_merge.py` (8) + `test_agents.py` (32, incl. 2 new) +
  related suites green; agent `test_agent_ops.py` (31, incl. 2 new) + full agent suite (69) green;
  frontend full vitest 248 passed / 4 skipped; `frontend build` OK; `openapi.json` regenerated.
- **Not pushed** — awaiting owner review (standing rule: never push without approval).

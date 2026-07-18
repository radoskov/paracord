# Handoff: AI panel job spinners never stopped — poll by id

Owner report: after pull/mount/unmount, the panel spinner stuck forever ("Pull started" / "Mounting
started", pull stuck at "100% — 0 MB / 0 MB"), even though the Jobs tab showed the job **finished** and
the result was correct after a manual Refresh.

## Root cause

`pollPull` / `pollModelJob` polled the whole Jobs list (`getJobs(50)`) and searched it for the job by
id: `q.jobs.find(j => j.id === jobId)`. `queue_status(limit)` slices each RQ registry to `limit`
entries, so once enough jobs have accumulated on a machine the just-finished job falls outside the
returned window → `find` returns `undefined` → the loop never sees a terminal status → it spins
forever and never runs its on-finish refresh (so the model list / mounted state only updated on a
manual Refresh). The `0 MB / 0 MB @ 100%` was just the last tiny-layer progress event left frozen
because the finish was never detected.

## Fix

Poll the job **by id** instead of scanning the capped list — `GET /jobs/{id}/result`
(`fetch_job_result`) fetches the exact RQ job, so the terminal state is always detected regardless of
how many other jobs exist.

- **backend** (`workers/queue.py`): `fetch_job_result` now also returns `progress_done`/
  `progress_total` from the job meta (reads meta with `refresh=True`), so a single by-id poll gives
  both the terminal state AND live pull progress. These jobs set no `requester`, so the endpoint's
  requester-gating doesn't apply (and the panel is admin-only anyway).
- **frontend** (`client.ts`): `getJobResult` return type gains the optional progress fields.
- **frontend** (`AiModelsPanel.svelte`): `pollPull` and `pollModelJob` now call
  `client.getJobResult(jobId)` each tick; they stop on `finished` / `failed` / `missing`
  (`missing` = finished + expired from Redis → treat as done + refresh). Pull progress + the failure
  error text come from the same call.

## Verification

`make frontend-test` — 342 passed (mount test now mocks `getJobResult`). Backend job/result tests —
8 passed; api imports clean. Dev server healed + warm.

## Note

This also explains why it "worked relatively nice" on the fresh machine (few accumulated jobs → the
finished job was still within the list window) but not on the machine that had run many jobs.

# Handoff: mount/unmount robustness + GPU/CPU control

Second round on the mount/unmount feature (`a04b576`), addressing the owner's field reports: flaky
unmount, tab freezing on a big load, a model that couldn't be unmounted (needed `make down-ai`), no
auto-refresh, a "phantom remount", plus a request for CPU/GPU control + placement reporting.

## What was wrong

- **Synchronous mount blocked everything.** The mount endpoint loaded the model inline (up to minutes),
  holding an API worker and keeping the daemon busy → the tab showed "Loading…" with no cards.
- **Unmount could get stuck.** `unmount_model` used the modality of the *guessed* kind; a wrong guess
  (e.g. an embedding model unmounted via `/api/generate`) errored and left the model loaded.
- **No auto-refresh.** The semaphore + loaded list only updated on manual Refresh.
- **"Phantom remount"** wasn't a bug: searching with an Ollama embedding model must embed the *query*
  with that model, so the daemon auto-loads it (5-min TTL). It showed in the loaded list looking like
  a self-mount. Stored embeddings are for documents; the query still needs embedding.

## Changes (committed, tested)

- **Mount/unmount are background jobs** (`mount_model_job`/`unmount_model_job` in `workers/jobs.py`;
  `enqueue_model_mount`/`enqueue_model_unmount` + `MOUNT_MODEL_JOB`/`UNMOUNT_MODEL_JOB` +
  `model-mount`/`model-unmount` labels in `workers/queue.py`). Endpoints now enqueue + return 202 with
  a `job_id`; the config flip happens *in the job after the load succeeds*, so a failed mount leaves
  the prior selection intact. The frontend polls the job (never blocks).
- **Robust unmount** (`model_management.unmount_model`): tries the expected modality then the other
  (`/api/generate` ⇄ `/api/embed`) with `keep_alive:0`, so a wrong kind can't leave a model stuck.
- **GPU/CPU control**: `mount_model(..., compute)` maps auto→(no override) / gpu→`num_gpu:999` /
  cpu→`num_gpu:0` into the load request's `options`. `MountRef.compute` on the endpoint; a "Compute
  for next mount" selector in the panel.
- **Placement reporting**: the loaded list shows GPU (VRAM) vs CPU (RAM) from `/api/ps` `size_vram`;
  after a GPU mount that lands on CPU, the panel explains the likely cause (container has no GPU
  access). `size_vram==0` ⇒ CPU.
- **Pinned vs auto**: loaded rows show `mounted` (pinned, `keep_alive:-1`, far-future `expires_at`)
  vs `auto · frees in ~Nm` (transient request-load) — killing the "phantom remount" confusion.
- **Auto-refresh**: an 8 s poll of reachability + loaded while the tab is visible, plus an immediate
  re-check on `visibilitychange`/`focus`. Deliberately does not touch `config` (no clobbering edits).
- **Snappier status**: metadata reads (`_ollama_tags`, `ollama_version`, `list_loaded`) dropped 5s→3s
  so the tab never hangs on a busy daemon.

## Verification

- Backend `test_ai_admin.py` (mount/unmount **endpoints** enqueue + validate; mount/unmount **jobs**
  select/free-previous/revert-to-baseline via a SessionLocal reused onto the test DB) — 33 passed.
  Fast tier: <see PROGRESS>.
- Frontend `make frontend-test` — 341 passed (mount test now asserts the job enqueue with compute).
- Live: worker imports the new jobs; endpoints/service import; `_COMPUTE_NUM_GPU` maps as expected.
  Worker restarted (registers the new jobs); dev server healed + warm.

## Notes

- Mount job timeout 1800s; a truly huge model on slow disk could still exceed it (rare).
- The semaphore is still api-side (see the prior handoff); a worker→Redis heartbeat remains the
  fully-robust follow-up.
- Deferred to the next commit: clearer "Registered embedding models"/"Embedding index" copy, tooltips
  everywhere, and a Help modal.

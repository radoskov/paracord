# Handoff: worker OLLAMA_URL fix + Ollama reachability semaphore

Fixes the *real* cause of the owner's persistent model-pull failure and adds a debugging aid. Follows
the pull-progress/search (`f425eea`) and mount/unmount (`a04b576`) chunks.

## Root cause (finally)

The improved error message from `f425eea` exposed it: the pull job raised
`Ollama daemon unreachable at http://localhost:11434`. Pulls run in the **worker** container, where
`localhost` is the worker itself — not Ollama.

- `.env` sets `OLLAMA_URL=http://localhost:11434` (a sensible default for host-run tooling).
- Both `api` and `worker` load it via `env_file: .env`.
- The **api** service overrides it with `OLLAMA_URL: "http://ollama:11434"`; the **worker** service
  did **not** → worker kept `localhost`.
- `ai_config.ollama_url` is NULL, so `get_ai_config` falls back to each container's env. Result: api
  resolved `http://ollama:11434` (so the UI's "reachable ✓" was green), worker resolved
  `http://localhost:11434` (so every pull/embed in the worker failed with connection-refused).

This is why it "worked on another machine" — that host either ran Ollama on localhost or had the
worker env set right.

## Fix

- **`docker-compose.yml`**: added `OLLAMA_URL: "http://ollama:11434"` to the **worker** service's
  `environment` (mirrors the api). Recreated the worker (`docker compose up -d worker`); it now
  resolves `http://ollama:11434` and reaches the daemon (v0.31.1). Verified.
- Left `.env` alone (localhost is the correct default for running tooling on the host).

## Semaphore (owner's request)

- **Backend**: `ollama_version(url)` in `model_management.py`; `ai_status` now returns `ollama_version`
  (only when reachable).
- **Frontend** (`AiModelsPanel.svelte`): replaced the plain "Ollama: reachable ✓" text with a
  green/red dot (same style as the Jobs-tab nav dot) + label; tooltip shows the URL + version when
  reachable, and when red explicitly notes that pulls/embeds run in the worker, which must also reach
  the URL — pointing at exactly this class of bug. `AiStatus.ollama_version` type added.

## Caveat / follow-up

The semaphore reflects reachability from the **api** container. The bug we just fixed was an api/worker
URL divergence, which a pure api-side check can't detect. Now that the DB config drives both (and the
worker env is corrected) they agree, but a future robust improvement would be a worker heartbeat
(worker pings Ollama, writes result to Redis, api surfaces it) so the dot reflects the pull path.

## Verification

Backend `test_ai_admin.py` 33 passed; `make frontend-test` 341 passed. Live: worker resolves
`http://ollama:11434`, reachable, v0.31.1. Dev server healed + warm; worker recreated.

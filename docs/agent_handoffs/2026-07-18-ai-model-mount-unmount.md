# Handoff: AI model mount/unmount (live VRAM control)

Admin → AI & Models panel. Owner's 4th ask: mount/unmount a model live so a big model isn't stuck in
VRAM (previously needed an SSH `docker` kill, which took all LLM features down for everyone). Built on
Ollama's `keep_alive` API. Follows the pull-progress/search chunk (`f425eea`).

## Decisions (owner, 2026-07-18)

- **Mount = load + make active**: pin in memory (`keep_alive:-1`) AND select it as the active model
  for its capability. **Unmount = release + baseline**: unload (`keep_alive:0`) AND drop that
  capability to its built-in baseline (hash-BOW / extractive) so features keep working, never error.
- **VRAM warning = admin-set budget**: a `vram_budget_gb` field in AI config; the mount confirm warns
  when `estimate + already-loaded > budget`. (Ollama reports no total VRAM; nvidia-smi isn't reachable
  from the app — confirmed CPU-only on this dev box.)

## Landed (committed, tested)

- **Config + migration**: `AIConfig.vram_budget_gb` (Float, nullable) + `0080_ai_config_vram_budget`
  (parity green, applied to live DB 0079→0080). `EffectiveAIConfig`/`EDITABLE_FIELDS`/`_validate`
  (non-negative) updated.
- **Runtime** (`services/model_management.py`): `list_loaded` (GET /api/ps → name, size, size_vram,
  expires_at), `mount_model`/`unmount_model` (`_keep_alive` posts to `/api/embed` for embedding kinds,
  `/api/generate` for LLMs; actionable RuntimeErrors). `list_models` now carries an estimated `vram_gb`
  (from the tag's `parameter_size`/`quantization_level`, else the name).
- **Endpoints** (`ai_admin.py`): `GET /ai/loaded` (loaded + budget); `POST /ai/models/mount`
  (load → set config → free previous of kind → reindex if embedding changed); `POST /ai/models/unmount`
  (unload → revert to baseline **only if it was the active model of that kind**). Owner-gated.
- **Frontend** (`AiModelsPanel.svelte`, `client.ts`): Mount/Unmount buttons on the Semantic-search
  and Scope-summaries cards; a "Memory budget (GB)" input; a "Loaded in memory" section (per-model
  VRAM + Unmount, total vs budget). Mount/unmount confirm dialogs warn on (a) VRAM budget overflow,
  (b) running/queued AI jobs (`getJobs`, matched by task label). `LoadedModel` type + `getLoadedModels`
  /`mountAiModel`/`unmountAiModel`.

## Design notes

- **One model per kind** enforced by us: mount repoints the capability's config and frees the
  previously-active model of that kind from memory (best-effort). Ollama may still hold others; we
  never pin more than one per capability.
- **Reindex**: mounting a *different* embedding model queues a reindex (its vectors must match) —
  identical to changing the dropdown + Save; the response returns `reindex_job_id`. The multi-embedding
  registry (#21) means already-indexed models coexist, so switching between them is cheap.
- **Graceful live unmount** leans on the pre-existing baseline-fallback architecture (search→hash-BOW,
  summary→extractive) — the "features must handle live mount/unmount" requirement was largely already
  met; unmount just flips config to the baseline.
- **CPU hosts**: `size_vram` is 0, so the loaded list shows resident RAM size instead.

## Verification

- Backend: `test_ai_admin.py` (mount selects + one-per-kind frees previous; embedding mount → reindex;
  unmount → baseline; bad kind/provider → 400; loaded owner-only; budget persists + rejects negative),
  `test_model_catalog.py`, `test_migration_parity.py` (4 passed). Fast tier: <see PROGRESS>.
- Frontend: `make frontend-test` — 341 passed (+2 mount tests). Dev server healed + warm; worker
  restarted (picks up chunk-1's streaming `pull_model_job`).
- Live: routes registered; `list_loaded`/`list_models`/`vram_budget_gb` resolve in-container against
  the real Ollama (0.31.1, currently nothing loaded / no models pulled).

## Notes / follow-ups

- Mounting is synchronous (generous 600s timeout); loading a very large model off slow disk blocks the
  request. If that bites, promote mount to a background job like pull.
- No e2e journey added for mount (needs a real Ollama + GPU); covered by unit/component tests.

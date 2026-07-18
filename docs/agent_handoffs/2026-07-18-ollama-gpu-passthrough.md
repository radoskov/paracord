# Handoff: Ollama GPU passthrough

Owner reported the machine has a powerful GPU yet Ollama ran on CPU. Diagnosis + fix.

## Diagnosis

Host is fully GPU-capable — the compose file just never requested the GPU:
- Host: **RTX 3090 Ti (24 GB)**, driver 595.58.03; **NVIDIA Container Toolkit 1.17.8**; Docker
  `Runtimes: … nvidia runc` (but `Default Runtime: runc`).
- The `ollama` service in `docker-compose.yml` had **no GPU reservation** → started under `runc` with
  no GPU → Ollama fell back to CPU (`/api/ps` showed `size_vram: 0`; embeddings/recommend were slow,
  ~2m20s per recommend run).

## Fix

- **`docker-compose.gpu.yml`** (new): overlays the `ollama` service with
  `deploy.resources.reservations.devices` (driver nvidia, count all, capabilities [gpu]).
- **Makefile**: `COMPOSE` now appends the overlay when `OLLAMA_GPU=1`, so **every** compose target
  (up-ai, up-all, ai-update, restart, …) keeps Ollama on the GPU consistently — no footgun where a
  later plain `up` reverts it to CPU. Default (unset) stays CPU → portable to GPU-less hosts (the base
  compose is unchanged and still works where there's no GPU/toolkit).
- **Help dialog**: the GPU/CPU note now points at `export OLLAMA_GPU=1` + `make up-ai` +
  `docker-compose.gpu.yml`.

## How the owner enables it (persistent)

```
export OLLAMA_GPU=1        # add to shell profile on a GPU host
make up-ai                 # recreates ollama on the GPU
```

## Verification

- `docker compose exec ollama nvidia-smi` → sees the RTX 3090 Ti.
- After one embed, `/api/ps` → `size_vram == size` (5.78 GB) = **fully on GPU**.
- `embed_work_job` dropped from minutes to **0.67–5.9 s**.
- `make frontend-test` 342 passed.

## Note on e2e Journey 41

Still times out on this box even on GPU — because the live config uses `summary_provider=local_llm`
(`qwen3.5:4b`), so recommend runs the **LLM ranker** (per-paper generation, ~1m20s–2m on GPU), not the
fast embedding fallback the test assumes. Config/environment condition, not a regression; passes in CI
(extractive default) and with the baseline provider. Left as-is.

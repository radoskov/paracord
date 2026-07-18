# Handoff: AI model management — pull progress, error surfacing, search + VRAM

Admin → AI & Models panel (`AiModelsPanel.svelte` / `ai_admin.py` / `model_management.py`). Addresses
the owner's report that a model pull failed with only an uninformative "Pull failed ✗ (job …)", plus
requests for a download progress bar, better error reporting, and a model search with VRAM estimates.

## Diagnosis (root cause)

- The `qwen3:4b` pull failed with a **transient** `httpx.ConnectError [Errno 111]` — the Ollama
  daemon was momentarily unreachable at `http://ollama:11434`. It's reachable now and the same pull
  succeeds. The real defect was that the **UI never surfaced the reason**.
- **Delete already existed** (Delete button per model + `DELETE /ai/models`). The owner saw no delete
  option because the container Ollama had **zero models** (`/api/tags` → `[]`) — an empty list renders
  no rows/buttons. Once a model is pulled it appears with a Delete button.

## Landed (committed, tested)

- **Streaming pull + clean errors** (`model_management.py`): `pull_model` now takes `on_progress`
  and streams Ollama `/api/pull` (`stream:true`) via `_pull_ollama`, relaying `(completed, total,
  status)`; daemon-reported errors and transport failures become **actionable** `RuntimeError`s
  ("Ollama daemon unreachable at … — is the ollama service running? (make up-ai)"). `pull_model_job`
  passes `job_report_progress` as the callback — progress + error already flow through
  `queue_status` (`progress_done/total`, `error`) which the Jobs API returns.
- **Model catalog + search** (`services/model_catalog.py`, NEW): curated catalog of popular models
  (params, quant, size, kind, popularity, blurb) + `estimate_vram_gb` (quantized weights + KV/context
  + overhead) + best-effort `ollama.com` scrape that **falls back to the catalog on any failure**
  (the hybrid the owner picked). `GET /ai/models/search?q=` returns popularity-ranked results, each
  with a VRAM estimate, marking already-pulled models. `test_model_catalog.py` + 2 endpoint tests.
- **Frontend** (`AiModelsPanel.svelte`, `client.ts`): `pollPull` now renders a real `<progress>` bar
  (bytes → %/MB) and shows the **actual error text** on failure (was just "✗"); a "Find a model"
  search box → results table (name, type, size, est. VRAM, popularity bar, Pull/pulled✓); empty-models
  copy now points at the per-model Delete button. `CatalogModel` type + `searchAiModels`.

## Design notes

- VRAM is a **conservative estimate** (Ollama reports none): `params_b × bytes/param(quant) + KV
  (0.12×params×ctx/4096) + 0.8 GB overhead`, rounded to 0.1 GB. qwen3:4b Q4_K_M ≈ 3.5 GB. Labeled a
  sizing guide, not a guarantee, in the UI.
- Search matches name **+ family + blurb** (so `bge-m3` matches "embed" via its blurb). Scraped-only
  hits get params guessed from the `<n>b` name token; size/VRAM unknown until pulled.
- No new DB/migration. Owner/admin-gated (`ADMIN_DEP`).

## Verification

- Backend: `test_model_catalog.py` (6) + `test_ai_admin.py` (incl. 2 new search tests) — 33 passed
  (via `compose run --no-deps --entrypoint python api -m pytest`, bypassing the migrating entrypoint).
- Frontend: `make frontend-test` — 339 passed. Dev server healed (`.vite` cleared + restart + warm).
- Live: container Ollama reachable (0.31.1); streaming `qwen3:4b` pull confirmed downloading; search
  route registered in OpenAPI; catalog/VRAM/pulled-flag verified in-container.

## Next (requested, NOT yet built)

- **Live mount/unmount of models** for VRAM control (owner's 4th ask): pin via Ollama `keep_alive:-1`,
  unload via `keep_alive:0`, list loaded via `/api/ps`; one model per kind (summary/embedding);
  warn on VRAM pressure + on running AI jobs that a remount would cancel. See the next workplan.

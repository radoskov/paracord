# Handoff — Frontend + Infra audit batch (D16, D17, D29, D2, D5, D24, D4)

**Date:** 2026-07-03
**Branch:** committed directly on `main` (not pushed).

## Task

Batch of frontend + infra/ops audit fixes from `docs/AUDIT.md`: D16, D17, D29 (frontend),
D2, D5, D24, D4 (infra/ops). Stack left healthy for follow-on work.

## Commits (on `main`, oldest→newest)

| Hash | Message |
|------|---------|
| f01c7fe | frontend: run library batch actions with bounded concurrency (D16) |
| 63da6c9 | frontend: filter citation graph via show/hide without relayout (D17) |
| 7120d07 | frontend: add CSP and security headers to nginx (D2) |
| 0c81be0 | ops: generate a random POSTGRES_PASSWORD on make init (D5) |
| 7a5ffa7 | backend: compile a hash-pinned lock and install from it; bump httpx2 to 2.5.0 (D24) |
| 4c9cff4 | infra: run api, worker, agent, and frontend containers as non-root (D4) |

## Files changed per item

- **D16** — `frontend/src/pages/LibraryPage.svelte`: added a `runBatched()` helper (chunked
  `Promise.allSettled`, concurrency 6) and rewired `batchDelete` / `batchReextract` /
  `batchSetStatus` / `batchPutInto` off serial `await`-in-a-loop. Failures are counted and surfaced
  in the status message (e.g. "Deleted 98 paper(s); 2 failed."); a single `loadWorks()` refresh is
  still done once at the end. `batchReextract` now gathers file lists concurrently, then queues
  extraction concurrently (was N×M serial).
- **D17** — `frontend/src/components/CitationGraph.svelte`: `filteredElements()` replaced by
  `applyFilters()` which shows/hides elements on the live Cytoscape instance
  (`node.style('display', …)`), no rebuild. Edges now carry a stable `e{index}` id so they can be
  toggled. `renderGraph()` builds the full element set once and lays out only the visible subset;
  `relayout()` runs on `cy.elements(':visible')`. The reactive block was split: **rebuild+layout
  only** on data/render-surface change (`rNodes`/`rEdges`/`renderMode`/`cyContainer`); a **filter
  toggle** (`hideSingletons`/`hideExternalLeaves`) only calls `applyFilters()`. Re-layout on a
  filter toggle is gone; explicit re-layout is the existing layout `<select>` (`on:change=relayout`).
- **D29** — no code change (see findings below).
- **D2** — `frontend/nginx.conf`: added the four security headers at server scope (with `always`)
  and redeclared them in the hashed-asset `location` block (an `add_header` there otherwise
  suppresses inherited headers).
- **D5** — `Makefile` (`init` target) + `.env.example`: `make init` now generates a random 32-char
  `POSTGRES_PASSWORD` (from `/dev/urandom`, `openssl rand` fallback) and substitutes it into the new
  `.env` (both `POSTGRES_PASSWORD` and the host-local `DATABASE_URL`). `.env.example` ships the
  clearly-fake placeholder `change_me_generated_on_init`.
- **D24** — `backend/requirements.txt` (httpx2 `2.4.0`→`2.5.0`), new `backend/requirements.lock`
  (hash-pinned, `pip-compile --generate-hashes`, 65 packages), `backend/Dockerfile` (both stages
  install `--require-hashes -r requirements.lock`; dev then layers `requirements-dev.txt` for
  pytest/ruff).
- **D4** — `backend/Dockerfile` (+gosu, create `appuser` UID 1000), `backend/docker-entrypoint.sh`
  (chown `/app/storage`, then `exec gosu appuser "$@"`), `agent/Dockerfile` (create `appuser`,
  chown `/app`, `USER appuser`), `frontend/Dockerfile` dev stage (+gosu, new entrypoint),
  new `frontend/docker-entrypoint.sh` (chown `node_modules` volume, `exec gosu node "$@"`).

## D29 findings — versions NOT changed

Verified via web search (as of mid-2026) that **all seven** queried frontend majors are stable/GA
releases, so per the task guidance nothing was pinned back:

| Package | Queried major GA? | Latest stable |
|---|---|---|
| vite 8 | yes | 8.1.x (Rolldown-powered, GA) |
| typescript 6 | yes | 6.0.x (GA 2026-03-23; TS 7 still RC) |
| pdfjs-dist 6 | yes | 6.1.x |
| vitest 4 | yes | 4.1.x |
| jsdom 29 | yes | 29.1.x |
| @sveltejs/vite-plugin-svelte 7 | yes | 7.1.x |
| svelte 5 | yes | 5.56.x |

## D2 — exact CSP shipped + how it was smoke-tested

```
Content-Security-Policy: default-src 'self'; script-src 'self'; object-src 'none';
  frame-ancestors 'none'; base-uri 'self'; img-src 'self' data:; font-src 'self' data:;
  style-src 'self' 'unsafe-inline'; worker-src 'self' blob:; connect-src 'self' http: https:
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
```

Two deliberate, documented relaxations from a pure `'self'` policy:
- **`connect-src 'self' http: https:`** — the API is a *separate origin* (`VITE_API_BASE_URL`,
  e.g. `http://192.168.1.10:8000`, baked into the bundle at build time). A `'self'`-only
  `connect-src` would block every API call (login, list, upload). nginx.conf is static and can't
  know the API host, so http/https is the minimum that keeps the app functional across LAN setups.
- **`style-src 'unsafe-inline'`** (Svelte/Vite runtime-injected `<style>`) and
  **`worker-src blob:`** (defensive fallback for the locally-bundled PDF.js worker; the worker is
  actually a same-origin `?url` asset, so `'self'` already covers the normal path).

**Smoke test:** `make frontend-check` built the bundle; the built `dist/index.html` has **no inline
scripts and no inline styles** (only a same-origin `<script type=module src=/assets/...>` and an
external stylesheet), so `script-src 'self'` is safe. Served `dist/` through a throwaway
`nginx:1.27-alpine` with the real `nginx.conf`: config syntax OK; document, JS asset, CSS asset, and
a deep SPA route all returned **200** with all four headers present. Every asset the bundle loads is
same-origin (script/style/worker `'self'`), so nothing is CSP-blocked.

## D4 — approach + proof

Standard "root entrypoint fixes volume ownership, then drops privileges via gosu" pattern, because
the existing named volumes (`paperracks_library`, `paperracks_frontend_node_modules`) are
root-owned and would otherwise be unwritable by a non-root process:
- **api/worker** (`backend/Dockerfile`): `appuser` UID 1000 (matches the host dev so bind-mounted
  source stays writable). Entrypoint (still root) chowns `/app/storage` (guarded — recursive chown
  effectively runs once), applies migrations, then `exec gosu appuser "$@"`.
- **agent**: no root-owned named volume, so a plain `USER appuser` (UID 1000) suffices.
- **frontend dev**: built-in `node` user (UID 1000); new entrypoint chowns the `node_modules`
  volume, then `exec gosu node "$@"`.

**Proof the stack is healthy after `make up` (all rebuilt, non-root):**
- `/api/v1/health` → **200**; `docker compose ps` shows api healthy, worker/agent/frontend Up.
- PID 1 owners: api = `appuser` (uvicorn), worker = `appuser` (supervisor) with **2 `rq worker`
  children also `appuser`**, frontend = `node` (vite), agent = `appuser`.
- Managed library writable: `/app/storage` (incl. pre-existing `library/` + `search_index/`) is now
  `appuser`-owned; a write as `appuser` succeeded. No permission/migration errors in api logs.
- Frontend: `:5173` → 200, `node_modules` chowned to `node`, `node` can write the `.vite` cache.

**Note (not backed out):** the *production* frontend image is nginx and was left as-is — its worker
processes already run as the non-root `nginx` user, and making the master non-root needs a port/cap
change out of scope here. Documented for a future pass.

## D24 — reproducibility note

The compiled lock resolved to the **exact versions already installed** in the known-good running
image (fastapi 0.139.0, pydantic 2.13.4, sqlalchemy 2.0.51, numpy 2.5.0, scipy 1.18.0, redis 8.0.1,
rq 2.10.0, lxml 6.1.1, …), differing only by the intended httpx2 2.5.0 bump — so rebuild risk was
minimal. Regenerate with: `pip install pip-tools && pip-compile --generate-hashes
--output-file backend/requirements.lock backend/requirements.txt` (run in the api container).

## Verification results

- `make frontend-check`: **green** — 88 passed / 1 skipped, build OK.
- Full backend suite (`docker compose exec -T api python -m pytest backend/tests -q`): **749 passed**.
- No Python source touched → ruff N/A (Makefile/shell/config only).
- `python scripts/check_secrets.py`: clean before every commit.

## Assumptions

- Host dev UID is 1000 (matches the scratchpad path and the bind-mount ownership); `appuser`/`node`
  are pinned to UID 1000 so the bind-mounted source tree stays writable in dev.
- `connect-src http: https:` is an acceptable relaxation at the single-user/LAN scale (XSS is
  contained by `script-src 'self'`); tighten to the concrete API origin if the deployment is ever
  fronted by a same-origin reverse proxy.

## Security implications

Net positive: containers no longer run as root (smaller blast radius for an RCE while parsing
untrusted PDFs), the SPA now ships CSP + framing/sniffing/referrer headers, fresh installs get a
random DB password instead of the shared literal default, and the backend dependency set is
hash-pinned so a rebuild can't silently pull a new major.

## Next recommended task

Remaining AUDIT items in this area: D3 (plaintext agent↔server transport warning), D30 (ops polish:
slim OCR target, runtime `config.js` injection), D36a (wire Playwright E2E into CI). Also consider a
dedicated pass to make the *production* nginx image fully non-root.

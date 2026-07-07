# Handoff — Batch W: Makefile & Docker workflow robustness (2026-07-07)

Infra-only. Committed on `main` (not pushed). Implements Batch W of
`docs/WORKPLAN_2026-07-06.md`. Goal: `make init && make up-all` works out of the box, and a
`git pull` + rebuild self-heals dependencies, volume ownership, and migrations with no manual steps.

## What changed

- `frontend/docker-entrypoint.sh` — hardened the node_modules self-heal (see below).
- `docker-compose.yml` — api healthcheck `start_period: 30s`; worker now `depends_on api:
  service_healthy` (migrations-at-head gate).
- `Makefile` — `rebuild` now rebuilds `--no-cache` **and** recreates; new `fresh` (destructive clean
  slate, `CONFIRM=1` guard) and `smoke` (dev-stack health assertions) targets; added a `PROJECT` var.
- `INSTALL.md` — replaced stale/unrelated content with the canonical run + recovery flow.
- `docs/runbooks/development_setup.md` — command list + decision table updated for the new targets.

## Frontend self-heal (`frontend/docker-entrypoint.sh`)

The image (`frontend/Dockerfile`, `development` target) bakes `node_modules` + a hash of
`package-lock.json` at `/opt/paracord-frontend`. `.dockerignore` excludes `frontend/node_modules`, so
the image's `npm ci` tree survives `COPY frontend ./` and also seeds a fresh named volume. On start
the entrypoint compares the volume's `.paracord-package-lock.sha256` marker to the current lock hash:

- (a) fresh/empty volume or (c) mismatch **with** a matching baked copy → fast restore from
  `/opt/paracord-frontend/node_modules` (no network);
- (d) mismatch with **no** matching baked copy → `npm ci` fallback (needs the registry);
- (b) hash match → no-op.

The marker is written **last**, so a crash mid-restore re-heals on the next start.

**Bug fixed:** the previous chown guard checked only the top-level `node_modules` directory. A restore
or `npm ci` rewrites the *contents* as root while the directory keeps its owner, so on a volume that
was already `node`-owned the new root-owned files were never chowned back → `node` couldn't write.
The rewrite sets a `healed` flag and always `chown -R node:node` after any heal (plus the original
fresh-root-volume check).

## Named-volume ownership audit

| Named volume | Written by | Runs as | Ownership handling |
|---|---|---|---|
| `paperracks_postgres` | postgres | postgres (official) | image-managed |
| `paperracks_ollama` | ollama | root (official) | n/a |
| `paperracks_library` (`/app/storage`) | api + worker | `appuser` (1000) | `backend/docker-entrypoint.sh` chown → `gosu` |
| `paperracks_frontend_node_modules` | frontend | `node` (1000) | `frontend/docker-entrypoint.sh` chown → `gosu` |

`frontend/dist` (on the `./frontend` bind mount) is also chowned by the frontend entrypoint. The
agent runs non-root but mounts only `./agent` (no root-owned named volume) → no chown needed.

## Recovery flow (documented in INSTALL.md)

- Out of the box: `make init && make up-all`
- After a pull (self-heals deps/ownership/migrations): `make up-all`
- Verify: `make smoke`
- Wedged, keep data: `make rebuild`
- Clean slate (drops DB + library, keeps Ollama models): `make fresh CONFIRM=1`
- Full reset incl. models: `make clean`

## Proof (against the live stack; demo data untouched)

1. Rebuilt the frontend image with the new entrypoint, recreated only `frontend`
   (`docker compose up -d --no-deps frontend`). Log: "restoring dependencies from the baked image
   copy"; marker written; `yaml`/`echarts`/`cytoscape` present and **owned by `node`**; HTTP 200. No
   `docker volume rm`.
2. Simulated a dep change: version `0.0.0`→`0.0.1` in `package.json` + `package-lock.json`
   (lock hash `f939…`→`4989…`), rebuilt (npm ci reran) + recreated. Stale marker mismatched, new
   baked hash matched, deps restored from baked copy, marker → `4989…`, HTTP 200. Self-healed with
   no manual volume removal.
3. Reverted the version bump, rebuilt/recreated → marker back to `f939…`, `git diff` on the package
   files empty, HTTP 200.

`make smoke` passes; api container health = `healthy`. Did **not** run `make fresh` (would drop the
demo DB) — instead verified its label-based volume selection resolves to exactly the three app
volumes (ollama excluded) and `make -n fresh` parses.

## Verification

- `make -n rebuild`, `make -n fresh`, `make -n smoke` parse; `make help` lists all three.
- `make smoke` → all core services ok, exit 0.
- `python scripts/check_secrets.py` clean.
- No Python touched (shell/compose/Makefile/docs only), so no ruff/test-suite run required per the
  infra-only rule.

## Deviations / confidence

- Proof used a **frontend-targeted** rebuild+recreate rather than a full `make up-all`, to avoid
  disrupting the owner's running api/worker/DB and demo data. Because `docker compose up -d --build`
  only recreates services whose image/config changed, a real `up-all` after a frontend-only lock
  change behaves identically for the frontend. High confidence the self-heal works on a full
  `up-all`.
- `make fresh` was **not** executed (destructive). Verified its commands statically + via read-only
  label queries. High confidence the volume selection is correct (labels resolved to the three real
  volume names; ollama excluded).

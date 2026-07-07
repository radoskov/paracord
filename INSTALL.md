# Installing and running PaRacORD

PaRacORD is Docker-first: Docker Compose is the source of truth for the runtime, database
migrations, and tests. You need Docker with the Compose v2 plugin (`docker compose`) and `make`.
Nothing else is required on the host to run the stack.

For the full development workflow (lint, tests, migrations, per-area loops) see
`docs/runbooks/development_setup.md`. This file is the short, canonical "how do I get it running,
and what do I do when it breaks" guide.

## Out of the box

```bash
make init          # create .env from .env.example (generates a random POSTGRES_PASSWORD)
make up-all        # build + start the full stack, plus the GROBID and Ollama profiles
```

`make up-all` builds the images and starts every service. On a cold start with empty volumes the
stack orders itself: Postgres and Redis come up and pass their healthchecks first, the API waits for
Postgres to be healthy and then runs `alembic upgrade head`, and the worker waits for the API to be
healthy (schema at head) before it starts consuming jobs. No manual retries are needed.

If you only want the core stack without the optional GROBID (PDF extraction) and Ollama (local LLM)
services, use `make up` instead of `make up-all`.

Then create the first owner account and verify:

```bash
make bootstrap-admin   # create the initial owner account
make smoke             # assert every core service is healthy
```

`make smoke` checks Postgres, Redis, the API health endpoint, the frontend dev server, and the
worker; GROBID and Ollama are checked only when their profiles are running. It exits non-zero with a
clear message if anything is wrong.

## After a `git pull`

```bash
make up-all
```

A rebuild self-heals automatically:

- **Dependencies.** The frontend's `node_modules` lives in a named volume that would otherwise
  shadow a freshly built image. The image bakes a copy of `node_modules` plus a hash of
  `package-lock.json`. On start the frontend entrypoint compares the volume's installed hash to the
  current lock hash and, on a mismatch, restores the dependencies from the baked copy (falling back
  to `npm ci` only if no baked copy matches). You do **not** need to remove any volume by hand when
  `package-lock.json` changes. Backend Python dependencies live in the image and refresh on
  `--build`.
- **Ownership.** The API/worker entrypoint chowns the managed-library volume (`/app/storage`) and
  the frontend entrypoint chowns `node_modules` (and `dist`) to their non-root runtime users before
  dropping privileges, so root-owned fresh volumes never block writes.
- **Migrations.** The API entrypoint runs `alembic upgrade head` on every server start, so a pulled
  schema change is applied automatically.

Run `make smoke` afterwards if you want an explicit all-green confirmation.

## When something is wedged

```bash
make rebuild       # docker compose build --no-cache, then up -d --force-recreate
```

Use `make rebuild` when build caches are stale or a container is in a bad state but you want to keep
your data. It rebuilds images from scratch and recreates the containers; **volumes (database,
library, dependencies) are kept.**

## Clean slate (destructive)

```bash
make fresh CONFIRM=1
```

`make fresh` is the guaranteed-clean-slate reset. It stops the stack and **drops the app-data
volumes** — the Postgres database, the managed library, and the frontend `node_modules` — then
rebuilds and starts everything. The `CONFIRM=1` guard is required because this **wipes the database
and the managed library**.

What it drops vs. keeps:

| Volume | `make fresh` |
|---|---|
| `paperracks_postgres` (database) | **dropped** |
| `paperracks_library` (managed library / uploaded PDFs) | **dropped** |
| `paperracks_frontend_node_modules` (frontend deps) | **dropped** |
| `paperracks_ollama` (downloaded LLM models) | **kept** (expensive to re-pull) |

After `make fresh` you must re-create the owner account (`make bootstrap-admin`) and re-import your
library.

For a full reset that also removes the Ollama models, use `make clean` (`docker compose down -v`).

## Recovery cheat sheet

| Situation | Command |
|---|---|
| First run, out of the box | `make init && make up-all` |
| After a `git pull` (self-heals deps/ownership/migrations) | `make up-all` |
| Verify the stack is healthy | `make smoke` |
| Build caches wedged / container in a bad state (keep data) | `make rebuild` |
| Guaranteed clean slate (drops DB + library, keeps models) | `make fresh CONFIRM=1` |
| Full reset including models | `make clean` |

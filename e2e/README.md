# PaRacORD end-to-end browser tests (Playwright)

Headless Chromium journeys that drive the real frontend against the running dev stack.

## Prerequisites

- The dev stack must be **up** (`make up` from the repo root): frontend on
  `http://127.0.0.1:5173`, API on `http://127.0.0.1:8000`. The frontend's default
  `VITE_API_BASE_URL` already points at that API.
- Node 20+ and the Chromium browser binary (installed once, see below).

## Install

```bash
cd e2e
npm install
npx playwright install chromium   # downloads the browser (needs network)
```

## Run

From the repo root (recommended — seeds the test user first):

```bash
make e2e
```

Or directly:

```bash
cd e2e
npx playwright test          # headless
npx playwright test --headed # watch it run
npx playwright show-report   # open the last HTML report
```

## The seeded test user

The suite signs in as a dedicated **admin** account with fixed credentials, created idempotently by
`scripts/ensure_e2e_user.py` (run inside the `api` container):

```bash
docker compose exec -T api python scripts/ensure_e2e_user.py
```

Credentials come from the environment, with dev defaults that match the seed script:

| Variable       | Default          |
| -------------- | ---------------- |
| `E2E_USERNAME` | `e2e_admin`      |
| `E2E_PASSWORD` | `e2e-Passw0rd!`  |

Override the harness endpoints with `E2E_BASE_URL` / `E2E_API_URL` if the stack runs elsewhere.

`global-setup.ts` logs in once through the UI and saves the session to `.auth/state.json`; every
spec reuses it so tests start authenticated. The sign-in spec resets that state to test the login
form itself.

## Journeys

1. **Sign in** — the login form authenticates and the tab nav appears.
2. **Create + edit a paper** — create via "+ New paper", edit the year, assert it persists across a
   reload, then delete (via API cleanup).
3. **Shelves** — create a shelf, add a paper, assert membership; shelf archived + paper deleted after.
4. **Search** — lexical search finds a paper by a distinctive token, shows a relevance %, and opens
   it in the Library.
5. **AI & Models** — the `#ai` panel shows provider availability (e.g. the `hash_bow` embedder).
6. **Tab navigation** — click + arrow-key navigation, and a Search query surviving a tab switch
   (tab-state caching).

Mutating journeys use unique `E2E …` names and clean up after themselves via the API, so reruns and
parallel runs never collide.

Journeys that require GROBID/Ollama (PDF import, extraction, the reader with a real PDF) are out of
scope here and are intentionally not covered rather than made flaky.

## CI wiring

1. Bring the stack up (`make up`) and wait for the frontend + API to be healthy.
2. `cd e2e && npm ci && npx playwright install --with-deps chromium`.
3. Seed the user: `docker compose exec -T api python scripts/ensure_e2e_user.py`.
4. `npx playwright test`.
5. On failure, upload `e2e/playwright-report/` and `e2e/test-results/` (traces, screenshots, video)
   as build artifacts.

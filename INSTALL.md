# Extended test battery — direct installation

This bundle is designed for direct copy into the PaRacORD repository.

From inside this extracted directory:

```bash
rsync -av ./ /path/to/paracord/
```

Or copy folders manually into the repository root.

## What is included

```text
backend/tests/test_ext_access_policy_contracts.py
backend/tests/test_ext_file_path_resolver_contracts.py
backend/tests/test_ext_queue_capacity_contracts.py
backend/tests/slow/test_ext_slow_visibility_scope_invariants.py
backend/tests/safety/test_ext_safety_filesystem_isolation.py
backend/tests/future/test_future_ext_acceptance_contracts.py
agent/tests/test_ext_agent_path_contracts.py
agent/tests/safety/test_ext_agent_path_safety.py
frontend/src/api/client.ext.test.ts
frontend/src/lib/reader/readingMode.ext.test.ts
frontend/src/future/ExtFutureWorkflow.test.ts
e2e/tests/slow/40-ext-slow-search-export-review.spec.ts
e2e/tests/future/90-future-agent-teleport-and-summary.spec.ts
docs/testing/EXT_TEST_BATTERY.md
docs/testing/EXT_TEST_DESIGN_REVIEW.md
```

## Test marker scheme

The bundle follows the current project convention:

- **normal** tests: no pytest marker; picked up by the routine suite.
- **slow** tests: `pytest.mark.slow`; excluded by `make test`, included by `make test-full`.
- **safety** tests: `pytest.mark.safety`; excluded by fast and full feature loops, run with `make test-safety`.
- **future** tests: skipped/disabled placeholders that document acceptance criteria for future verticals.

For Playwright E2E, slow/safety tagging is expressed by directory/name/title because the project does not use pytest markers for Playwright. The included E2E slow test lives under `e2e/tests/slow/`. The future E2E file is `test.describe.skip(...)` so it is safe to copy now.

## Suggested commands after copying

```bash
make fix
make test
make test-full
make test-safety
make frontend-test
make e2e
make ready-full
```

If you only want a quick sanity pass after copying:

```bash
make fix
make test
make frontend-test
```

## Notes

The tests are intentionally contract-oriented. They avoid brittle timing checks, exact k-means cluster-size expectations, and fixed sleeps. Where eventual consistency is relevant, E2E tests use Playwright's retry assertions.

## TLS on the LAN (AUDIT D3)

Out of the box the server speaks plaintext HTTP — fine on loopback, but on a LAN every session
JWT and agent bearer token crosses the network readable by any sniffer. The production overlay
ships a Caddy TLS proxy:

1. `cp config/Caddyfile.example config/Caddyfile` and set your server's LAN name/IP.
2. `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` — the app is now at
   `https://<your-host>`; Caddy's `tls internal` mints certificates from a local CA (no public
   domain needed). With a real domain, drop `tls internal` for automatic Let's Encrypt.
3. Trust the local CA on client machines:
   `docker compose exec caddy cat /data/caddy/pki/authorities/local/root.crt` — import it in the
   browser, and point the agent at it via `ca_cert: /path/to/root.crt` in the agent config.
4. Update `server.public_base_url` (and the agent's `server_url`) to the `https://` address.

Transport guards: the agent **refuses** plaintext HTTP to a non-loopback server unless the agent
config sets `allow_insecure_http: true`; the server logs a loud startup warning in the same
situation unless `PARACORD_ALLOW_INSECURE_HTTP=true` acknowledges it.

Agent tokens (D3): approval mints a **permanent** token by default (right for your own trusted
machines). To hand a temporary user a short-lived token, approve their agent with a
`token_ttl_days` value (Admin → Agents); expired tokens are rejected with 401 until re-approval.

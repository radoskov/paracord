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

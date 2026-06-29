# Additional PaRacORD Test Battery

This bundle adds tests for current-stage invariants plus disabled future-stage
acceptance tests.

## Files to copy

Copy this bundle's directory contents into the repository root:

```bash
cp -R backend agent frontend docs /path/to/paracord/
```

A safer command from inside the unzipped bundle is:

```bash
rsync -av ./ /path/to/paracord/
```

The bundle places files at:

```text
backend/tests/test_additional_security_contracts.py
backend/tests/test_additional_library_contracts.py
backend/tests/test_additional_algorithm_contracts.py
backend/tests/future/test_future_agent_teleport_acceptance.py
backend/tests/future/test_future_grobid_coordinates_acceptance.py
backend/tests/future/test_future_local_llm_acceptance.py
agent/tests/test_additional_agent_security.py
frontend/src/api/client.additional.test.ts
frontend/src/future/FutureUserFlow.test.ts
docs/testing/ADDITIONAL_TEST_BATTERY.md
```

## Current-stage tests

The enabled tests cover:

- disabled/expired user sessions;
- raw bearer token non-storage;
- reader write restrictions for upload/import endpoints;
- managed-library streaming inside/outside the configured root;
- many-to-many work/shelf/rack membership semantics;
- idempotent membership writes;
- shelf/rack tag isolation from work-tag filters;
- non-brittle topic-model assignment replacement and rack deduplication;
- agent root-prefix and symlink escape checks;
- frontend API request construction and upload request behavior.

## Future-stage tests

The future tests are intentionally skipped with `pytest.mark.skip` or
`describe.skip`. They document acceptance criteria for later phases:

- real agent enrollment/manifest/teleport round trip;
- GROBID extraction with citation contexts and PDF coordinates;
- local-LLM summaries with provenance;
- browser-level literature review flow.

They should remain disabled until the corresponding features exist.

## Commands

Run the normal project checks:

```bash
make test
make frontend-test
make ready
```

For focused runs:

```bash
docker compose run --rm --no-deps api python -m pytest \
  backend/tests/test_additional_security_contracts.py \
  backend/tests/test_additional_library_contracts.py \
  backend/tests/test_additional_algorithm_contracts.py

docker compose run --rm --no-deps agent python -m pytest \
  agent/tests/test_additional_agent_security.py

make frontend-test
```

## Design notes

These tests deliberately avoid fragile assertions such as exact k-means cluster
membership. Algorithm tests assert stable contracts: all expected work is
assigned, prior assignments are replaced, scopes are deduplicated, and topic
counts remain bounded by requested limits.

If a test fails, prefer fixing the product contract rather than hard-coding the
implementation to satisfy the test. When a behavior is intentionally changed,
adjust the test to the new specification-level invariant.

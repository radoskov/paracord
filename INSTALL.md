# PaRacORD topic-modeling test rework

This bundle contains drop-in replacements/additions for the current topic-modeling tests and one small service implementation update.

## Files

- `backend/tests/test_topic_modeling.py`
  - Full replacement for the current topic-modeling test module.
  - Replaces brittle exact-cluster-size checks with semantic assertions and detailed diagnostic output.
  - Adds a test that verifies topic semantics are stable across different insertion orders.

- `backend/app/services/topic_modeling.py`
  - Full replacement for the current lightweight topic-modeling service.
  - Same public interface and behavior, with one important stabilizing change: scoped works are sorted before tokenization/vectorization/k-means seeding.

- `backend/tests/future/test_future_topic_modeling_acceptance.py`
  - New skipped future acceptance tests for richer embedding/BERTopic-style behavior.
  - These document intended future contracts without affecting the current suite.

## Installation

From inside this extracted bundle:

```bash
rsync -av ./ /path/to/paracord/
```

Or manually copy:

```bash
cp backend/tests/test_topic_modeling.py /path/to/paracord/backend/tests/test_topic_modeling.py
cp backend/app/services/topic_modeling.py /path/to/paracord/backend/app/services/topic_modeling.py
mkdir -p /path/to/paracord/backend/tests/future
cp backend/tests/future/test_future_topic_modeling_acceptance.py \
  /path/to/paracord/backend/tests/future/test_future_topic_modeling_acceptance.py
```

## Verification

From the repository root:

```bash
make fix
make test-api
make ready
```

Focused check:

```bash
docker compose run --rm --no-deps api python -m pytest backend/tests/test_topic_modeling.py -v
```

## Notes

The new main separation test is still meaningful: it requires every work to be assigned, two non-empty topics, separate dominant ML/cooking topics, useful topic keywords, and at most one cross-assigned paper in the small toy corpus. It does not require an exact 3/3 split, because k-means and future topic backends should not be forced to produce balanced clusters.

If the future tests appear in pytest output, they should be reported as skipped until the richer topic backend exists.

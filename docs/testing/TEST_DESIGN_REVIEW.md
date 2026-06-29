# Test Design Review

This review explains why the added tests fit the current PaRacORD specification
and avoid the brittleness that previously affected the k-means topic test.

## Specification coverage

| Specification area | Added coverage |
|---|---|
| No guest/anonymous access; authenticated access only | Disabled/expired session rejection, reader write restrictions |
| Credentials and sessions | Raw bearer tokens are not stored and token hashes cannot authenticate directly |
| Filesystem isolation | Managed-library stream accepts inside-root files and rejects root escapes |
| Keep/copy PDFs safely | Managed-path stream contract checks the managed-library root boundary |
| Shelves/racks many-to-many model | Work in multiple shelves, shelf in rack, rack filtering de-duplicates works |
| Tags on multiple entity types | Shelf/rack tags do not accidentally satisfy work filters |
| Topic modeling and local classification | Assignment replacement and scope de-duplication, not exact cluster sizes |
| Local agent filesystem boundary | Prefix collisions and symlink escapes are rejected |
| Frontend/API interaction | Query serialization, bearer auth, login behavior, FormData upload behavior |
| Future milestones | Disabled tests capture agent teleport, GROBID coordinates, local LLM summaries, browser-level literature workflow |

## Anti-brittleness choices

The tests avoid exact random/algorithmic outcomes. For example, topic modeling is
checked by these stable contracts:

- the same scope/model ID replaces previous assignments instead of accumulating stale rows;
- every tokenized work in scope receives one assignment;
- requested topic count is treated as an upper bound, not an exact promise;
- rack-scoped modeling de-duplicates the same work when it is present through multiple shelves.

The tests do not assert exact k-means membership, exact cluster size balance, or
exact floating-point scores.

## Current-stage vs future-stage split

Enabled tests should pass against the current scaffold and core implementation.
Future tests are skipped because they describe target behavior whose production
code is not yet expected to exist.

Future skipped tests should be unskipped one feature at a time when the matching
vertical is implemented. Do not keep a future test skipped after its feature is
merged; either make it pass or update it to the refined acceptance contract.

## Expected maintenance

When behavior changes intentionally, update tests at the product-contract level.
Avoid changing tests just to match internal implementation details.

Recommended commit after adding this bundle:

```bash
git add backend/tests agent/tests frontend/src docs/testing INSTALL.md
git commit -m "test: add additional product-contract test battery"
```

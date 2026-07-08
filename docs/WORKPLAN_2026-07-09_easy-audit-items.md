# Workplan — easy audit items (2026-07-09)

Implementation batch selected from the merged [`AUDIT.md`](AUDIT.md): the open/partial audit items
that are **self-contained and need no owner decision**, plus a CI regression the owner reported
mid-batch. Each item was verified against the current code (branch `feature/library-resize`) by
dedicated research passes before being scheduled here.

**Selection rule.** "Easy to implement, no additional information required." Excluded because they
need an owner call or are out of the stated scale: **H3** (default embedding model — gated on an
owner decision), **D38** (import breadth / Zotero — product-scope decisions), **E6** (onboarding —
design decisions), **E3** (SQL-predicate visibility refactor — the audit itself scopes it to "large
multi-user collections, out of the stated scale"), **S2 residual** (agent loopback-GUI token
niceties — loopback-only, low value), **D2 residual** (HttpOnly-cookie migration — deferred by
design).

---

## 0. CI regression — flaky `no such table: groups` *(owner-reported 2026-07-09)*

**Symptom.** `test_user_management.py::test_create_user_rejects_duplicate_and_bad_role` failed in CI
with `sqlite3.OperationalError: no such table: groups`; 1187 others passed. Order-dependent, passes
in isolation.

**Root cause.** Seven services memoized "does this optional table exist?" in a module-global dict
keyed on `id(db.get_bind())`. CPython reuses the memory address of a garbage-collected engine, so a
later, narrower test database whose engine reused that address inherited the previous engine's
stale `True` answer and then queried a table it does not have. `create_user` → `create_personal_group`
runs `SELECT ... FROM groups`, which is exactly the failing SQL. `test_web_find.py` already worked
around this by `.clear()`-ing one such cache — evidence the pattern was known-fragile.

**Fix (build now).** Replace all seven `id(bind)`-keyed caches with one shared helper
`app.utils.table_presence.table_present(db, table_name)` backed by a `WeakKeyDictionary` keyed on
the live bind object, so an entry is purged when its engine is GC'd and address reuse can't alias.
Services: `groups`, `access`, `app_config`, `ai_config`, `embedding_registry`, `access_settings`,
`web_find_settings`. Add a regression test; update the one test that poked the old cache.
*(Related latent site not in scope: `bm25_index` uses `(id(bind), signature)` for its index cache —
noted for a future pass; the signature component limits its blast radius.)*

---

## 1. E2 — parser-level PDF validation (PyMuPDF open-probe)  *(AUDIT E2, PARTIAL → close)*

**Current state.** All five upload handlers validate size (413) + `%PDF` header (400) only; an
encrypted or structurally-broken PDF passes and only fails later inside GROBID/OCR. PyMuPDF (`fitz`)
is already a dependency (`requirements.txt` / lock). `storage._extract_pdf_preview` already opens
page 0 with fitz but *fails open* (swallows errors → "unknown").

**Fix.** A shared `probe_pdf_openable(pdf_bytes) -> str | None` that **fails closed**: rejects PDFs
fitz cannot open, password-protected/encrypted PDFs (`needs_pass`), zero-page PDFs, and PDFs whose
first page cannot be loaded — returning a clear user-facing reason. Call it in each of the five
upload handlers immediately after the `%PDF` header check, raising `HTTPException(400)` so invalid
bytes never reach GROBID/OCR. Add safety tests (valid-`%PDF`-header-but-unopenable / encrypted).

## 2. E1 — Redis fail-closed flag + "limits unavailable" status  *(AUDIT E1, OPEN → close)*

**Current state.** Rate-limit, queue-cap, and login-throttle all fail *open* when Redis is down
(correct default for single-user). No production switch, no admin indicator.

**Fix.** Add `PARACORD_PRODUCTION_REQUIRE_REDIS` (default `false`) to `Settings`. When set and Redis
is unreachable: `rate_limit.check` denies with a distinct `unavailable` scope → the middleware
returns **503** (not 429), and `queue_capacity.assert_queue_has_capacity` raises **503** instead of
no-op'ing. Login-throttle already degrades to a per-process window (still throttles), so it is left
as-is and documented. Surface `require_redis` in the queue-status payload and show a red
"rate/queue limits unavailable" line in the Jobs page when `require_redis && !redis_reachable`
(yellow "not enforced" when the flag is off). Document the flag in `config/server.example.yaml`.

## 3. L7 cleanup — drop dead `Agent.revoked_at`  *(AUDIT notes / WORKPLAN L7)*

**Current state.** `Agent.revoked_at` is never read or written; revocation is via `status !=
"approved"` / `delete_agent` (both correctly 401 a stale token). Confirmed zero references outside
the model column + its original migration `0020`.

**Fix.** Remove the column from the model and add migration `0055_drop_agent_revoked_at`
(down_revision `0054_agentfile_work_id`). Verify with the Postgres migration-parity test
(`make test-migrations`).

---

## Verification

- `make test` (fast tier) green after each chunk; full fast backend suite green for the CI fix.
- `make test-migrations` (Postgres parity) for L7.
- `make test-safety` for the new E2 upload probes.
- `ruff check` / `ruff format` clean; `npm run build` clean for the E1 frontend change.

## Out of scope / follow-ups
- `bm25_index` `id(bind)` index-cache (related to §0; separate, hot-path, signature-guarded).
- Everything under the "Selection rule" exclusions above stays in `AUDIT.md` / `WORKPLAN.md`.

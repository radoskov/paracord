# Batch S — deeper safety / attack-robustness / web-stability battery

- **Task:** add a deeper adversarial security/attack/web-stability test layer as a SEPARATE
  `make test-safety` target, kept OUT of the core `make test`/`make test-full` suites. Reference:
  `docs/WORKPLAN_2026-07-06.md` Batch S.

## Files changed

- `pyproject.toml` — registered the `safety` pytest marker.
- `Makefile` — `PYTEST_FAST` now `-m "not slow and not safety"`; new `PYTEST_FULL`
  (`-m "not safety"`) used by `test-api-full`/`test-agent-full`; new `PYTEST_SAFETY` (`-m safety`)
  and a new `test-safety` target (runs `backend/tests/safety` in the API container). Help line added.
- `docs/runbooks/development_setup.md` — documented `make test-safety`.
- `backend/tests/safety/conftest.py` — shared seeding fixtures (`make_shelf`/`make_rack`/`make_work`/
  `add_to_shelf`/`hidden_work`/`headers_for`), building on the parent `backend/tests/conftest.py`.
- `backend/tests/safety/test_safety_*.py` — 10 files, all `pytestmark = pytest.mark.safety`:
  `authz_idor`, `privilege_escalation`, `rate_and_throttle`, `ssrf`, `path_traversal`,
  `upload_abuse`, `xxe`, `sql_injection`, `auth_session`, `web_stability`.
- `PROGRESS.md` — progress note.

## Marker / target mechanism + how the core excludes it

- The `safety` marker is registered in `pyproject.toml` and applied per module (required for BOTH
  `-m safety` selection and `-m "not safety"` deselection).
- `make test` / `make ready` (fast) select `-m "not slow and not safety"`.
- `make test-full` / CI (`make ci`) select `-m "not safety"` (still includes `@slow`).
- `make test-safety` selects `-m safety` over `backend/tests/safety` in the API container.
- Mirrors how `@slow` is already deselected from the fast targets.

## Test groups + counts (158 tests total)

- **authz_idor** (~21): hidden-work reads (get/citation-neighborhood/shelves/related/references/
  metadata → 404) + mutations (403/404); viz/citation-summary/missing-export private-shelf/rack
  scope → 404; external-preview hidden reference → 404; worklist + import-batch per-user isolation;
  jobs/app-config/theme admin floors; work-create mass-assignment ignored.
- **privilege_escalation** (~14): role-ladder floors; admin can't create/promote/disable/delete an
  admin or the owner; self-role-escalation via profile ignored; create-user mass-assignment ignored.
- **rate_and_throttle** (6): rate-limit trips under burst + concurrency + recovers; login-throttle
  trips (429 + Retry-After) and recovers; queue-cap rejects burst + no concurrency bypass.
- **ssrf** (~24): `_ip_is_internal` over all private ranges; injected-resolver internal-host
  detection + fail-closed; `_classify_download_host` hard-blocks internal/bad-scheme/shadow;
  `resolve_final_url`/`_stream_pdf`/`download_and_attach` refusals; admin `ollama_url` guard.
- **path_traversal** (~9): `_validated_path` `..`/absolute/symlink escapes; `derived_ocr_path`
  digest check + root containment; managed-stream endpoint 403 on `..` escape.
- **upload_abuse** (6): 413 oversized (cap monkeypatched small), 400 non-PDF/malformed/zero-byte/
  truncated, bomb-style body bounded by the read cap + app stays responsive.
- **xxe** (7): external local-file + network SYSTEM entities not resolved; billion-laughs refused at
  depth (no hang) / bounded when shallow; citation-list + body + sections all safe (lxml 6.1.1).
- **sql_injection** (~13): search-query allowlist carries values verbatim (bound via ORM); unknown
  keys → free text; year regex rejects injection; `slugify`/`_SAFE_COLUMN` reject SQL metachars;
  HTTP `/works?q=`/`?sort=` injection harmless.
- **auth_session** (~13): session-token entropy/uniqueness/opacity; revoked/expired → 401; token not
  echoed in a URL/Location header; garbage bearer → 401; agent bad/unapproved/revoked-status token →
  401; approved token → 200; missing-privilege agent → 403; enrollment token single-use + unknown.
- **web_stability** (4): nginx CSP/security-header assertion (skips when the config isn't reachable);
  health-endpoint concurrency; sustained authenticated reads; oversized query string doesn't wedge.

## Real holes found + fixes

**None.** Every existing guard held under the deeper probes — no app code was changed, so
`backend/openapi.json` is unaffected. The battery is regression coverage of the existing hardening
(SSRF egress guard, path-root validation, safe XML parse, allowlisted SQL, ACL/role gates, rate/
throttle/queue caps, opaque tokens).

### Observations (by-design, not holes)

- `Agent.revoked_at` is dead code (never written). Agent revocation works via `status != "approved"`
  (the `require_agent_token` gate) or `delete_agent`; both correctly 401 a stale token — asserted in
  `test_safety_auth_session.py`. Left as-is (scale: mostly single-user / a few LAN users).
- The nginx CSP/header test SKIPS in the API container because `frontend/nginx.conf` lives only in
  the frontend image; it asserts the committed directives when the config is reachable (e.g. host
  `pytest`). This was the only probe class not fully exercisable at the API-test layer.
- `ollama_url` pointing at a loopback / docker-service host is intentionally allowed (Ollama is an
  internal service); only LAN/public/metadata hosts are rejected unless `ALLOW_EXTERNAL_OLLAMA=true`.

## Tests added / skipped

- Added: 157 passing + 1 skipped (the nginx-config assertion, cleanly skipped in the API container).
- `make test-safety` → **157 passed, 1 skipped**.
- Core suite unaffected: `-m "not safety" backend/tests` → **909 passed, 158 deselected** (unchanged).
- `ruff check` / `ruff format --check` clean on `backend`/`agent`.

## Security implications

Pure additive test coverage; no runtime/app behavior changed. Locks in the current adversarial
posture so a future regression in any probed guard fails `make test-safety`.

## Next recommended task

- Consider wiring `make test-safety` into a scheduled/nightly CI lane (kept out of the fast/full core
  by design). If agent revocation-by-flag is ever wanted, make `revoked_at` live and gate on it.

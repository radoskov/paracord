# Handoff — easy-audit-items batch + CI fix (2026-07-09)

**Task.** Merge the two audit files into one; select the audit items that are easy and need no owner
decision; build them; fix an owner-reported CI regression that landed mid-batch. Branch
`feature/library-resize`, **not pushed**. Plan: `docs/WORKPLAN_2026-07-09_easy-audit-items.md`.

## Commits (one per logical chunk)
1. `backend: fix flaky table-presence cache (id(bind) aliasing)`
2. `docs: merge AUDIT_EXT into AUDIT; add easy-items workplan`
3. `backend: drop dead Agent.revoked_at column (AUDIT L7)`
4. `backend: parser-level PDF validation via PyMuPDF probe (AUDIT E2)`
5. `backend,frontend: PARACORD_PRODUCTION_REQUIRE_REDIS fail-closed + limits status (AUDIT E1)`
(docs-update commit follows for AUDIT/WORKPLAN/PROGRESS/SPEC/handoff.)

## Files changed
- **Audit merge:** `docs/AUDIT.md` (Appendix A + resolved E1/E2/L7/CI entries); deleted
  `docs/AUDIT_EXT.md`; new `docs/WORKPLAN_2026-07-09_easy-audit-items.md`.
- **CI fix:** new `backend/app/utils/table_presence.py`; `groups.py`, `access.py`,
  `access_settings.py`, `ai_config.py`, `app_config.py`, `embedding_registry.py`,
  `web_find_settings.py`; new `backend/tests/test_table_presence.py`; `tests/test_web_find.py`.
- **L7:** `backend/app/models/agent.py`; new migration `0055_drop_agent_revoked_at.py`;
  `SPECIFICATION.md` (agents schema table).
- **E2:** `backend/app/services/storage.py` (`probe_pdf_openable`); the 5 upload handlers in
  `api/v1/endpoints/{imports,works,agents}.py`; new `tests/test_pdf_probe.py`; a case in
  `tests/safety/test_safety_upload_abuse.py`; real-PDF fixtures in `tests/{test_agents,
  test_agent_teleport_acceptance,test_api_flows,test_d7_extraction_recovery,test_import_expansion}.py`.
- **E1:** `backend/app/core/config.py`, `services/rate_limit.py`, `main.py`,
  `services/queue_capacity.py`, `workers/queue.py`; `config/server.example.yaml`;
  `frontend/src/api/client.ts`, `pages/JobsPage.svelte`, `pages/JobsPage.test.ts`; tests in
  `tests/test_rate_limit.py`, `tests/test_queue_cap.py`.

## Assumptions
- "Easy, no info needed" excluded H3 (default embedding model — owner decision), D38 (import
  breadth/Zotero), E6 (onboarding), E3 (perf refactor, out of the stated scale), S2/D2 residuals.
- The CI fix's root cause (`id(bind)` aliasing) was fixed for the whole class (7 presence caches),
  not just `groups`, since any of the others could flake the same way. `bm25_index`'s
  `(id(bind), signature)` index cache was left alone (signature limits blast radius) — a noted
  follow-up.
- E1 login-throttle left fail-open-degrading (per-process window) rather than hard-failing auth, to
  avoid locking the owner out when Redis is down. Documented.
- Removing `Agent.revoked_at` from `SPECIFICATION.md` is a faithful update (dead column; revocation
  is via `status`, still listed).

## Tests
- Full fast backend suite green (671 passed) after the CI fix. Migration parity green on Postgres
  (L7). E2: `test_pdf_probe` + upload-abuse + all upload happy-paths (82 passed incl. the slow
  teleport acceptance). E1: `test_rate_limit` + `test_queue_cap` (24 passed) cover both fail-open
  default and fail-closed paths. Frontend Vitest green incl. a new JobsPage "limits unavailable"
  case; `npm run build` clean. `ruff check` clean on all touched files.
- **Not run here:** the deeper `make test-safety` battery beyond the upload-abuse file, and E2E.

## Security implications
- **E2** narrows the upload attack surface (encrypted/corrupt PDFs rejected at the edge, not in a
  worker). **E1** adds an opt-in fail-closed posture for LAN/production (default unchanged, so
  single-user behavior is preserved). **L7** removes dead code only. The CI fix is correctness-only.

## Next recommended task
- Push (needs owner approval — hard rule) and confirm CI is green.
- Remaining audit residuals: **E3** (SQL visibility predicates), **E5** (read-only mounts + backup
  verify in CI), and the `bm25_index` `id(bind)` index-cache follow-up.

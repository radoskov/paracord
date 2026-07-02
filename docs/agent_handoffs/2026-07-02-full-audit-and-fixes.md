# 2026-07-02 — Full audit + consolidated decisions + auto-fixes

- **Task:** full audit (security, efficiency, stability, tech suitability at product scale),
  merge findings with `FOLLOWUP.md` + `docs/NEEDS_DISCUSSION.md` into one decision document,
  auto-implement the unambiguous fixes.
- **Deliverable:** **`docs/DECISIONS.md`** — §A lists the ~30 fixes applied, §B the 38 decisions
  awaiting the owner (each with a recommendation), §C verified-sound areas, §D items closed since
  the old docs. `FOLLOWUP.md` and `docs/NEEDS_DISCUSSION.md` are superseded by it.
- **Files changed:** agent (perms/XSS/sync-guard/WAL: `web_server.py`, `web.py`, `state.py`,
  `secrets.py`, `agent_ops.py`); backend workers/services (job timeouts, rollback discipline,
  savepoints, HTTP-client + provider caching, batched queries: `workers/*`, `embedding_registry`,
  `embeddings`, `chunk_embeddings`, `semantic_search`, `storage`, `grobid_client`,
  `metadata_enrichment`, `summarization`, `citation_graph`, `duplicate_resolution`,
  `agent_files`); endpoints (`imports` IDOR clamp + default-shelf hook, `works`/`agents` upload
  threadpooling, `duplicates` batching, `ai_admin` 409 on locked DDL); `Makefile`
  (transactional restore), `scripts/ensure_e2e_user.py` (env guard), `backend/requirements.txt`
  + `README.md` (4 dead deps removed), `bm25_index.py` (stale-file pruning),
  `LibraryPage.svelte` (in-place row update), `docs/runbooks/backup_restore.md`.
- **Assumptions:** scale = mostly single-user, a few LAN users supported (per owner note);
  NEEDS_DISCUSSION 2c and 3a were implemented (both were marked "recommended" there);
  `httpx2` verified as the legitimate Pydantic-maintained fork (see DECISIONS D23) — keep it.
- **Tests:** `make test-full` green (660 backend + 32 agent; new regression tests for provider
  cache, GROBID timeout mapping, bm25 pruning, redirect guard adapted); `make frontend-check`
  green (75 tests + build); ruff + check_secrets clean.
- **Security implications:** fixes only tighten (perms, XSS escaping, IDOR clamp, e2e-seed
  guard). Open security decisions for the owner: DECISIONS **D1–D6** (multi-worker login
  throttle, CSP/localStorage, agent TLS, non-root containers, default DB password, ollama_url
  SSRF).
- **Next recommended task:** owner reads `docs/DECISIONS.md` §B and answers the decision list
  (D13 — BM25 rebuild off the read path — is the highest-leverage performance item; D1–D4 the
  security ones); then implement the agreed batch.

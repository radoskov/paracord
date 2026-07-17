# Handoff: Profile layout + attach-from-URL / server-path

## Files changed

- `frontend/src/pages/ProfilePage.svelte` — two flex columns in the grid (main: Appearance first,
  then Reference-graph weights; side: Account, Password, Roles & access); max-width 52 → 72rem;
  single column < 860px. `.theme-option` gets `color: var(--ink-strong)` (it inherited the global
  button's inverse ink — theme names were near-invisible; pre-existing).
- `backend/app/api/v1/endpoints/works.py` — `POST /works/{id}/files/from-path`
  (`AttachFromPathRequest{path}` → `WorkFileRead`, 201). Containment: resolve (symlinks followed)
  must land inside a merged allowed import root, else 403 with an actionable hint; then size cap,
  `%PDF` magic, openability probe — identical to a browser upload. The shared attach tail
  (store/dedupe/link/queue-extraction/response) is factored into `_attach_pdf_bytes_to_work`,
  used by both the upload endpoint and the new one. Audited as `work.file_attached_from_path`
  with the resolved path.
- `backend/openapi.json` — regenerated (`make openapi`).
- `backend/tests/test_attach_from_path.py` — 10 tests: attach + dedupe by content, outside-root /
  symlink-escape / `..`-traversal refusals, missing-file & directory 404s, non-PDF 400, blank 400,
  reader-role 403.
- `frontend/src/api/client.ts` — `attachWorkFileFromPath(workId, path)`.
- `frontend/src/components/WorkDetail.svelte` — "From URL…" and "From server path…" buttons in the
  Files attach row + one modal for both modes (Proceed/Cancel → result in place → OK). URL mode
  sends `{candidate_id:'manual-url', source:'manual_url', url, confirmed}` through the EXISTING
  `downloadWebCandidates` endpoint, so the whole download policy applies unchanged, including the
  `needs_confirmation` handshake ("Download anyway" re-sends confirmed). Attached/deduped refetches
  the work (identifier backfill) and re-arms the job watch.
- `frontend/src/components/WorkDetail.remoteattach.test.ts` — 5 tests: URL success + OK-close,
  needs_confirmation → confirmed re-send, blocked reason surfaced, path success + file-list
  refresh, path refusal detail.
- `e2e/tests/35-attach-remote.spec.ts` — both modals' refusal paths, offline-deterministic
  (localhost URL → policy refusal alert; `/etc/passwd` → outside-roots detail).
- `e2e/tests/13-pagination.spec.ts` — go-to-page now types + presses Enter; the old `fill` +
  synthetic `change` dispatch raced the pager re-render (`value={page}` reset the input between
  the two calls) — the flake seen once after serialization.

## Assumptions made

- Reusing `find-on-web/download` for pasted URLs is deliberate: one policy surface, no second
  download path to keep in sync. It 403s when `web_find_enabled=false` — acceptable coupling.
- `manual_url` provenance string distinguishes pasted-URL attaches in metadata/audit.
- Path attach requires roots to exist; on this deployment none are configured yet — the owner
  must add one under Admin → Server import folders before the feature is usable here.

## Tests added or skipped

- 10 backend + 5 frontend + 1 e2e journey added; nothing skipped. Verified: `make ready-full`
  (1237 backend / 305 frontend), `make test-safety` (161), `E2E_ONLINE=1 make e2e` 37/37 with
  0 flaky on the final run; Journey 13+23 re-run 3× green after the keystroke fix. Live checks:
  from-path 201-inside/403-outside via a temporary DB import root (removed after), from-URL
  fetched the real arXiv 1706.03762 PDF.

## Security implications

- from-path is the sensitive surface: containment on the RESOLVED path (symlink escapes + `..`
  refused, covered by tests), contributor role + per-work modify guard, 200 MB cap, PDF-magic +
  openability validation, audit event with the resolved path. It cannot read outside the merged
  allowed roots, which stay owner-managed.
- from-URL adds NO new network surface — it is the existing policy-gated downloader.

## Next recommended task

- Consider surfacing the allowed roots (aliases) inside the from-path modal for discoverability
  (needs a non-owner-readable listing endpoint — today's listing is owner/admin).

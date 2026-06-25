# Coding-Agent Coordination Guide

This file is for coding agents that will work on PaRacORD in parallel. Treat `SPECIFICATION.md` as the product contract and `WORK_SPLIT.md` as the recommended implementation partition.

## Global rules

1. Do not remove security boundaries to make development easier.
2. Do not add guest or anonymous access.
3. Do not create an API endpoint that accepts an arbitrary filesystem path and reads it.
4. All file access must go through one of:
   - a configured server-side allowed root,
   - a server managed-library object,
   - a local-agent file ID,
   - an uploaded file stream.
5. User-corrected metadata must not be silently overwritten by external metadata.
6. Every destructive or bulk-changing action must produce an audit event.
7. Store AI-generated summaries separately from extracted abstracts and human notes.
8. Store raw GROBID TEI for reproducibility.
9. Add tests for every implemented service boundary.
10. Prefer small, reviewable pull requests that complete one vertical slice.
11. **Never commit a real credential, secret, or personal datum.** Light config (URLs, IPs, ports, flags) goes through `.env` / `config/*.local.yaml` with placeholders in the `*.example` files. Serious secrets (DB passwords, `PARACORD_SECRET_KEY`, agent tokens, API keys) are read from the environment and referenced by env-var name, never inlined. User passwords use `hash_password`/`verify_password` (bcrypt) only — never plaintext, never logged. Other recoverable sensitive data is encrypted at rest with a key from the environment. Only *clearly fake* dummy/test values may appear in the repo. The full, enforced policy is in `docs/runbooks/secrets_management.md` — read it before touching auth, config, storage, or external integrations.

## Branch naming convention

```text
agent/<area>/<short-task>
```

Examples:

```text
agent/backend/auth-bootstrap
agent/agent/folder-manifest
agent/frontend/paper-table
agent/docs/latex-manual
```

## Commit message style

```text
area: concise imperative description
```

Examples:

```text
backend: add health router
agent: implement manifest hashing
security: add server-local password reset command
docs: document teleport workflow
```

## Required handoff note for each coding agent

At the end of a task, update `PROGRESS.md` and add a short note under `docs/agent_handoffs/` with:

```text
- task name
- files changed
- assumptions made
- tests added or skipped
- security implications
- next recommended task
```

## Definition of done for scaffold tasks

- Code is formatted.
- Type hints are present for public functions.
- New modules have docstrings explaining their role.
- Tests or explicit TODO test notes are included.
- Config changes are reflected in example YAML files.
- New config keys add a placeholder to the matching `*.example` file; secret keys are referenced by env-var name, never given a real value.
- `python scripts/check_secrets.py` passes (no real credentials staged).
- Docs are updated when behavior changes.

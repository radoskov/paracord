# Coding-Agent Coordination Guide

This file is for coding agents that will work on PaRacORD in parallel.

## Where the truth lives (S17)

- **Engineering source of truth: [`docs/reference/`](docs/reference/00_index.md)** — the 12-part
  reference (architecture, data model, services, API, pipelines, agent protocol, frontend,
  security, efficiency, workflows, revision register). It is written from the code; when you
  change the code, update the matching section in the same commit.
- **Product intent: `SPECIFICATION.md`** — what the product is meant to be (the destination
  document). Use it for feature semantics and product decisions; where it disagrees with the code
  on engineering details, `docs/reference/` wins (drift notes are being added at known points).
- `WORK_SPLIT.md` is the historical implementation partition; `docs/architecture/` was archived
  into the (gitignored) `documentation_archive.zip` on 2026-07-13 — do not look for it in the
  tree.

## Terminology: "paper" in the UI, "work" in code

The user-facing term for the primary library entity is **"paper"** (e.g., buttons say "New paper", toasts say "Paper added to shelf", error messages say "Paper not found").

In code, the same entity is consistently called **"work"**: the Python model is `Work`, the DB table is `works`, API URL prefix is `/api/v1/works`, schema classes are `WorkCreate`/`WorkRead`/`WorkUpdate`, client methods are `createWork()`/`listWorks()`, and `entity_type="work"` is stored as a discriminator in TagLink, MetadataAssertion, Embedding, Summary, and Annotation rows.

**Rule:** When writing new user-visible strings (button labels, toasts, error messages, placeholder text, tooltips, hint text, docstrings shown in Swagger), use "paper"/"papers". When writing code (variable names, function names, class names, DB columns, API path segments, JSON field names), use "work"/"works". Do not rename the code-level identifiers or DB discriminators to "paper".

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

## Before starting any work (e.g., after query from a user)

Read progress tracking documents: PROGRESS.md, CHANGELOG.md, ROADMAP.md
Before working on any larger feature, read SPECIFICATION.md and make sure you understand the feature! If anything is not clear enough, ask a human.

## After finishing a logical chunk of code

Update the progress tracking documents.
Add add relevant files to staging and create a commit with descriptive message (but not too long).
Don't put your credentials in the commit message (e.g., never put there "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>")

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

DO NOT PUT YOUR CREDENTIALS into the commit message (e.g., never put there "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>")

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

## Practical implementation hints (merged from HINTS_FOR_AGENTS.md, 2026-07-13)

### Design for slow workers

GROBID, OCR, embeddings, topic models, and local LLM jobs can be slow. Do not block page rendering
on them. Use import-batch status, per-work processing status fields, and the RQ queue (scopes above
`app_config.ai_scope_job_threshold` already route topic/summary requests to the worker).

### Prefer append-only provenance

For metadata, summaries, AI output, and extraction results, store provenance. Do not overwrite
without recording the old value or the source of the new value (MetadataAssertion is the pattern).

### Make scopes reusable

Many features need the same "scope → works" logic (library, rack, shelf, search result, selected
works, import batch, saved filter). Use `app/services/scope_resolution.py` — it returns a
composable query, applies the merged-shadow filter, and **requires** the caller to state its
`visible_ids` access-control context. Do not re-implement the joins per feature.

### Keep file identity separate from paper identity

Never assume one PDF equals one paper. The model must allow: one file → one work, one file →
multiple works, one work → multiple files, one work → multiple versions, one version → multiple
files.

### Keep security tests close to the agent

The local agent is the main filesystem security boundary. Test symlink escapes, path traversal
attempts, deleted files, renamed files, token failures, and attempts to request unknown file IDs.

### Make review queues explicit

Do not hide uncertainty. Use review queues for: duplicate candidates, version candidates, metadata
conflicts, multi-work file warnings, unresolved references, reference-dupe contradictions
(Admin → Reference dupes), failed extraction, missing abstracts, bad OCR/text layer.

### UI performance

For thousands of papers, use server-side pagination and virtualized tables. Graphs should default
to scoped subsets rather than rendering the entire library every time.

### API stability

The web frontend and local agent both talk to versioned endpoints under `/api/v1`. Avoid breaking
request/response schemas without documenting the migration (regenerate `backend/openapi.json`).

### Historical: vertical-slice bootstrap order

The original build guidance (admin/login → scan/teleport → GROBID/extract → citations/graphs) is
long complete; it is kept in git history only. New work should follow PROGRESS.md and the
workplans instead.

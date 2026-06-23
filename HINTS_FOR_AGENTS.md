# Hints for Coding Agents

## Start with vertical slices

Avoid implementing a giant data model without a runnable path. The first vertical slice should be:

```text
create admin -> login -> register agent -> scan one PDF -> teleport PDF -> create file/work placeholder -> audit events
```

The second vertical slice should be:

```text
run GROBID -> parse title/authors/abstract/references -> display paper -> export BibTeX
```

The third vertical slice should be:

```text
resolve local citations -> show shelf graph -> show citation contexts -> summarize scope
```

## Design for slow workers

GROBID, OCR, embeddings, BERTopic, and local LLM jobs can be slow. Do not block page rendering on them. Use import-batch status and per-work processing status fields.

## Prefer append-only provenance

For metadata, summaries, AI output, and extraction results, store provenance. Do not overwrite without recording the old value or the source of the new value.

## Make scopes reusable

Many features need the same scope logic:

```text
full library
rack
shelf
search result
selected works
import batch
```

Build a reusable `ScopeResolver` service rather than duplicating SQL in every feature.

## Keep file identity separate from paper identity

Never assume one PDF equals one paper. The model must allow:

```text
one file -> one work
one file -> multiple works
one work -> multiple files
one work -> multiple versions
one version -> multiple files
```

## Keep security tests close to the agent

The local agent is the main filesystem security boundary. Test symlink escapes, path traversal attempts, deleted files, renamed files, token failures, and attempts to request unknown file IDs.

## Make review queues explicit

Do not hide uncertainty. Use review queues for:

```text
duplicate candidates
version candidates
metadata conflicts
multi-work file warnings
unresolved references
failed extraction
missing abstracts
bad OCR/text layer
```

## UI performance

For thousands of papers, use server-side pagination and virtualized tables. Graphs should default to scoped subsets rather than rendering the entire library every time.

## API stability

The web frontend and local agent should both talk to versioned endpoints under `/api/v1`. Avoid breaking request/response schemas without documenting the migration.

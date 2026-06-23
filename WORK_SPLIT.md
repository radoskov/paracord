# Multi-Agent Work Split

This guide splits the implementation into independent work packages. Agents should avoid crossing ownership boundaries unless the task explicitly requires integration.

## Agent A: Backend platform and database

Owns:

```text
backend/app/main.py
backend/app/core/
backend/app/db/
backend/app/models/
backend/app/schemas/
backend/alembic/
config/server.example.yaml
scripts/bootstrap_admin.py
scripts/reset_admin_password.py
```

Initial tasks:

1. Implement settings loading.
2. Implement SQLAlchemy session management.
3. Create initial Alembic migration.
4. Implement health endpoint.
5. Implement first-admin bootstrap.
6. Implement server-console password reset.
7. Add tests for config and health.

Interfaces needed by others:

- DB session dependency.
- User model.
- Audit event helper.
- Settings object.

## Agent B: Authentication, authorization, and audit logging

Owns:

```text
backend/app/core/security.py
backend/app/api/v1/endpoints/auth.py
backend/app/services/audit.py
backend/app/models/user.py
backend/app/models/audit.py
backend/app/schemas/auth.py
```

Initial tasks:

1. Implement password hashing.
2. Implement login/logout/session or token flow.
3. Enforce no guest role.
4. Implement roles: owner, editor, reader.
5. Add audit events for login, logout, failed login, password reset, PDF view, export, import, metadata edit.
6. Add rate limiting or lockout hooks for failed login attempts.

Security constraints:

- No password recovery endpoint exposed to unauthenticated users.
- Password reset must require server-console execution or equivalent local operator action.

## Agent C: Local workstation agent

Owns:

```text
agent/paperracks_agent/
agent/systemd/
config/agent.example.yaml
backend/app/api/v1/endpoints/agents.py
backend/app/services/agent_protocol.py
```

Initial tasks:

1. Implement agent configuration.
2. Implement allowed-root canonicalization.
3. Implement folder scanning.
4. Compute SHA-256 and file metadata.
5. Send manifest to server.
6. Implement upload/teleport by file ID.
7. Implement optional PDF streaming by file ID.
8. Add token authentication and token rotation.

Security constraints:

- Agent must reject arbitrary raw path requests from server.
- Agent must expose only files indexed from configured roots.
- Symlink escapes from configured roots must be rejected by default.

## Agent D: Ingestion, storage, and duplicate/version detection

Owns:

```text
backend/app/services/storage.py
backend/app/services/duplicate_detection.py
backend/app/services/import_pipeline.py
backend/app/models/file.py
backend/app/models/work.py
backend/app/models/imports.py
backend/app/api/v1/endpoints/imports.py
backend/app/api/v1/endpoints/files.py
```

Initial tasks:

1. Implement managed library content-addressed storage.
2. Implement teleport receiver.
3. Implement import batch creation.
4. Implement exact duplicate detection by hash.
5. Implement DOI/arXiv/title-author-year duplicate candidates.
6. Implement multi-work file warning model.
7. Add review-queue records.

## Agent E: GROBID extraction and metadata enrichment

Owns:

```text
backend/app/services/grobid_client.py
backend/app/services/tei_parser.py
backend/app/services/metadata_enrichment.py
backend/app/workers/jobs.py
backend/app/models/citation.py
backend/app/models/metadata.py
```

Initial tasks:

1. Implement GROBID client.
2. Store raw TEI XML.
3. Parse header metadata.
4. Parse abstract, sections, bibliography, citation mentions, and coordinates.
5. Integrate DOI/Crossref/arXiv/OpenAlex/Semantic Scholar connectors behind source-specific modules.
6. Implement metadata assertions and canonical-field selection rules.

## Agent F: Citation graph and citation summaries

Owns:

```text
backend/app/services/citation_graph.py
backend/app/api/v1/endpoints/citations.py
backend/app/api/v1/endpoints/graph.py
backend/app/models/citation.py
backend/app/schemas/citation.py
```

Initial tasks:

1. Resolve extracted references to local works.
2. Build scoped graph queries for library, rack, shelf, and search result.
3. Expose nodes, edges, citation contexts, and missing references.
4. Implement shelf/rack citation summary skeleton.
5. Add graph export support.

## Agent G: Frontend UI

Owns:

```text
frontend/
```

Initial tasks:

1. Create authentication screens.
2. Create library paper table.
3. Create rack/shelf browser.
4. Create file/folder view.
5. Integrate PDF.js reader.
6. Create citation graph component.
7. Create duplicate/version review queue.
8. Create export dialog.
9. Create audit log admin view.

## Agent H: Export and bibliography formats

Owns:

```text
backend/app/services/export_service.py
backend/app/api/v1/endpoints/exports.py
backend/app/schemas/export.py
```

Initial tasks:

1. Define internal CSL JSON conversion.
2. Implement BibTeX/BibLaTeX export.
3. Implement RIS export.
4. Implement Markdown/plain-text/HTML bibliography rendering.
5. Implement export scope: work, selected works, shelf, rack, search result.
6. Audit export events.

## Agent I: Local AI, embeddings, and topic modeling

Owns:

```text
backend/app/services/summarization.py
backend/app/services/topic_modeling.py
backend/app/services/embeddings.py
backend/app/models/ai.py
backend/app/api/v1/endpoints/ai.py
```

Initial tasks:

1. Add embedding generation interface.
2. Add pgvector storage.
3. Add semantic search endpoint.
4. Add local LLM summary job interface.
5. Add human/external summary storage.
6. Add BERTopic integration for library, rack, shelf, and search-result scopes.
7. Store model provenance.

## Agent J: Documentation, tests, and developer experience

Owns:

```text
docs/
scripts/
Makefile
docker-compose.yml
README.md
PROGRESS.md
```

Initial tasks:

1. Keep docs synchronized with implemented behavior.
2. Maintain LaTeX manual.
3. Add deployment runbooks.
4. Add testing guide.
5. Add CI skeleton.
6. Add sample data generator.

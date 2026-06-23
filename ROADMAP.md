# Roadmap

## M0: Developer skeleton

- Docker Compose starts infrastructure.
- Backend health endpoint works. (started)
- Backend settings load from YAML plus environment overrides. (started)
- Initial database migration creates users and audit events. (started)
- First admin can be bootstrapped from the server console. (started)
- Login/logout creates and revokes server-side sessions. (started)
- Server-console credential recovery exists without a web reset endpoint. (started)
- Tests run through `make test`.
- Docs compile.

## M1: Agent-assisted import

- Agent registers with server.
- Agent scans configured roots.
- Server receives manifests.
- User can teleport a PDF to the server managed store.
- File/work placeholders are created.

## M2: GROBID extraction

- GROBID full-text extraction runs in background.
- Raw TEI stored.
- Header, abstract, references, and citation mentions parsed.
- Metadata assertions stored.

## M3: Library organization and reading

- Shelves/racks/tags CRUD.
- Work can be in multiple shelves.
- Shelf can be in multiple racks.
- PDF.js reader integrated.
- Separate annotations stored.

## M4: Citation graph and summaries

- Local reference resolution.
- Scoped citation graph.
- Citation context display.
- Shelf/rack citation summaries.

## M5: Export and interoperability

- BibTeX, BibLaTeX, RIS, CSL JSON, Markdown, HTML, and plain-text exports.
- Import from BibTeX/RIS/CSL JSON.
- Zotero-compatible interchange documented.

## M6: Local AI and topics

- Embeddings and semantic search.
- Local summaries.
- Human/external summaries.
- BERTopic keyword and topic suggestions.
- Shelf/rack topic summaries.

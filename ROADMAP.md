# Roadmap

This is a condensed mirror of the canonical milestone plan in `SPECIFICATION.md` §20.
The ordering is value-first: it front-loads the complete single-machine loop
(import → organize → extract → read → export, M1–M4), then adds the remote-machine
agent (M5) and the heavier analytical layers (M6–M7) before final hardening (M8).
`WORK_SPLIT.md` maps the work packages (A–J) onto these milestones.

## M0: Foundation (developer skeleton)

- Docker Compose starts infrastructure.
- Backend health endpoint works. (started)
- Backend settings load from YAML plus environment overrides. (started)
- Initial database migration creates users and audit events. (started)
- First admin can be bootstrapped from the server console. (started)
- Login/logout creates and revokes server-side sessions. (started)
- Roles: owner, editor, reader. (started)
- Non-health API stubs require bearer-token authentication. (started)
- Server-console credential recovery exists without a web reset endpoint. (started)
- Tests run through `make test`. Docs compile.

## M1: Core library, organization, and files

- Sources, files, locations, works, versions.
- Shelves/racks/tags CRUD; a work can be in multiple shelves, a shelf in multiple racks.
- Server-folder import (single-machine mode — usable without the agent).
- Fast first-page text/thumbnail preview (PyMuPDF) on import.
- Basic metadata search and filters.
- Library table, shelf view, rack view, file view, reading queue.

## M2: PDF extraction and metadata

- GROBID full-text extraction in background; raw TEI stored.
- Header, abstract, references, and citation mentions parsed.
- Deterministic keyword extraction (YAKE/KeyBERT).
- needs_ocr detection with optional OCRmyPDF fallback.
- Optional reference-parser fallback (anystyle/refextract).
- Crossref/arXiv/OpenAlex connectors; metadata assertions and conflict review.

## M3: Reader, annotations, and exports

- PDF.js reader; separate annotation storage; annotation/note search.
- References / citation-context tabs.
- BibTeX, BibLaTeX, RIS, CSL JSON, Markdown, HTML, plain-text exports.
- Import from BibTeX/RIS/CSL JSON; Zotero-compatible interchange documented.
- Citation key management; live always-current shelf/rack bibliography.

## M4: Duplicate, version, and multi-work review

- Exact, DOI/arXiv, fuzzy, and text-fingerprint duplicate detection.
- Version linking; multi-paper file links and segments.
- Review UI (merge / link as version / split / keep separate / ignore).

## M5: Local agent and teleport (remote machines)

- Agent registers with server and scans configured roots.
- Server receives manifests; remote import by file ID.
- Teleport a PDF to the server managed store with checksum verification.
- File streaming; agent revocation; path-isolation security tests.

## M6: Citation graph and summaries

- Local reference resolution; scoped citation graph (library/rack/shelf/search).
- Citation context display; related-papers suggestions.
- Shelf/rack citation summaries; missing-references view.

## M7: Local AI and topics

- Embeddings and pgvector storage; semantic search.
- Local summaries; human/external summaries (with provenance).
- BERTopic keyword and topic suggestions; shelf/rack topic summaries.
- Optional ML extraction path (Nougat/Marker) for hard documents.

## M8: Polish, backup, and deployment hardening

- Backup/restore; LAN deployment docs; security checklist.
- Performance tuning; error-handling polish; full E2E test suite.

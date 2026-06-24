# Roadmap

This is a condensed mirror of the canonical milestone plan in `SPECIFICATION.md` §20.
The ordering is value-first: it front-loads the complete single-machine loop
(import → organize → extract → read → export, M1–M4), then adds the remote-machine
agent (M5) and the heavier analytical layers (M6–M7) before final hardening (M8).
`WORK_SPLIT.md` maps the work packages (A–J) onto these milestones.

> **Current position:** M0 done; **M1 done and validated end-to-end**; **M2 extraction +
> metadata enrichment are live and validated on real arXiv PDFs** — GROBID via the RQ
> worker, then arXiv/Crossref enrichment that auto-corrects bad GROBID titles, with a
> provenance/conflict review surface. Raw TEI and citation mentions are now persisted.
> Next in M2/M4: citation-context API/UI surfacing and duplicate/version detection.
> See `PROGRESS.md` → "Start here (next agent)".
> The two unchecked M0 items (login rate limiting, in-app password change) are hardening and
> are deliberately deferred in favour of building the product.

## M0: Foundation (developer skeleton) — DONE (auth hardening deferred)

- Docker Compose builds and starts the stack (postgres, redis, api, agent). (done)
- Backend health endpoint, YAML+env settings, auth tables migration. (done)
- Server-console admin bootstrap and password reset; revocable sessions. (done)
- Roles owner/editor/reader with owner-only admin user management and audit log. (done)
- Bearer-token auth on non-health routes. (done)
- `make test` runs in the api container (Python 3.12). (done)
- Deferred (hardening, not blocking): login rate limiting / failed-login lockout;
  in-app `change-password` endpoint with session revocation.

## M1: Core library, organization, and files — DONE (validated end-to-end)

- Sources, files, locations, works, versions.
- Shelves/racks/tags CRUD; a work can be in multiple shelves, a shelf in multiple racks.
- Server-folder import (single-machine mode — usable without the agent).
- Fast first-page text/thumbnail preview (PyMuPDF) on import.
- Basic metadata search and filters.
- Library table, shelf view, rack view, file view, reading queue.

## M2: PDF extraction and metadata — IN PROGRESS

- GROBID TEI parser + provenance-aware persistence (assertions, references, canonical-field
  promotion) + migration `0004`. (done)
- Background RQ worker + enqueue-on-import + live GROBID. (done, validated on real arXiv PDFs)
- Header, abstract, references parsed. (done) Raw-TEI storage and citation mention
  persistence. (done) Citation-context API/UI surfacing. (next)
- Metadata enrichment connectors: arXiv + Crossref (by identifier) with a provenance/
  conflict review surface and auto-correction of GROBID metadata. (done, validated live)
  OpenAlex/Semantic Scholar and fuzzy title lookup still to do.
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

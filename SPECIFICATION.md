# PaperRacks Software Specification

**Project codename:** PaperRacks  
**Document type:** implementation specification and multi-agent build guide  
**Target platform:** Linux server and Linux workstation/agent, with web client usable from any modern browser  
**Document date:** 2026-06-23  
**Status:** v1.0 implementation-ready draft

## Contents

1. Executive summary
2. Product goals and terminology
3. Architecture, deployment, security, and access control
4. Functional requirements
5. Data model and API specification
6. Local agent protocol
7. Processing pipelines
8. User interface specification
9. Search, export, performance, and configuration
10. Testing, milestones, and multi-agent implementation guide
11. Appendices and references

---

## 1. Executive summary

PaperRacks is a local-first, self-hostable scientific-paper library and literature-graph system. It is designed for hundreds to thousands of scientific PDFs, with long-term growth, fast runtime behavior, privacy-preserving filesystem access, citation-context extraction, local citation graphs, shelves/racks/tags organization, PDF reading, citation export, local AI summaries, and topic/keyword modeling.

The system intentionally avoids making Zotero the core database. Zotero-compatible import/export is supported through standard bibliography formats, but the canonical model is PaperRacks' own work/version/file graph.

The core design is:

```text
Browser UI
   -> PaperRacks Server API
      -> PostgreSQL + pgvector
      -> Redis/RQ background queue
      -> GROBID extraction service
      -> optional Ollama/local LLM service
      -> optional online metadata connectors
   -> PaperRacks Local Agent(s)
      -> configured local folders only
      -> file hashing, manifesting, upload/teleport, optional streaming
```

The server may run on the same PC as the PDFs or on another machine in the local network. When the PDFs live on a workstation and the server is external, a small local agent runs on the workstation. The server never receives arbitrary filesystem access. It communicates with the agent using opaque file IDs and scoped tokens. The agent exposes only configured folders and can upload selected PDFs to the server's managed library store through a "teleport" action.

---

## 2. Product goals

### 2.1 Primary goals

1. Build and maintain a structured scientific paper library from PDFs, folders, arXiv/DOI links, and bibliographic files.
2. Keep PDFs in place by default, but allow selected PDFs to be copied to a managed server-side library folder.
3. Extract bibliographic metadata, abstracts, full text, references, citation mentions, and citation contexts.
4. Support shelves and racks as many-to-many organizational structures:
   - A paper can appear in multiple shelves.
   - A shelf can appear in multiple racks.
   - Tags can be applied to papers, shelves, racks, annotations, summaries, import batches, and topics.
5. Support duplicate and version detection, including same work with different files, arXiv versions, publisher versions, and files containing multiple papers.
6. Provide shelf/rack/library-scoped citation graphs and citation-context summaries.
7. Provide fast search and filtering across metadata, tags, authors, shelves, racks, abstracts, summaries, notes, citation contexts, and full text.
8. Provide an integrated PDF reader with separate annotations stored outside the PDF.
9. Export citations and bibliographies for individual papers, selected papers, shelves, racks, and search results in standard formats.
10. Provide local AI summaries, external/human summaries, keyword suggestions, topic modeling, and semantic similarity search.
11. Work well on Linux, especially Ubuntu, and support a LAN-accessible web interface with authentication and audit logging.

### 2.2 Non-goals for the first complete version

1. No anonymous or guest read-only access.
2. No mandatory cloud storage.
3. No mandatory Zotero database dependency.
4. No collaborative real-time editing.
5. No attempt to defeat publisher access controls or download paywalled PDFs without authorization.
6. No arbitrary remote filesystem browser.
7. No writing annotations directly into the PDF as the primary storage mode.
8. No promise of perfect citation parsing; extraction confidence and review queues are required.

---

## 3. Terminology

| Term | Meaning |
|---|---|
| Work | The conceptual scholarly paper or intellectual unit. |
| Version | A specific version of a work, such as arXiv v1, arXiv v2, accepted manuscript, publisher version, corrected version. |
| File | A physical file instance, usually a PDF. |
| File segment | A page range or internal part of a file that corresponds to one work when a file contains multiple papers. |
| Location | A path, remote URL, arXiv link, DOI resolver, uploaded copy, managed library path, or agent-owned file reference. |
| Shelf | A user-defined collection of works. A work can appear in many shelves. |
| Rack | A user-defined collection/grouping of shelves. A shelf can appear in many racks. |
| Tag | A label applied to works, shelves, racks, files, notes, annotations, import batches, summaries, and topics. |
| Citation context | The sentence/paragraph/page/coordinates where one work cites another. |
| Teleport | Copying a PDF from a workstation/agent or local source into the server's managed library store. |
| Local agent | A small process running on the machine that owns local PDFs. It watches allowed folders and communicates with the server by file ID. |
| Managed library store | Server-side content-addressed storage for selected PDFs. |
| Metadata assertion | A candidate metadata value with a source and confidence, such as GROBID title, Crossref DOI, or user-corrected venue. |

---

## 4. Design principles

1. **Local-first:** The user's data is stored locally by default. Online metadata enrichment is opt-in per service and auditable.
2. **No arbitrary filesystem browsing:** The server and browser never get raw unrestricted filesystem access.
3. **Work/version/file separation:** A paper, a version, and a file are different things.
4. **Metadata provenance:** External sources do not silently overwrite local or user-corrected data.
5. **Incremental processing:** Imports appear quickly with partial metadata; expensive extraction and summaries run in background jobs.
6. **Fast UI:** No bloated desktop GUI is required. The core UI is a web app with virtualized tables and lazy-loaded graph/PDF components.
7. **Replaceable workers:** GROBID, topic modeling, LLM summarization, and metadata connectors are services/modules that can be upgraded independently.
8. **Review queues, not destructive automation:** Duplicates, version conflicts, multiple-paper files, and metadata conflicts are surfaced for user review.
9. **Reproducibility:** Import batches, extraction versions, model versions, prompts, source metadata, and user decisions are recorded.
10. **Secure LAN mode:** LAN access requires authentication, audit logging, explicit bind configuration, and no guest account.

---

## 5. Recommended technology stack

### 5.1 Required stack

| Layer | Choice | Reason |
|---|---|---|
| Backend API | FastAPI | Python-native, typed API models, OpenAPI generation, straightforward auth patterns. |
| Database | PostgreSQL | Reliable relational core, full-text search, JSONB, indexing, and strong transactional behavior. |
| Vector search | pgvector | Stores embeddings in PostgreSQL next to relational data. |
| Background jobs | Redis + RQ | Simple Python job queue for extraction, enrichment, embeddings, summaries, imports, exports. |
| PDF extraction | GROBID service | Scholarly PDF metadata, references, full-text structure, TEI XML, citation/reference coordinates. |
| Fast PDF preview | PyMuPDF (fitz) | Fast first-page text, page counts, thumbnails, and rendering for instant import preview before the slower GROBID job runs. |
| Keyword extraction | YAKE or KeyBERT | Lightweight deterministic keywords from title/abstract/headings without spinning up the full topic-modeling stack. |
| PDF viewer | PDF.js | Browser-native PDF rendering and annotation overlay. |
| Frontend | SvelteKit or React; choose SvelteKit for MVP | Lightweight web client, usable from Linux/macOS/Windows browsers. |
| Graph UI | Cytoscape.js, with vis-network as fallback | Interactive citation graph and graph analytics. |
| Local AI runner | Ollama first; llama.cpp later if needed | Simple local model serving and embeddings. |
| Topic modeling (optional) | BERTopic | Opt-in interpretable topics (transformer embedding + c-TF-IDF). Off by default; lightweight keyword extraction is the default. |
| Citation formatting | CSL JSON + citeproc + BibTeX/BibLaTeX/RIS exporters | Standard bibliography interoperability. |
| Deployment | Docker Compose | Repeatable local/server deployment. |
| Migrations | Alembic | Controlled schema evolution. |

### 5.2 External references supporting stack choices

- GROBID exposes REST endpoints, TEI output, consolidation options, PDF coordinates, and extraction parameters suitable for this project. See GROBID REST/API and coordinates documentation: https://grobid.readthedocs.io/en/latest/Grobid-service/ and https://grobid.readthedocs.io/en/latest/Coordinates-in-PDF/
- GROBID metadata consolidation can use Crossref or biblio-glutton and can add DOI-only or update metadata depending on configuration: https://grobid.readthedocs.io/en/latest/Consolidation/
- PDF.js is a web standards-based platform for parsing and rendering PDFs: https://mozilla.github.io/pdf.js/
- FastAPI documents JWT/password hashing patterns appropriate for the auth layer: https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/
- PostgreSQL supports built-in full-text search: https://www.postgresql.org/docs/current/textsearch.html
- pgvector provides vector similarity search inside PostgreSQL: https://github.com/pgvector/pgvector
- BERTopic uses transformer embeddings and c-TF-IDF for interpretable topic modeling: https://bertopic.readthedocs.io/en/latest/index.html
- Ollama exposes local APIs for generation and embeddings: https://docs.ollama.com/api/embed
- CSL provides a large open ecosystem for citation/bibliography formatting: https://citationstyles.org/
- Crossref, arXiv, OpenAlex, and Semantic Scholar provide useful scholarly metadata APIs: https://www.crossref.org/documentation/retrieve-metadata/rest-api/, https://info.arxiv.org/help/api/user-manual.html, https://developers.openalex.org/api-reference/introduction, https://www.semanticscholar.org/product/api

### 5.3 Supporting and optional tools

All of these are open-source and run locally. They are integrated behind replaceable interfaces so the system degrades gracefully when one is absent.

| Tool | Role | Default | Notes |
|---|---|---|---|
| PyMuPDF (fitz) | Fast first-page text, page count, thumbnails, rendering | on | Powers the instant import preview and the reader thumbnail strip; GROBID still does the authoritative structured extraction in the background. |
| OCRmyPDF + Tesseract | OCR fallback for PDFs with poor/no text layer | optional | OCRmyPDF wraps Tesseract. Produces a derivative OCR copy; never overwrites the original (see 8.3). |
| YAKE / KeyBERT | Deterministic keyword extraction | on (YAKE) | YAKE is dependency-light; KeyBERT reuses the embedding model when embeddings are enabled. |
| sumy (TextRank/LexRank) | Lightweight extractive section summaries | optional, on-demand | Pure-Python sentence ranking over GROBID Method/Experiment/Results sections; no LLM/GPU. The non-LLM tier of the body summary. |
| Ollama (local LLM) | Abstractive structured summaries | opt-in, off | Tier-2 body summary (rewritten Method/Experiment-Data/Results); runs locally, no data leaves the machine. Already the AI provider (5.1). |
| anystyle or refextract | Reference-string parsing fallback | optional | Used when GROBID reference parsing is low-confidence; results stored as competing assertions, never silent overwrites. |
| biblio-glutton | Self-hosted metadata consolidation backend | optional | Lets GROBID consolidation run against a local service instead of the public Crossref API. |
| Nougat / Marker | ML PDF→Markdown for math-heavy / poorly-structured papers | optional, off | Heavier ML models; offered as an alternative extraction path for documents GROBID handles poorly. |
| Zotero translation-server | URL/arXiv/DOI → metadata resolution | optional | Reuses Zotero's large translator set instead of hand-writing every connector; runs locally. |

All optional tools obey the same privacy rule as the rest of the system (see 7.8): they process only the files/identifiers handed to them and never read outside configured roots or export collection/filesystem data.

---

## 6. Deployment modes

### 6.1 Single-machine mode

Use this when the server and PDFs are on the same Ubuntu PC.

```text
Browser -> Server API -> allowed local folders
                    -> managed library store
                    -> GROBID/Ollama/PostgreSQL
```

The server process may index only folders listed in the server configuration. Raw path access through HTTP APIs is forbidden.

### 6.2 External LAN server + local workstation agent

Use this when the server runs on another PC in the local network and PDFs remain on the user's workstation.

```text
Browser -> Server API on LAN
          -> PostgreSQL / workers / GROBID / managed store
          -> Agent API on workstation
                -> configured workstation folders only
```

The workstation agent:

1. watches configured folders;
2. computes file hashes and manifests;
3. uploads metadata and file manifests to the server;
4. streams selected PDFs only by opaque file ID;
5. teleports selected PDFs to the server managed store;
6. never accepts arbitrary path requests from the server;
7. authenticates to the server using a scoped token.

### 6.3 Managed library mode

When a PDF is teleported, it is copied into the server's content-addressed store:

```text
server_library/
  sha256/
    ab/
      cd/
        abcdef....pdf
```

The original source path remains recorded as a `Location`, but the server can now serve/read the managed copy directly.

### 6.4 Recommended Docker Compose services

```yaml
services:
  api: PaperRacks FastAPI server
  web: SvelteKit frontend, or static frontend served by api/nginx
  worker: RQ worker for extraction/enrichment/AI/export jobs
  postgres: PostgreSQL with pgvector
  redis: job queue and cache
  grobid: GROBID service, internal network only
  ollama: optional local LLM/embedding service
  nginx_or_caddy: optional LAN reverse proxy with TLS
```

GROBID and Ollama must not be directly exposed to the LAN by default. They should be reachable only by the backend/worker network.

---

## 7. Security and access-control specification

### 7.1 Access model

1. There is no anonymous access.
2. There is no guest read-only access.
3. Every user must have an explicit named account.
4. MVP roles:
   - `owner`: full system control, user management, credential recovery configuration, backups, deletion.
   - `editor`: library editing, imports, metadata changes, annotations, exports, and shelf/rack management.
   - `reader`: authenticated reading, searching, and export where enabled by owner policy.
5. Optional post-MVP roles may include custom permissions, but no default guest role shall be shipped.

### 7.2 Authentication

1. Use username + password login.
2. Store password hashes using Argon2id or bcrypt with strong parameters.
3. Use secure HTTP-only cookies or signed bearer tokens.
4. Require login for every endpoint except health checks explicitly marked public.
5. Disable account enumeration in login and recovery flows.
6. Rate-limit login attempts per IP and per username.
7. Invalidate active sessions after password reset or credential recovery.
8. Provide optional local-network-only IP allowlist.

### 7.3 Credential recovery on server PC

Credential recovery must be possible only with OS-level access to the server PC or server container host.

Required recovery mechanisms:

```text
paperracks admin list-users
paperracks admin reset-password --user <username>
paperracks admin create-owner --user <username>
paperracks admin revoke-sessions --user <username>
```

Rules:

1. Web-based password recovery is disabled by default.
2. Server-side CLI recovery requires local shell access as the app owner user or root.
3. CLI recovery writes an audit event with timestamp, host, OS user, target app user, and action.
4. After password reset, all sessions for that user are revoked.
5. First-run bootstrap creates exactly one owner account.
6. A sealed emergency recovery token may be generated at first run and stored only in the server data directory, but CLI reset remains the primary recovery method.

### 7.4 Filesystem isolation

1. No API endpoint may accept an arbitrary path and then read that path.
2. All file access must use `file_id`, `source_id`, `location_id`, or `agent_file_id`.
3. All local source roots must be explicitly configured.
4. Path canonicalization is mandatory before any read/write.
5. Symlinks that escape an allowed root are rejected by default.
6. Absolute paths must not be exposed to normal LAN clients; display path aliases instead.
7. The server can read only:
   - configured server roots;
   - the managed library store;
   - files streamed/uploaded by trusted agents.
8. The agent can read only configured agent roots.
9. The browser never receives a raw local path as a read capability.

### 7.5 Agent security

1. Each agent has a unique ID, name, public key or token, and capability set.
2. Agent enrollment requires owner approval.
3. Agent tokens are scoped and revocable.
4. Agents maintain an allowlist of local folders.
5. Server requests to agents use opaque file IDs.
6. Agent denies requests for unknown file IDs or files outside configured roots.
7. Agent uploads/streams include checksum verification.
8. Agent heartbeat includes version, host alias, capabilities, and status, but not unrestricted filesystem details.

### 7.6 Audit logging

Audit events are append-only and queryable by owner.

Required event classes:

```text
auth.login_success
auth.login_failure
auth.logout
auth.password_change
auth.password_reset_cli
auth.session_revoked
user.created
user.role_changed
user.disabled
source.created
source.folder_added
source.agent_enrolled
file.imported
file.viewed
file.downloaded
file.teleported
paper.viewed
paper.metadata_edited
paper.exported
annotation.created
annotation.edited
shelf.created
shelf.modified
rack.created
rack.modified
metadata.enrichment_called
job.started
job.completed
job.failed
admin.backup_created
admin.restore_started
```

For browsing/reading activity, log at least:

```text
user_id
work_id nullable
file_id nullable
source: pdf_reader | metadata_page | graph | export
client_ip
user_agent
timestamp
```

### 7.7 Network exposure

1. Default bind is `127.0.0.1`.
2. LAN mode requires explicit configuration, e.g. `PAPERRACKS_BIND=0.0.0.0` and `PAPERRACKS_LAN_MODE=true`.
3. Production LAN deployment should use Caddy/nginx/Traefik with TLS where practical.
4. GROBID, PostgreSQL, Redis, and Ollama bind only to Docker internal network or localhost.
5. URL importers must block private/LAN IP ranges by default to avoid server-side request forgery.
6. Allow fetching from private URLs only if owner enables `allow_private_url_imports`.

### 7.8 Data egress and privacy

The system is local-first and built only from open-source, auditable components (GROBID, PostgreSQL, Redis, PDF.js, Ollama, BERTopic, and the supporting tools in 5.3). No component is a closed binary that could silently scan the host or exfiltrate data.

1. No component reads outside its configured roots (server roots, managed store, agent roots). There is no host-wide scanning.
2. The only outbound network traffic is **opt-in metadata enrichment and GROBID consolidation**, and it carries only **bibliographic identifiers** — titles, author names, DOIs, arXiv IDs, and raw reference strings.
3. The system never transmits PDF file contents, full text, annotations, notes, your collection/shelf/rack structure, filesystem paths, or any bulk export of your library to an external service.
4. Every external request is logged as a `metadata.enrichment_called` audit event (service, query type, entity IDs) so egress is fully visible to the owner.
5. Enrichment is configurable per service and can be fully disabled; consolidation may be pointed at a self-hosted biblio-glutton instance for zero third-party calls.
6. Secrets and credentials are never committed to version control; see `docs/runbooks/secrets_management.md`.

---

## 8. Functional requirements

### 8.1 Ingestion

The system shall ingest:

1. single PDF files;
2. folders containing PDFs;
3. watched folders;
4. agent-owned folders;
5. arXiv IDs and arXiv URLs;
6. DOI strings and DOI URLs;
7. publisher/open-access URLs;
8. BibTeX;
9. BibLaTeX;
10. RIS;
11. CSL JSON;
12. CSV/TSV metadata lists;
13. Zotero-compatible exports where practical.

Each ingestion creates an `ImportBatch` with source type, timestamp, user, agent/server source, errors, warnings, processing status, and generated work/file IDs.

### 8.2 PDF extraction

Required GROBID extraction tasks:

1. header metadata extraction;
2. full-text TEI extraction;
3. abstract extraction;
4. section/paragraph extraction;
5. bibliography extraction;
6. raw reference string retention;
7. in-text citation callout extraction;
8. citation/reference coordinates when available;
9. sentence segmentation when citation-context extraction is enabled;
10. storage of raw TEI blob for reprocessing.

When GROBID reference parsing is low-confidence, an optional reference-string parser fallback (anystyle or refextract) may re-parse the raw reference. Fallback results are stored as competing metadata assertions and never silently overwrite GROBID or user data. For math-heavy or poorly-structured PDFs that GROBID handles badly, an optional ML extraction path (Nougat or Marker) may be enabled per import profile.

GROBID options shall be configurable per import profile:

```text
consolidateHeader: 0 | 1 | 2
consolidateCitations: 0 | 1 | 2
includeRawCitations: true
includeRawAffiliations: true/false
teiCoordinates: configurable list
segmentSentences: true for citation context
```

External metadata consolidation is allowed, but all results must be recorded as metadata assertions with provenance.

### 8.3 OCR fallback

Scanned PDFs are expected to be rare, so OCR is optional in MVP.

Requirements:

1. detect PDFs with missing/poor text layers;
2. show `needs_ocr` warning;
3. optionally run OCRmyPDF (which wraps Tesseract) if installed and enabled;
4. preserve the original PDF;
5. store OCR output as a derivative file or managed copy, not as a replacement unless user confirms.

### 8.4 Duplicate and version detection

The system shall detect:

1. exact duplicate file by SHA-256;
2. same work with different files by DOI;
3. same work with different arXiv version by arXiv base ID;
4. likely duplicate by normalized title + author + year;
5. likely duplicate by text fingerprint;
6. possible multi-paper file by multiple title-like first pages, proceedings-like structure, or user-declared segmentation;
7. same paper with preprint and published version;
8. same local file reached through different paths.

All duplicate/version findings go into review queues. The system shall not auto-delete files.

User choices:

```text
merge as same work
mark as different version
mark as duplicate file
keep as distinct works
split file into multiple works
ignore warning
```

### 8.5 Multi-paper files

The `File` field must account for the possibility that one physical file contains multiple papers.

Requirements:

1. A file may link to multiple works through `FileWorkLink`.
2. A file segment may define page ranges per work.
3. UI shows a warning badge: `file contains multiple works`.
4. Warning is non-critical and can be user-confirmed.
5. A work may link to multiple files.
6. A file may represent a proceedings volume, thesis, book chapter collection, or combined PDF.

### 8.6 Organization: shelves, racks, tags

Requirements:

1. Works can belong to multiple shelves.
2. Shelves can belong to multiple racks.
3. Tags can apply to works, shelves, racks, files, versions, import batches, notes, annotations, summaries, and topics.
4. Tags support color, description, optional hierarchy, and aliases.
5. Shelves support name, description, tags, priority, status, owner, and notes.
6. Racks support name, description, tags, included shelves, and summary state.
7. Deleting a shelf shall not delete works.
8. Deleting a rack shall not delete shelves or works.

### 8.7 Search and filtering

Search must support simple text search and structured query syntax.

Example query syntax:

```text
author:vaswani title:attention year:2017
shelf:"transformers" rack:"thesis chapter 2" tag:important
abstract:"self-attention" fulltext:"positional encoding"
cites:"Attention Is All You Need"
cited_by_local:>3 has:pdf has:grobid has:summary
file:multiwork duplicate:possible version:preprint
```

Searchable fields:

```text
title
authors
affiliations
abstract
summary
full text
section headings
reference strings
resolved references
citation contexts
notes
annotations
tags
shelves
racks
DOI
arXiv ID
venue
year
file path alias
import batch
reading status
processing status
```

### 8.8 PDF reading and annotations

Requirements:

1. PDF reader embedded in the web app using PDF.js.
2. Ability to open in external reader via local helper/agent when feasible.
3. Page navigation, text search, zoom, page thumbnails, citation-jump links.
4. Separate annotations stored in the database, not written into PDFs by default.
5. Annotation types:
   - highlight;
   - note;
   - free text note;
   - page anchor;
   - citation-context note;
   - tag annotation;
   - reading status update.
6. Annotations store coordinates, page number, selected text, comment, author, timestamps, and link to work/file/version.
7. Export annotations to Markdown, JSON, and optionally PDF annotation formats later.

### 8.9 Citation graph

The system shall construct local citation graphs from extracted references and resolved local works.

Graph scopes:

```text
full_library
rack
shelf
search_result
selected_papers
import_batch
custom_filter
```

Graph modes:

```text
local_only
include_unowned_references
include_online_citing_papers_if_enrichment_enabled
collapse_versions
show_versions
show_file_nodes
show_missing_references
```

Edge context modes:

```text
none
first_context
all_contexts
summarized_contexts
```

Graph visual encodings:

```text
node color by shelf/tag/topic/status
node size by local citation count / degree / PageRank
edge thickness by number of citation mentions
edge style by direct citation / unresolved reference / external citation
warning badges for duplicate, unresolved, multiwork file, metadata conflict
```

### 8.10 Citation context

Citation context is a first-class feature.

For each citation mention, store:

```text
citing_work_id
cited_reference_id
resolved_cited_work_id nullable
section_id nullable
paragraph_id nullable
sentence_id nullable
page
marker_text
context_before
context_sentence
context_after
pdf_coordinates nullable
extraction_confidence
source_tei_id
```

UI behavior:

1. In paper reader, clicking an in-text citation shows resolved reference and all contexts.
2. In reference list, clicking a reference shows all places it is cited in the paper.
3. In graph view, hovering an edge shows representative citation contexts.
4. In shelf/rack summary, citation contexts are grouped by cited paper and theme.

### 8.11 Citation summaries across shelves and racks

For every shelf and rack, generate citation summaries:

```text
papers in scope
internal citation count
outgoing citation count
incoming local citation count
missing frequently cited works
top local bridge papers
most cited local papers
most cited external references
citation context themes
method/background/critique/baseline citation categories if classified
chronological distribution of cited works
papers isolated from the citation graph
papers connecting multiple shelves
```

The summary generator must support:

```text
library-wide summary
rack summary
shelf summary
custom search result summary
selected papers summary
```

Summaries shall be cached, versioned, and refreshable.

### 8.12 Metadata enrichment

External metadata enrichment is allowed and expected, but must avoid pollution.

Sources:

```text
GROBID
Crossref
arXiv
OpenAlex
Semantic Scholar
Unpaywall optional
user edits
imported BibTeX/RIS/CSL JSON
```

Rules:

1. Store every external field as a metadata assertion.
2. Do not silently overwrite user-confirmed fields.
3. External title must pass normalized similarity checks before updating canonical title.
4. DOI match can attach DOI, but author/title/year conflicts become warnings.
5. Crossref/arXiv/OpenAlex/Semantic Scholar data shall be stored with timestamps and source names.
6. User can inspect competing assertions and choose canonical fields.
7. User edits are highest priority by default.
8. Metadata source trust level is configurable.
9. Every external request shall be logged as `metadata.enrichment_called`, including service, query type, and work/reference IDs.

### 8.13 Citation export

Export targets:

```text
single paper
selected papers
shelf
rack
search result
citation graph neighborhood
missing references list
import batch
```

Export formats:

```text
BibTeX
BibLaTeX
RIS
CSL JSON
EndNote XML post-MVP
CSV/TSV
Markdown bibliography
plain text bibliography
HTML bibliography
LaTeX cite commands
Pandoc Markdown citation list
```

Citation key requirements:

1. Generate stable citation keys.
2. Detect and resolve key collisions.
3. Allow user override of citation key.
4. Allow shelf/rack export with selected citation style.
5. Provide preview before export.
6. Provide `copy BibTeX`, `copy citation`, and `download export` actions.
7. Include unresolved-reference export with warning labels.

Canonical internal export representation should be CSL JSON plus PaperRacks-specific metadata.

### 8.14 Local AI summaries and external summaries

Summary types:

```text
extracted_abstract
extractive_summary
local_ai_summary
external_ai_summary
human_summary
imported_summary
shelf_summary
rack_summary
citation_context_summary
```

Summaries are tiered from lightweight to heavy so the body summary works with or without an LLM:

```text
Tier 0 (default, free):    extracted_abstract — GROBID's abstract, always available.
Tier 1 (lightweight, opt): extractive_summary — sentence-ranking (TextRank/LexRank via sumy)
                           over GROBID body sections; no LLM, no GPU. Produces per-section
                           extracts mapped to Method / Experiment-Data / Results.
Tier 2 (opt-in, heavier):  local_ai_summary — abstractive structured summary via the local
                           LLM provider (Ollama), giving rewritten Method / Experiment-Data /
                           Results prose plus the fuller schema below.
```

Tier 1 is local, fast, and dependency-light; Tier 2 is off by default and enabled only when the user opts into local-LLM summaries. Both reuse GROBID's section segmentation to target the Method / Experiment-Data / Results split.

Requirements:

1. Never replace the abstract with an AI or extractive summary.
2. Store summary provenance:
   - model/provider;
   - prompt template ID;
   - source sections;
   - source text hash;
   - generation timestamp;
   - user who requested it;
   - parameters;
   - confidence/warnings.
3. Allow manual editing or separate human summary.
4. Allow external summary paste/import.
5. Local AI must work through a replaceable provider interface.
6. MVP provider: Ollama.
7. Later providers: llama.cpp server, external APIs if user enables them.

Recommended local summary pipeline:

```text
extract structured sections with GROBID
map sections to Method / Experiment-Data / Results
chunk by section, not arbitrary fixed text only
summarize each section (extractive by default; local LLM when opted in)
create structured paper summary
create shelf/rack summary from paper summaries + citation graph
create citation-context summary from citation sentences
store provenance
```

Recommended summary schema:

```text
research question
method
data/materials
main results
limitations
important references
how this paper is used in selected shelf/rack
key quotes/citation contexts
user notes
```

### 8.15 Topic modeling and keywords

Keyword extraction and topic modeling are two tiers. The **lightweight keyword extractor runs by default**; the **heavier BERTopic topic-modeling pipeline is optional and off by default**, enabled per scope when the user wants interpretable clusters.

Requirements:

1. Extract deterministic keywords from title, abstract, headings, and author keywords using a lightweight extractor (YAKE by default, KeyBERT when embeddings are enabled). This is the default and requires no GPU or transformer stack.
2. Generate embedding-based similar-paper suggestions ("related papers"), surfaced on the paper detail view and reusable as a graph/export scope.
3. Optionally run BERTopic (opt-in, disabled by default) for:
   - full library;
   - rack;
   - shelf;
   - search result;
   - import batch;
   - selected papers.
4. Store topic model parameters and model version.
5. Allow user to accept topic-derived suggested tags.
6. Allow creating shelves from topics.
7. Allow freezing a topic model to prevent accidental drift.
8. Allow rerunning topic models after substantial imports.

Suggested embedding models:

```text
EmbeddingGemma through Ollama or sentence-transformers
all-MiniLM-L6-v2 as lightweight fallback
SPECTER/SPECTER2-style scholarly embeddings later
```

Suggested local LLM candidates for less than 6 GB GPU:

```text
qwen3:4b through Ollama, listed at about 2.5 GB model size with a large context window
Gemma small models in quantized form where local runtime support is practical
CPU fallback for short summaries if GPU memory is insufficient
```

Use chunked summarization. Do not assume a small GPU can summarize entire PDFs in one prompt.

### 8.16 Backups, restore, and portability

Requirements:

1. Full backup command for database, managed files, config, and optional embeddings.
2. Restore command with dry-run mode.
3. Export/import complete library metadata as JSONL/SQLite package later.
4. Store original source paths as aliases so restoration on another machine can remap paths.
5. Managed library store is content-addressed and deduplicated by hash.
6. Agent configuration is not restored blindly onto another machine; it must be re-enrolled.

### 8.17 Additional usability features

These build on existing data (reading status, embeddings, annotations, exports) and add little new machinery.

1. **Reading queue.** A dedicated view backed by `reading_status` (`unread | skimmed | reading | read | important | revisit`) so the user can triage a to-read list across the whole library, a shelf, or a rack. Supports reordering and quick status changes.
2. **Related papers.** On each paper's detail view, surface embedding-based nearest neighbours (semantic similarity from 8.15) with the shelves/tags they already belong to. The related set can be promoted to a search/graph/export scope.
3. **Live shelf/rack bibliography.** A shelf or rack can expose an always-current bibliography (e.g. BibTeX) that re-renders when its works change, so it can be referenced directly while writing. Implemented on top of the export service (8.13) with cached, invalidate-on-change output.
4. **Annotation and note search ("where did I read X").** Full-text search over annotation text, highlighted selections, and notes (already listed as searchable fields in 8.7), with results linking back to the exact page/coordinates in the reader.

---

## 9. Data model

### 9.1 Entity overview

```text
User 1..* AuditEvent
User 1..* ImportBatch
Agent 1..* Source
Source 1..* Location
File 1..* Location
File *..* Work through FileWorkLink
File 1..* FileSegment
Work 1..* WorkVersion
Work *..* Shelf through ShelfWork
Shelf *..* Rack through RackShelf
Tag *..* Work/Shelf/Rack/File/Annotation/Summary/Topic through polymorphic tag links
Work 1..* Reference as citing work
Reference 0..1 Work as resolved cited work
Reference 1..* CitationMention
Work 1..* Summary
Work 1..* Annotation
Work 1..* MetadataAssertion
Work 1..* Embedding
TopicModel 1..* Topic
Topic *..* Work through WorkTopic
```

### 9.2 ID conventions

1. Use UUIDv7 or ULID-style sortable IDs for primary entities.
2. Use SHA-256 for file content identity.
3. Use normalized DOI, arXiv base ID, arXiv version ID, and external IDs as unique constraints where applicable.
4. Never use file paths as primary identifiers.

### 9.3 Core tables

#### users

```text
id
username unique
password_hash
display_name
email optional
role: owner | editor | reader
is_active
created_at
last_login_at
password_changed_at
```

#### agents

```text
id
name
host_alias
public_key_or_token_hash
status: pending | active | revoked | disabled
capabilities jsonb
last_seen_at
created_by_user_id
created_at
revoked_at nullable
```

#### sources

```text
id
type: server_folder | agent_folder | managed_library | upload | arxiv | doi | url | bibtex | ris | csl_json
name
owner_user_id nullable
agent_id nullable
path_alias nullable
canonical_root_hash nullable
config jsonb
created_at
is_active
```

#### files

```text
id
sha256
size_bytes
mime_type
original_filename
page_count nullable
text_layer_quality: unknown | good | poor | none
status: registered | available | missing | permission_denied | deleted | teleported
created_at
last_seen_at
```

#### locations

```text
id
file_id
source_id
location_type: server_path | agent_path | managed_path | uploaded | remote_url | arxiv_pdf | doi_resolved
path_encrypted_or_alias
path_display_alias
agent_file_id nullable
url nullable
is_primary
created_at
last_verified_at
```

#### works

```text
id
canonical_title
normalized_title
year nullable
canonical_abstract nullable
doi nullable
arxiv_base_id nullable
venue nullable
work_type: article | preprint | conference | thesis | book_chapter | proceedings | unknown
canonical_metadata_source
reading_status: unread | skimmed | reading | read | important | revisit
created_at
updated_at
user_confirmed boolean
```

#### work_versions

```text
id
work_id
version_label
version_type: arxiv | publisher | accepted_manuscript | preprint | corrected | unknown
arxiv_version nullable
doi nullable
publication_date nullable
source_metadata jsonb
created_at
```

#### file_segments

```text
id
file_id
page_start nullable
page_end nullable
label nullable
segment_type: full_file | paper | chapter | appendix | proceedings_entry | unknown
created_by: system | user
confidence
created_at
```

#### file_work_links

```text
id
file_id
work_id
version_id nullable
segment_id nullable
relationship_type: primary | contains | appendix | proceedings_chapter | ambiguous | duplicate_copy
page_start nullable
page_end nullable
confidence
warning_state: none | file_contains_multiple_works | work_has_multiple_files | unresolved
user_confirmed
created_at
```

#### shelves

```text
id
name
description
status: active | archived
created_by_user_id
created_at
updated_at
```

#### racks

```text
id
name
description
status: active | archived
created_by_user_id
created_at
updated_at
```

#### shelf_works

```text
shelf_id
work_id
added_by_user_id
added_at
position nullable
note nullable
```

#### rack_shelves

```text
rack_id
shelf_id
added_by_user_id
added_at
position nullable
```

#### tags and tag_links

```text
tags:
  id
  name
  normalized_name
  color nullable
  description nullable
  parent_tag_id nullable
  created_at

tag_links:
  tag_id
  entity_type: work | file | version | shelf | rack | annotation | summary | import_batch | topic
  entity_id
  created_by_user_id
  created_at
```

#### references

```text
id
citing_work_id
raw_reference
parsed_title nullable
parsed_authors jsonb
parsed_year nullable
parsed_venue nullable
parsed_doi nullable
parsed_arxiv_id nullable
resolved_work_id nullable
resolution_confidence
resolution_status: unresolved | local_match | external_match | ambiguous | ignored
source_tei_id nullable
created_at
```

#### citation_mentions

```text
id
citing_work_id
reference_id
resolved_cited_work_id nullable
section_id nullable
paragraph_id nullable
sentence_id nullable
page nullable
marker_text
context_before nullable
context_sentence nullable
context_after nullable
pdf_coordinates jsonb nullable
extraction_confidence
created_at
```

#### metadata_assertions

```text
id
entity_type: work | version | reference | author | venue
entity_id
field_name
field_value jsonb
source: grobid | crossref | arxiv | openalex | semantic_scholar | user | import | other
confidence
retrieved_at
selected_as_canonical boolean
conflict_status: none | conflicts | rejected | accepted
```

#### summaries

```text
id
entity_type: work | shelf | rack | citation_edge | citation_context_set | search_result
entity_id
summary_type: extracted_abstract | local_ai | external_ai | human | imported | shelf | rack | citation_context
content_markdown
model_provider nullable
model_name nullable
prompt_template_id nullable
source_hash nullable
source_entity_ids jsonb
created_by_user_id nullable
created_at
updated_at
is_current
```

#### annotations

```text
id
work_id
file_id nullable
version_id nullable
page nullable
coordinates jsonb nullable
selected_text nullable
annotation_type: highlight | note | page_anchor | citation_note | tag_note
content_markdown nullable
created_by_user_id
created_at
updated_at
```

#### import_batches

```text
id
created_by_user_id
source_id nullable
agent_id nullable
input_type
status: queued | running | completed | completed_with_warnings | failed | cancelled
settings jsonb
stats jsonb
created_at
started_at nullable
finished_at nullable
```

#### duplicate_candidates

```text
id
candidate_type: exact_file | same_doi | same_arxiv | fuzzy_title | text_fingerprint | multiwork_file
entity_a_type
entity_a_id
entity_b_type
entity_b_id
score
signals jsonb
status: open | accepted | rejected | ignored
created_at
resolved_by_user_id nullable
resolved_at nullable
```

#### embeddings

```text
id
entity_type: work | section | abstract | summary | citation_context
entity_id
model_name
dimensions
vector vector
text_hash
created_at
```

#### topic_models, topics, work_topics

```text
topic_models:
  id
  scope_type: library | rack | shelf | search_result | import_batch | selected
  scope_id nullable
  embedding_model
  algorithm: bertopic
  parameters jsonb
  status
  created_at

topics:
  id
  topic_model_id
  topic_number
  label
  keywords jsonb
  representative_work_ids jsonb
  created_at

work_topics:
  work_id
  topic_id
  score
  assigned_at
```

#### audit_events

```text
id
user_id nullable
event_type
entity_type nullable
entity_id nullable
client_ip nullable
user_agent nullable
agent_id nullable
details jsonb
created_at
```

---

## 10. API specification overview

The backend shall expose a versioned REST API under `/api/v1`. OpenAPI JSON must be generated and committed to the repository for frontend/agent client generation.

### 10.1 Auth endpoints

```text
POST /api/v1/auth/login
POST /api/v1/auth/logout
POST /api/v1/auth/change-password
GET  /api/v1/auth/me
POST /api/v1/auth/revoke-session
```

No web endpoint for anonymous password recovery in MVP.

### 10.2 User/admin endpoints

```text
GET    /api/v1/admin/users
POST   /api/v1/admin/users
PATCH  /api/v1/admin/users/{user_id}
POST   /api/v1/admin/users/{user_id}/disable
GET    /api/v1/admin/audit-events
POST   /api/v1/admin/backup
POST   /api/v1/admin/restore/dry-run
```

### 10.3 Source and agent endpoints

```text
GET    /api/v1/sources
POST   /api/v1/sources/server-folder
POST   /api/v1/sources/url
POST   /api/v1/sources/arxiv
POST   /api/v1/sources/doi
POST   /api/v1/agents/enroll-request
POST   /api/v1/admin/agents/{agent_id}/approve
POST   /api/v1/admin/agents/{agent_id}/revoke
GET    /api/v1/agents/{agent_id}/status
```

### 10.4 Work/file/import endpoints

```text
POST   /api/v1/imports/pdf
POST   /api/v1/imports/folder
POST   /api/v1/imports/bibtex
POST   /api/v1/imports/ris
POST   /api/v1/imports/csl-json
GET    /api/v1/imports/{batch_id}
POST   /api/v1/imports/{batch_id}/cancel
GET    /api/v1/files/{file_id}
GET    /api/v1/files/{file_id}/stream
POST   /api/v1/files/{file_id}/teleport
GET    /api/v1/works
GET    /api/v1/works/{work_id}
PATCH  /api/v1/works/{work_id}
GET    /api/v1/works/{work_id}/files
GET    /api/v1/works/{work_id}/references
GET    /api/v1/works/{work_id}/citation-contexts
POST   /api/v1/works/{work_id}/summaries
```

### 10.5 Organization endpoints

```text
GET    /api/v1/shelves
POST   /api/v1/shelves
GET    /api/v1/shelves/{shelf_id}
PATCH  /api/v1/shelves/{shelf_id}
POST   /api/v1/shelves/{shelf_id}/works/{work_id}
DELETE /api/v1/shelves/{shelf_id}/works/{work_id}
GET    /api/v1/racks
POST   /api/v1/racks
PATCH  /api/v1/racks/{rack_id}
POST   /api/v1/racks/{rack_id}/shelves/{shelf_id}
DELETE /api/v1/racks/{rack_id}/shelves/{shelf_id}
GET    /api/v1/tags
POST   /api/v1/tags
POST   /api/v1/tags/{tag_id}/link
DELETE /api/v1/tags/{tag_id}/link
```

### 10.6 Search endpoints

```text
GET  /api/v1/search?q=...
POST /api/v1/search/advanced
POST /api/v1/search/semantic
GET  /api/v1/search/suggestions
```

### 10.7 Graph and citation summary endpoints

```text
POST /api/v1/graphs/citation
POST /api/v1/graphs/citation/summary
GET  /api/v1/graphs/work/{work_id}/neighborhood
GET  /api/v1/shelves/{shelf_id}/citation-summary
GET  /api/v1/racks/{rack_id}/citation-summary
POST /api/v1/citation-contexts/summarize
```

### 10.8 Export endpoints

```text
POST /api/v1/exports
GET  /api/v1/exports/{export_id}
GET  /api/v1/exports/{export_id}/download
POST /api/v1/exports/preview
```

Export request example:

```json
{
  "target_type": "shelf",
  "target_id": "...",
  "format": "bibtex",
  "style": null,
  "include_notes": false,
  "include_unresolved_references": false
}
```

---

## 11. Local agent protocol

### 11.1 Agent responsibilities

The local agent is a companion process running on a machine that owns PDFs. It is mandatory for external-server mode and optional for same-machine mode.

Responsibilities:

1. enroll with server;
2. maintain configured folder roots;
3. watch folders for PDFs;
4. compute file hashes and metadata;
5. send manifests to server;
6. upload selected PDFs;
7. stream selected PDFs for reading/extraction if allowed;
8. teleport PDFs to server managed store;
9. report heartbeat and job status;
10. deny arbitrary path requests.

### 11.2 Agent enrollment

1. Server owner creates enrollment token from UI.
2. User runs on workstation:

```bash
paperracks-agent enroll --server https://server.local:8443 --token <token> --name "my-laptop"
```

3. Agent sends public key or token proof and host alias.
4. Owner approves agent in admin UI.
5. Server issues scoped token.
6. Agent stores token locally with file permissions `0600`.

### 11.3 Agent folder registration

```bash
paperracks-agent add-root --name "Project A papers" --path /home/me/projects/a/papers
paperracks-agent add-root --name "Global papers" --path /home/me/papers
```

The agent stores canonicalized roots and refuses files outside them.

### 11.4 Agent-to-server messages

#### Heartbeat

```json
{
  "agent_id": "...",
  "version": "0.1.0",
  "host_alias": "my-laptop",
  "capabilities": ["manifest", "upload", "stream", "teleport", "watch"],
  "roots": [{"root_id": "...", "name": "Project A papers"}],
  "last_scan_at": "..."
}
```

#### File manifest

```json
{
  "agent_id": "...",
  "root_id": "...",
  "agent_file_id": "opaque-id",
  "path_alias": "Project A papers/transformers/paper.pdf",
  "sha256": "...",
  "size_bytes": 1234567,
  "mtime": "...",
  "page_count": 12,
  "mime_type": "application/pdf"
}
```

#### Teleport request

```text
POST /api/v1/files/{file_id}/teleport
```

Server creates an upload session. Agent uploads file chunks with checksum. Server verifies SHA-256 and stores in managed library.

### 11.5 Agent streaming

PDF streaming is allowed only if:

1. the file is already registered;
2. the requesting user is authenticated;
3. the server request is signed or uses a short-lived request token;
4. the agent recognizes the file ID;
5. the file is still under an allowed root and hash matches manifest.

---

## 12. Processing pipeline

### 12.1 Import pipeline

```text
1. Create import batch.
2. Register source and file/location manifest.
3. Hash file and inspect basic PDF metadata.
4. Detect text layer quality and page count (PyMuPDF).
5. Extract first-page text, a thumbnail, and identifiers quickly with PyMuPDF so the work appears in the library immediately, before the slower GROBID job.
6. Run duplicate/version precheck.
7. Create provisional Work and File records.
8. Queue GROBID extraction.
9. Parse TEI and persist structured data.
10. Queue external metadata enrichment if enabled.
11. Create metadata assertions and conflict warnings.
12. Resolve references to local works and external IDs.
13. Build/refresh local citation edges.
14. Create embeddings.
15. Suggest tags/topics.
16. Queue local summaries if configured.
17. Mark import batch complete or complete_with_warnings.
```

### 12.2 GROBID worker pipeline

```text
Input: file_id or managed/agent stream
Output: TEI blob + parsed records

1. Fetch/stream PDF.
2. Submit to GROBID processFulltextDocument.
3. Store raw TEI.
4. Parse header metadata.
5. Parse abstract and body sections.
6. Parse bibliography entries.
7. Parse citation markers and sentence contexts.
8. Store coordinates where available.
9. Emit events for metadata resolution and graph refresh.
```

### 12.3 Metadata enrichment pipeline

```text
1. Determine identifiers: DOI, arXiv ID, title/authors/year.
2. Query high-trust sources first: arXiv for arXiv IDs, Crossref for DOI.
3. Query OpenAlex/Semantic Scholar for graph enrichment and additional IDs.
4. Store assertions, not blind updates.
5. Run conflict detector.
6. Promote safe assertions to canonical fields when confidence is high and no user lock exists.
7. Send ambiguous cases to metadata conflict review.
```

### 12.4 Duplicate/version pipeline

```text
1. Exact hash match -> duplicate file candidate.
2. DOI match -> same work candidate.
3. arXiv base ID match -> same work/version candidate.
4. arXiv version mismatch -> version candidate.
5. Normalized title + author + year -> fuzzy duplicate candidate.
6. Text fingerprint similarity -> duplicate candidate.
7. Multiple strong title/abstract sections in one file -> multi-paper candidate.
8. Queue user review.
```

### 12.5 Citation graph pipeline

```text
1. For each reference, attempt local resolution.
2. Create directed edge citing_work -> cited_work when resolved.
3. Attach citation mention counts and contexts to edge.
4. For unresolved references, create external reference nodes in graph views.
5. Refresh graph metrics for affected scopes.
6. Invalidate shelf/rack citation summaries that depend on changed works.
```

### 12.6 AI summary pipeline

```text
1. Select scope: work, shelf, rack, citation-context set.
2. Gather structured source text.
3. Hash source text and context settings.
4. Split by sections and citation contexts.
5. Run local model through provider interface.
6. Validate output shape.
7. Store summary with provenance.
8. Mark summary current.
```

---

## 13. User interface specification

### 13.1 Main navigation

```text
Dashboard
Library
Reading queue
Shelves
Racks
Files
Graphs
Imports
Duplicates
Topics
Search
Exports
Admin
Settings
```

### 13.2 Dashboard

Cards:

```text
Total works
Total files
Works without PDF
Works with warnings
Pending imports
Failed jobs
Duplicate candidates
Metadata conflicts
Unresolved references
Recently added
Recently read
```

### 13.3 Library table

Columns:

```text
title
authors
year
venue
DOI/arXiv
shelves
racks
tags
reading status
files
warnings
local citation count
added date
summary status
```

Requirements:

1. virtualized table;
2. sortable columns;
3. saved filters;
4. bulk actions;
5. quick add to shelf/rack/tag;
6. export selected;
7. duplicate warning badges;
8. multi-file/multi-work indicators.

### 13.4 Shelf view

Features:

1. shelf metadata and tags;
2. papers in shelf;
3. shelf-specific notes;
4. citation summary;
5. graph limited to shelf;
6. topic modeling limited to shelf;
7. export shelf bibliography, including an always-current live bibliography (8.17);
8. compare with another shelf;
9. add/remove works.

### 13.5 Rack view

Features:

1. rack metadata and tags;
2. shelves in rack;
3. papers grouped by shelf;
4. option to collapse duplicates across shelves;
5. rack citation summary;
6. graph limited to rack;
7. topic modeling limited to rack;
8. export rack bibliography;
9. show bridge papers between shelves.

### 13.6 File view

Features:

1. server folder sources;
2. agent folder sources;
3. managed library store;
4. path aliases, not raw unrestricted paths;
5. file status and warnings;
6. work links;
7. teleport button;
8. re-scan folder;
9. verify file presence;
10. split multi-paper file into segments.

### 13.7 Paper detail view

Tabs:

```text
Overview
PDF
Metadata
References
Citation contexts
Graph neighborhood
Related papers
Notes/annotations
Summaries
Files/versions
Topics/tags
History/audit
```

The Overview/Related papers area surfaces embedding-based nearest neighbours (8.17) with their existing shelves/tags.

### 13.8 PDF reader view

Requirements:

1. PDF.js viewer;
2. highlight/citation overlays;
3. side panel with metadata, references, notes, citation contexts;
4. click citation marker -> reference detail;
5. click reference -> citation mentions;
6. add note/highlight;
7. show page anchors from citation contexts;
8. open external reader button if agent/local helper supports it.

### 13.9 Graph view

Controls:

```text
Scope: library | rack | shelf | search result | selected works
Node mode: local only | include unresolved references | include online citations
Version mode: collapse versions | show versions
Layout: force | hierarchical | radial | timeline
Color by: shelf | rack | tag | topic | status | warning
Edge labels: off | counts | context snippets
Context mode: none | first | all | summarized
```

Actions:

```text
open paper
add node to shelf
import missing reference
show citation contexts
summarize edge contexts
export graph bibliography
```

### 13.10 Duplicate/version review

Views:

1. exact duplicate files;
2. same DOI;
3. same arXiv base ID;
4. different arXiv versions;
5. fuzzy title matches;
6. text fingerprint matches;
7. multi-paper file candidates.

Actions:

```text
merge works
link as version
mark duplicate file
split file
keep separate
ignore
```

### 13.11 Admin UI

Owner-only.

Features:

```text
user management
agent enrollment/revocation
configured server roots
LAN bind warning/status
metadata connector settings
GROBID/Ollama status
job queue status
audit logs
backup/restore
credential recovery instructions
security warnings
```

---

## 14. Search query language

### 14.1 Basic query

A bare query searches title, authors, abstract, summary, full text, notes, tags, shelves, racks, and citation contexts.

```text
transformer positional encoding
```

### 14.2 Field filters

```text
author:<text>
title:<text>
abstract:<text>
summary:<text>
fulltext:<text>
venue:<text>
year:2017
year:>=2020
doi:<text>
arxiv:<text>
tag:<tag>
shelf:<shelf>
rack:<rack>
status:unread|skimmed|reading|read|important|revisit
has:pdf|summary|abstract|grobid|ocr|notes|annotations
file:multiwork|missing|managed|agent|server
warning:duplicate|metadata_conflict|multiwork|unresolved_reference
cites:<title/doi/arxiv>
cited_by_local:>N
```

### 14.3 Saved filters

Any query can be saved as a named dynamic view. Saved filters can be used as graph scopes and export targets.

---

## 15. Export behavior

### 15.1 Export target resolution

When exporting a rack, resolve works as the union of all works in all shelves in the rack. Duplicates across shelves are collapsed by Work ID unless `include_versions=true`.

When exporting a shelf, use the shelf's work order if defined, otherwise sort by first author/year/title.

When exporting selected works, use UI selection order if provided.

### 15.2 Export warnings

Exports must include warnings when:

1. work has no canonical title;
2. work has unresolved authors;
3. work has no year;
4. DOI conflicts exist;
5. duplicate unresolved work selected;
6. citation key collision was resolved automatically;
7. unresolved references are included.

### 15.3 Required export UX

1. Preview bibliography.
2. Preview BibTeX/BibLaTeX/RIS raw text.
3. Copy to clipboard.
4. Download file.
5. Save export job in audit/history.
6. Allow selecting CSL style for free-text bibliography.

---

## 16. Performance requirements

Target corpus: hundreds to thousands of PDFs.

### 16.1 UI performance

1. Library table first load under 2 seconds for 5,000 works on LAN after server is warm.
2. Search response under 500 ms for metadata/full-text queries after indexing, excluding semantic search.
3. PDF reader starts streaming first page under 2 seconds for local/managed files on LAN.
4. Graph view should default to scoped graph if full graph exceeds practical UI limits.
5. Large graph views must support filtering, clustering, and lazy expansion.

### 16.2 Processing performance

1. Imports are non-blocking.
2. File hash/pre-scan should complete before deep extraction.
3. GROBID jobs run concurrently according to CPU/memory configuration.
4. Local AI jobs run at low priority and are cancellable.
5. Topic modeling is batch-oriented and not run on every small change unless configured.

### 16.3 Indexing

Use:

```text
PostgreSQL B-tree indexes for identifiers and foreign keys
GIN indexes for full-text search
pgvector indexes for embeddings after corpus grows
materialized graph/stat summaries for shelves/racks
```

---

## 17. Configuration specification

Use YAML or TOML config plus environment variables.

Example:

```yaml
server:
  bind: "127.0.0.1"
  port: 8000
  lan_mode: false
  public_base_url: "http://127.0.0.1:8000"

security:
  password_hash: "bcrypt"   # implemented default; argon2id is an acceptable future upgrade
  session_ttl_hours: 24
  allow_guest: false
  login_rate_limit_per_minute: 5
  audit_retention_days: 365

storage:
  managed_library_path: "/srv/paperracks/library"
  allow_symlink_escape: false
  display_absolute_paths_to_members: false

metadata:
  enable_crossref: true
  enable_arxiv: true
  enable_openalex: true
  enable_semantic_scholar: false
  crossref_mailto: "user@example.com"
  trust_policy: "conservative"

extraction:
  grobid_url: "http://grobid:8070"
  consolidate_header: 2
  consolidate_citations: 2
  consolidation_backend: "crossref"   # or "biblio_glutton" for a local consolidation service
  biblio_glutton_url: null
  include_raw_citations: true
  segment_sentences: true
  extract_coordinates: true
  pdf_preview: "pymupdf"              # fast first-page text/thumbnails before GROBID
  keyword_extractor: "yake"          # or "keybert" when embeddings are enabled
  reference_parser_fallback: null    # null | "anystyle" | "refextract"
  enable_ocr_fallback: false
  ocr_engine: "ocrmypdf"
  advanced_extraction:               # optional ML PDF->markdown for hard documents
    nougat_enabled: false
    marker_enabled: false

ai:
  enable_local_llm_summaries: false   # opt-in Tier-2 abstractive summaries via local LLM
  provider: "ollama"
  ollama_url: "http://ollama:11434"
  summary_model: "qwen3:4b"
  embedding_model: "embeddinggemma"
  max_parallel_ai_jobs: 1

summaries:
  default_tier: "abstract"            # always-available GROBID abstract (free)
  extractive_enabled: true            # Tier-1 lightweight section summaries, no LLM
  extractive_engine: "textrank"       # via sumy (TextRank/LexRank)
  structured_sections: ["method", "experiment_data", "results"]

topic_modeling:
  enabled: false                      # opt-in; lightweight keyword extraction is the default
  algorithm: "bertopic"
  min_topic_size: 5
```

Agent config example:

```yaml
server_url: "https://paperracks-server.local:8443"
agent_name: "workstation"
roots:
  - id: "project-a"
    name: "Project A papers"
    path: "/home/me/projects/project-a/papers"
  - id: "global"
    name: "Global papers"
    path: "/home/me/papers"
streaming:
  enabled: true
teleport:
  enabled: true
watch:
  enabled: true
```

---

## 18. Error handling and warnings

### 18.1 User-visible warnings

```text
possible duplicate
same DOI as another work
same arXiv ID with different version
metadata conflict
file missing
agent offline
file contains multiple works
work has multiple files
unresolved references
GROBID extraction failed
PDF has poor/no text layer
summary stale
topic model stale
external metadata source unavailable
```

### 18.2 Retry behavior

1. Network errors to metadata services retry with exponential backoff.
2. GROBID failures retry once unless file is invalid.
3. Agent offline actions remain queued if appropriate.
4. Teleport resumes from chunks if possible.
5. User can manually rerun extraction, enrichment, summary, or topic jobs.

---

## 19. Testing and acceptance criteria

### 19.1 Unit tests

Required areas:

```text
path canonicalization
agent root enforcement
metadata normalization
DOI/arXiv parsing
duplicate scoring
citation key generation
query parser
CSL/BibTeX export
TEI parsing fixtures
auth password hashing/session behavior
audit event writing
```

### 19.2 Integration tests

Required areas:

```text
import PDF -> GROBID mock -> work/file/reference records
agent manifest -> server file records
teleport upload -> managed library file
metadata enrichment mock -> metadata assertions
search index update
citation graph scope filtering
export shelf/rack bibliography
credential reset CLI
```

### 19.3 End-to-end tests

Required scenarios:

1. first-run setup creates owner;
2. login required for library access;
3. add server folder;
4. enroll agent;
5. import PDF from agent folder;
6. teleport PDF to server;
7. view PDF in browser;
8. add paper to shelf and shelf to rack;
9. generate citation graph for shelf;
10. export shelf BibTeX;
11. create local summary using mocked LLM provider;
12. reset password from server CLI and verify old session revoked.

### 19.4 Security tests

1. API cannot read arbitrary path.
2. Symlink escape rejected.
3. Agent refuses unknown file ID.
4. Agent refuses file outside configured root.
5. No guest/anonymous access to library endpoints.
6. GROBID/Ollama not accessible from LAN through exposed ports in default compose.
7. Login rate limiting works.
8. Audit logs record reading/export events.
9. URL importer blocks private IPs unless explicitly enabled.

### 19.5 Performance tests

Synthetic dataset:

```text
1,000 works
1,500 files
10,000 references
25,000 citation mentions
100 shelves
20 racks
20,000 annotations/notes
```

Acceptance:

1. metadata search under 500 ms after warm-up;
2. library table page under 2 seconds;
3. graph query for a shelf under 2 seconds for 500 nodes/2,000 edges;
4. export 1,000 BibTeX entries under 5 seconds;
5. duplicate scan under acceptable background job time, not blocking UI.

---

## 20. Milestone plan

The ordering is value-first and dependency-sound: it front-loads the complete single-machine loop — import → organize → extract → read → export (Milestones 1–4) — so the tool is genuinely useful early, then adds the remote-machine agent (5) and the heavier analytical layers, citation graph (6) and local AI/topics (7), before final hardening (8). `ROADMAP.md` is a condensed mirror of this list and `WORK_SPLIT.md` maps the work packages onto it.

### Milestone 0: foundation

Deliverables:

```text
monorepo
Docker Compose
FastAPI skeleton
PostgreSQL + Alembic
Redis/RQ
SvelteKit shell
login/session/auth
owner bootstrap
server CLI
basic audit log
OpenAPI generation
```

Exit criteria:

1. server starts with one command;
2. owner can log in;
3. no anonymous library access;
4. CLI password reset works;
5. audit log records login and reset events.

### Milestone 1: core library, organization, and files

This milestone delivers the organizational heart of the product and is independent of GROBID, so it comes first. After it, the tool is already useful on the machine where the PDFs live (single-machine mode via server folders); the local agent for remote machines is added later in Milestone 5.

Deliverables:

```text
sources
files
locations
works
versions
shelves
racks
tags
server-folder import
fast first-page text/thumbnail preview (PyMuPDF)
basic metadata search and filters
library table
shelf view and rack view
file view
reading queue view
```

Exit criteria:

1. import folder of PDFs as file records with an immediate PyMuPDF preview;
2. create/edit works manually;
3. add works to multiple shelves and shelves to multiple racks;
4. tag works/shelves/racks;
5. search by title/authors/tags/shelves/racks;
6. browse via shelf/rack view, file view, and reading queue;
7. no arbitrary path endpoint exists.

### Milestone 2: PDF extraction and metadata

Deliverables:

```text
GROBID worker
TEI storage
header/abstract/reference parsing
citation mention parsing
metadata assertions
deterministic keyword extraction (YAKE/KeyBERT)
needs_ocr detection with optional OCRmyPDF fallback
optional reference-parser fallback (anystyle/refextract)
Crossref/arXiv/OpenAlex connectors
metadata conflict review
```

Exit criteria:

1. import PDF creates work metadata, abstract, and keywords;
2. references and citation mentions are extracted;
3. DOI/arXiv enrichment stored as assertions;
4. conflicts displayed and resolvable;
5. PDFs with poor text layers are flagged as needs_ocr.

### Milestone 3: reader, annotations, and exports

Deliverables:

```text
PDF.js reader
annotation storage
references/citation-context tabs
annotation/note full-text search
BibTeX/BibLaTeX/RIS/CSL JSON/Markdown/HTML/plain-text export
citation key management
live shelf/rack bibliography
```

Exit criteria:

1. user reads PDFs in browser;
2. annotations stored separately and are searchable;
3. shelf/rack bibliography exports work, including a live always-current bibliography;
4. audit log records PDF views and exports.

### Milestone 4: duplicate/version/multiwork review

Deliverables:

```text
exact duplicate detection
DOI/arXiv duplicate detection
fuzzy + text-fingerprint duplicate detection
version linking
multi-paper file links and segments
review UI
```

Exit criteria:

1. duplicate candidates are generated;
2. user can merge/link/ignore;
3. one file can link to multiple works with warning;
4. one work can link to multiple files.

### Milestone 5: local agent and teleport

Adds remote-machine support: importing PDFs that live on a different workstation than the server. Deferred to here because server-folder import already covers the same-machine case from Milestone 1.

Deliverables:

```text
agent enrollment
agent root config
manifest sync
agent import
file streaming
teleport to managed library
agent audit events
```

Exit criteria:

1. server on PC A imports files from agent on PC B;
2. server cannot request arbitrary path;
3. teleport copies PDF to server library with checksum verification;
4. agent can be revoked.

### Milestone 6: citation graph and summaries

Deliverables:

```text
local reference resolution
citation graph endpoint
graph UI
scope filters: library/rack/shelf/search
citation context extraction UI
shelf/rack citation summaries
missing references view
```

Exit criteria:

1. graph can be scoped to shelf/rack/full library;
2. edge hover shows citation contexts;
3. missing frequently cited works are listed;
4. shelf/rack citation summaries generate and refresh.

### Milestone 7: local AI and topics

Deliverables:

```text
Ollama provider
embedding generation
pgvector storage
semantic search
related-papers suggestions
extractive paper summaries (Tier 1, no LLM) — Method/Experiment/Results
optional local-LLM abstractive summaries (Tier 2, opt-in)
external/human summaries
optional BERTopic topic pipeline (off by default)
topic view
tag suggestions
shelf/rack topic summaries
optional ML extraction path (Nougat/Marker) for hard documents
```

Exit criteria:

1. extractive Method/Experiment/Results summaries generate without an LLM; optional local-LLM abstractive summaries generate when enabled;
2. user can paste external summaries;
3. semantic search returns similar papers;
4. when enabled, BERTopic runs on a shelf/rack and suggests tags (off by default).

### Milestone 8: polish, backup, deployment hardening

Deliverables:

```text
backup/restore
installer docs
LAN deployment docs
security checklist
performance tuning
error handling polish
full E2E test suite
```

Exit criteria:

1. owner can backup and restore;
2. LAN mode is documented and safe by default;
3. all critical tests pass;
4. system is usable as a personal production tool.

---

## 21. Multi-agent implementation guide

This section is written for multiple coding agents working in parallel.

### 21.1 Shared rules for all coding agents

1. The specification is the contract. If changing behavior, create an Architecture Decision Record in `docs/adr/`.
2. Do not modify database schema without an Alembic migration and migration test.
3. Do not change API response schemas without updating OpenAPI and generated clients.
4. Do not introduce an endpoint that accepts arbitrary filesystem paths for reading.
5. Do not add anonymous/guest access.
6. All file access must go through IDs and permission checks.
7. All user-visible imports, exports, PDF reads, and auth events must write audit events.
8. All background jobs must be idempotent or safely retryable.
9. Every module must include unit tests and at least one integration test where applicable.
10. Keep fixtures small and legally redistributable.

### 21.2 Repository layout

This is the actual repository layout the scaffold uses. `WORK_SPLIT.md` maps work packages onto these paths.

```text
paperracks/
  backend/
    app/
      main.py
      core/                config, security
      db/                  session, base
      models/              SQLAlchemy models
      schemas/             pydantic schemas
      api/v1/endpoints/    routers
      services/            domain services (storage, grobid, export, ...)
      workers/             RQ job runners
    alembic/               migrations
    tests/
  agent/
    paperracks_agent/      local workstation agent (CLI + client)
    tests/
  frontend/                web client (SvelteKit)
  config/                  *.example.yaml + local overlays (gitignored)
  scripts/                 admin/bootstrap/backup/secret-scan helpers
  docs/
    architecture/
    runbooks/
    agent_handoffs/
    latex/                 implementation manual sources
  docker-compose.yml
  Makefile
```

### 21.3 Work packages

The detailed per-agent work packages live in `WORK_SPLIT.md`, which is the single
source of truth for ownership boundaries and initial tasks. It defines packages A–J
mapped onto the repository layout in 21.2 and the milestones in section 20. This
specification defines *what* to build; `WORK_SPLIT.md` defines *who builds which part*.
Where the two ever disagree on scope or ownership, `WORK_SPLIT.md` wins.

---

## 22. Coordination plan for multiple coding agents

### 22.1 Contract-first workflow

1. Agent A creates OpenAPI skeleton and DB migration baseline.
2. Agents B/C/D implement core contracts first.
3. Frontend agents consume generated client only.
4. Any endpoint/schema change requires:
   - OpenAPI update;
   - migration if needed;
   - tests;
   - short ADR if behavior changes.

### 22.2 Branch strategy

```text
main: always runnable
feature/<agent-letter>-<short-name>
adr/<short-decision>
```

### 22.3 Pull request checklist

Every PR must answer:

```text
What spec section does this implement?
What endpoints/schema changed?
Are migrations included?
Are tests included?
Does this expose filesystem access?
Does this create audit events where required?
Does this preserve no-guest access?
Does this affect agent/server security?
```

### 22.4 Shared test fixtures

Use small synthetic or public-domain fixtures:

```text
minimal_pdf_with_text.pdf
minimal_pdf_two_papers.pdf
minimal_grobid_tei.xml
crossref_response.json
arxiv_response.xml
openalex_response.json
semantic_scholar_response.json
bibtex_sample.bib
ris_sample.ris
csl_json_sample.json
```

### 22.5 Dependency order

Work-package letters below refer to `WORK_SPLIT.md` (A–J), aligned to the milestones in section 20.

```text
Foundation (A) -> Auth/Audit (B) + Ingestion/Storage/File-safety (D) -> GROBID/Metadata (E)
  -> Reader/Export (H) -> Duplicate/Version review (D) -> Local agent (C)
  -> Citation graph (F) -> AI/Topics (I) -> Hardening (J)
```

Parallelization:

```text
B and D can start once A provides DB session, core models, settings, and the audit helper.
E can mock file input before D's storage is complete, then integrate.
G (frontend) can start against API mocks once the OpenAPI skeleton exists.
H (export) can start from the data model with mock works.
I (local AI) can start with provider interfaces and mock summaries.
C (local agent) starts once D defines the source/file contracts.
F (citation graph) starts after references and graph contracts exist (after E).
J (docs/tests/DX) runs continuously alongside every package.
```

---

## 23. Initial implementation tasks

### 23.1 First week target

```text
repository scaffold
compose up
PostgreSQL/Redis/API/Web running
owner bootstrap
login/logout
CLI reset password
basic audit log
initial schema migration
health/status page
```

### 23.2 Second target

```text
server folder source
file hashing
work/file manual creation
shelves/racks/tags
library table
basic search
managed store skeleton
```

### 23.3 Third target

```text
GROBID worker
TEI parser
metadata assertions
reference extraction
deterministic keyword extraction
PyMuPDF first-page preview/thumbnails
```

### 23.4 Fourth target

```text
PDF.js reader
separate annotation storage
BibTeX/CSL JSON export
exact + DOI/arXiv duplicate detection
```

### 23.5 Later target (remote machines)

```text
agent enrollment
agent folder manifest sync
remote import
teleport with checksum
file streaming
security tests for path isolation
```

---

## 24. Open decisions

These are the remaining choices before implementation starts.

1. Frontend framework: SvelteKit is recommended, but React is acceptable if the implementation team is stronger there.
2. Session type: HTTP-only cookie sessions are recommended for browser UI; bearer tokens are recommended for agents.
3. Exact password hashing library: Argon2id preferred; bcrypt acceptable if easier.
4. Whether to enable Semantic Scholar by default or keep it optional behind API key/rate-limit settings.
5. Whether to run GROBID `consolidateHeader`/`consolidateCitations` as `1` or `2` by default. Conservative default: `2` for DOI-only enrichment.
6. Whether to store absolute paths encrypted at rest or simply hide them from non-owner UI. For personal LAN use, hiding plus filesystem isolation is acceptable; encryption can be added later.
7. Whether managed library teleport keeps both original and managed locations as active or marks the managed copy as primary.

Recommended defaults:

```text
Frontend: SvelteKit
Browser auth: HTTP-only cookie session
Agent auth: scoped bearer token or mTLS later
Password hash: bcrypt (implemented; Argon2id acceptable as a future upgrade)
GROBID consolidation: DOI-only mode where possible
Semantic Scholar: optional, disabled by default
OpenAlex/Crossref/arXiv: enabled if user opts in during setup
Teleport: keep original location record, mark managed copy primary only if user chooses
```

---

## 25. Definition of done for v1.0

PaperRacks v1.0 is complete when:

1. User can deploy server on Ubuntu with Docker Compose.
2. User can run an agent on another Ubuntu PC.
3. User can enroll the agent and import PDFs from configured folders.
4. Server cannot browse arbitrary agent or server filesystem paths.
5. User can teleport PDFs from agent PC to server managed store.
6. User can view and search the library from LAN after login.
7. There is no guest/anonymous access.
8. Owner can recover credentials from server PC CLI.
9. PDF metadata, abstracts, references, and citation contexts are extracted with GROBID.
10. External metadata enrichment is stored with provenance and conflict warnings.
11. Works, versions, files, and multi-paper files are represented correctly.
12. Duplicates and versions can be reviewed and resolved.
13. Works can be organized into shelves and racks with tags.
14. PDF reader works in browser and stores separate annotations.
15. Citation graph can be scoped to library, rack, shelf, or search result.
16. Shelf/rack citation summaries can be generated.
17. Citations can be exported for papers, shelves, racks, and selections in standard formats.
18. Local summaries and external/human summaries are supported.
19. Topic modeling and keyword suggestions work for at least shelves and full library.
20. Authentication, browsing/reading, imports, exports, and admin actions are audited.
21. Backup and restore are available.
22. Security, integration, and E2E test suites pass.

---

## 26. Appendix A: example graph-scope request

```json
{
  "scope": {
    "type": "rack",
    "id": "rack_01"
  },
  "node_mode": "include_unowned_references",
  "version_mode": "collapse_versions",
  "edge_context_mode": "summarized_contexts",
  "filters": {
    "tags": ["important"],
    "year_min": 2010
  }
}
```

Expected response shape:

```json
{
  "nodes": [
    {
      "id": "work_1",
      "type": "local_work",
      "title": "...",
      "year": 2020,
      "tags": ["important"],
      "warnings": []
    }
  ],
  "edges": [
    {
      "id": "edge_1",
      "source": "work_1",
      "target": "work_2",
      "citation_mentions": 3,
      "contexts_preview": ["..."]
    }
  ],
  "summary": {
    "node_count": 120,
    "edge_count": 340,
    "missing_reference_count": 44
  }
}
```

---

## 27. Appendix B: example summary object

```json
{
  "entity_type": "work",
  "entity_id": "work_123",
  "summary_type": "local_ai",
  "content_markdown": "## Research question\n...",
  "model_provider": "ollama",
  "model_name": "qwen3:4b",
  "prompt_template_id": "paper-summary-v1",
  "source_entity_ids": {
    "sections": ["sec_1", "sec_2"],
    "references": ["ref_1", "ref_2"]
  },
  "source_hash": "sha256:...",
  "is_current": true
}
```

---

## 28. Appendix C: minimum viable OpenAPI response patterns

All list endpoints shall support pagination:

```json
{
  "items": [],
  "total": 0,
  "limit": 50,
  "offset": 0
}
```

All mutation endpoints shall return either the changed object or a job object:

```json
{
  "job_id": "...",
  "status": "queued",
  "entity_type": "import_batch",
  "entity_id": "..."
}
```

All user-facing errors shall use:

```json
{
  "error": {
    "code": "path_not_allowed",
    "message": "This file is outside the configured source roots.",
    "details": {}
  }
}
```

---

## 29. Appendix D: security checklist

Before release:

```text
[ ] No anonymous library endpoints.
[ ] No guest account or guest role shipped.
[ ] Owner bootstrap required.
[ ] CLI password recovery tested.
[ ] Password reset revokes sessions.
[ ] Login attempts rate-limited.
[ ] Audit events for login/read/export/import/admin.
[ ] GROBID not exposed to LAN.
[ ] Ollama not exposed to LAN.
[ ] PostgreSQL/Redis not exposed to LAN.
[ ] URL importer blocks private IPs by default.
[ ] Server file reads restricted to configured roots and managed store.
[ ] Agent file reads restricted to configured roots.
[ ] Symlink escape rejected.
[ ] Agent revocation tested.
[ ] Teleport checksum verification tested.
[ ] Backups exclude plaintext credentials/tokens.
```

---

## 30. Appendix E: coding-agent handoff packet

Each coding agent should receive:

1. this specification;
2. current OpenAPI schema;
3. current Alembic migration head;
4. current ADR list;
5. assigned section from Section 21;
6. test command;
7. Docker Compose startup command;
8. branch naming rule;
9. PR checklist;
10. list of files/modules they own.

Suggested first instruction to any coding agent:

```text
Implement only your assigned section of PaperRacks. Preserve the no-guest, no-arbitrary-path, audit-logged design. Do not change shared API/database contracts without updating OpenAPI, Alembic migrations, tests, and an ADR.
```

---

## 31. References

1. GROBID REST API documentation: https://grobid.readthedocs.io/en/latest/Grobid-service/
2. GROBID consolidation documentation: https://grobid.readthedocs.io/en/latest/Consolidation/
3. GROBID coordinates documentation: https://grobid.readthedocs.io/en/latest/Coordinates-in-PDF/
4. PDF.js: https://mozilla.github.io/pdf.js/
5. FastAPI security tutorial: https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/
6. PostgreSQL full-text search: https://www.postgresql.org/docs/current/textsearch.html
7. pgvector: https://github.com/pgvector/pgvector
8. BERTopic documentation: https://bertopic.readthedocs.io/en/latest/index.html
9. Ollama embeddings API: https://docs.ollama.com/api/embed
10. Ollama Qwen3 model library: https://ollama.com/library/qwen3
11. Citation Style Language: https://citationstyles.org/
12. citeproc-py: https://pypi.org/project/citeproc-py/
13. Crossref REST API documentation: https://www.crossref.org/documentation/retrieve-metadata/rest-api/
14. arXiv API user manual: https://info.arxiv.org/help/api/user-manual.html
15. OpenAlex API documentation: https://developers.openalex.org/api-reference/introduction
16. Semantic Scholar API: https://www.semanticscholar.org/product/api
17. Cytoscape.js: https://js.cytoscape.org/
18. vis-network documentation: https://visjs.github.io/vis-network/docs/network/
19. EmbeddingGemma overview: https://ai.google.dev/gemma/docs/embeddinggemma
20. llama.cpp quantization docs: https://github.com/ggml-org/llama.cpp/blob/master/tools/quantize/README.md

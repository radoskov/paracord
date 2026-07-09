# Roadmap

This is a condensed mirror of the canonical milestone plan in `SPECIFICATION.md` §20.
The ordering is value-first: it front-loads the complete single-machine loop
(import → organize → extract → read → export, M1–M4), then adds the remote-machine
agent (M5) and the heavier analytical layers (M6–M7) before final hardening (M8).
`WORK_SPLIT.md` maps the work packages (A–J) onto these milestones.

> **Current position (2026-06-29):** M0–M1 done and validated end-to-end. **M2–M7 all have an
> implemented, tested backend vertical** — GROBID extraction via the RQ worker;
> arXiv/Crossref/OpenAlex/Semantic-Scholar enrichment with a provenance/conflict surface; raw TEI
> + citation mentions; the full M4 duplicate/version/multiwork review (scan, merge, link-version,
> mark-dup, split, keep, ignore) with a frontend panel; M6 scoped citation graph; and M7
> semantic search / extractive + scope summaries / topic modeling. The frontend now has a
> hash-routed navigation shell, an Admin UI (users / agents / audit), and PDF-upload + arXiv/DOI
> identifier import. **M5 (agent) is DONE** — manifest + hash-verified agent-push teleport, plus the
> agent redesign v2 (SPEC §32): persistent tool-managed agent, privileges, import actions, durable
> state index, CLI, and a local web GUI.
>
> The reader/graph UI (Stage 3), metadata-review UI (Stage 4), agent teleport (Stage 5 + SPEC §32),
> the AI provider layer (Stage 6 — embeddings off the read path, summary/topic/embedding provider
> seams with the lexical baselines kept as defaults), and most Stage-7 hardening (auth throttling +
> change-password, SSRF guard, view audit events, dedup blocking, backup/restore, prod-smoke) are
> all built. The lexical/TF-IDF/extractive engines remain the defaults; heavier providers
> (sentence-transformers, Ollama, BERTopic, rapidfuzz) are opt-in. The remaining tail is the
> non-blocking polish in `docs/WORKPLAN.md` Stage 7 (pgvector/H7, CSL citeproc styles, the C3/C4
> FK+JSONB migration, a Postgres integration suite).
>
> **The ordered plan to finish the app is `docs/WORKPLAN.md` (2026-06-29)** — it re-validates the
> audit against current code and sequences the remaining work in 7 stages, front-loading
> whole-area unblockers (managed-path extraction fix, GROBID coordinates, PDF.js + Cytoscape,
> metadata UI, agent teleport, AI provider seams) and deferring minor polish to the last stage.
> See also `PROGRESS.md` → "Start here (next agent)".
> The two formerly-deferred M0 hardening items (login rate limiting, in-app password change) are
> now **DONE** in Stage 7.

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
  persistence. (done) Work-scoped citation-context API. (done) Initial frontend surfacing.
  (done)
- Metadata enrichment connectors: arXiv + Crossref (by identifier) with a provenance/
  conflict review surface and auto-correction of GROBID metadata. (done, validated live)
  OpenAlex/Semantic Scholar and fuzzy title lookup still to do.
- Deterministic keyword extraction (YAKE/KeyBERT).
- needs_ocr detection with optional OCRmyPDF fallback.
- Optional reference-parser fallback (anystyle/refextract).
- Crossref/arXiv/OpenAlex connectors; metadata assertions and conflict review.

## M3: Reader, annotations, and exports

- PDF.js reader; separate annotation storage; annotation/note search. (embedded PDF surface and
  annotation create/list UI started; search pending)
- References / citation-context tabs. (initial References tab started)
- BibTeX, BibLaTeX, RIS, CSL JSON, Markdown, HTML, plain-text exports.
  (BibTeX/text service started for work/shelf/rack scopes)
- Import from BibTeX/RIS/CSL JSON; Zotero-compatible interchange documented.
- Citation key management; live always-current shelf/rack bibliography.

## M4: Duplicate, version, and multi-work review

- Exact, DOI/arXiv, fuzzy, and text-fingerprint duplicate detection. (scanner/API/initial
  status UI started)
- Version linking; multi-paper file links and segments. (backend version-link action and
  multiwork split action started)
- Review UI (merge / link as version / split / keep separate / ignore). (initial real-action UI
  exists, including split controls)

## M5: Local agent and teleport (remote machines) — DONE (2026-06-29)

- Agent registers (owner-gated enrollment) and scans configured roots into an opaque-id index. (done)
- Server receives manifests; remote import strictly by `local_file_id`. (done)
- Teleport a PDF to the managed store via secure agent-push with SHA-256 verification. (done)
- Path isolation: the agent never accepts a server-supplied path; the raw-path helper was removed,
  with security tests. (done)
- **Agent redesign v2 (SPEC §32) — DONE (2026-06-29):** the agent became a single persistent,
  tool-managed deployable: per-agent privileges (`can_index`/`can_extract`/`can_teleport`/…,
  owner-set, audited), three import actions (`index_only` / `index_and_extract` — PDF discarded
  after extraction, reference + preview kept / `teleport`), teleport request + reject-forever/
  unblock, a **durable SQLite state index** (the closed M8 deferral), a full CLI, and a token-gated
  loopback **web GUI** (`paracord-agent web up`). Deferred (M8/polish): admin teleport browser.

## M6: Citation graph and summaries

- Local reference resolution; scoped citation graph (library/rack/shelf/search).
- Citation context display; related-papers suggestions.
- Shelf/rack citation summaries; missing-references view.

## M7: Local AI and topics — provider seams DONE (2026-06-30)

- Embeddings + semantic search — **DONE** (Stage 6 H2): built off the read path (import / RQ /
  reindex), read-only search, provider interface (`hash_bow` default; `sentence_transformers` /
  `ollama` opt-in). pgvector storage is deferred until a real embedding model is the default (H7).
- Local summaries with provenance — **DONE**: extractive/abstract defaults + opt-in `local_llm`
  (Ollama) with graceful fallback and `source_sections`.
- Topic suggestions — **DONE**: TF-IDF baseline + an `embedding`/`bertopic` backend
  (representative works, coherence, outliers, hierarchy). Real BERTopic can drop in behind it.
- Optional ML extraction path (Nougat/Marker) for hard documents — still future.

## M8: Polish, backup, and deployment hardening — substantially DONE (2026-06-30)

- Backup/restore — **DONE** (`make backup`/`restore` + `docs/runbooks/operations.md`).
- Security checklist — auth throttling, in-app change-password + session revocation, SSRF-hardened
  egress, `SECURITY.md` reconciled (Stage 7). LAN deployment docs exist; prod smoke = `make
  prod-smoke`.
- Performance — fuzzy-dedup blocking + background full-scan (H3).
- Remaining: full E2E suite, CSL citeproc styles, pgvector (H7), and the C3/C4 FK/JSONB migration.

## Known future gaps

- **DOI collision across visibility boundaries.** A paper's DOI is globally unique
  (`uq_works_doi`). Extraction / manual edit / metadata-apply now fail closed on a collision with a
  clear message naming the offending DOI and the paper that holds it (batch 8, issue 3). But once
  shelves/racks carry visibility permissions, a user could be blocked from adding a DOI-colliding
  paper to a shelf they *can* see because the conflicting paper lives on a shelf they *can't* — and
  the fail-closed message would then name a paper they aren't allowed to know exists. Resolving this
  needs a policy decision (permission-aware conflict message, or a cross-visibility "someone already
  has this DOI" mediation flow) — out of scope for batch 8, tracked here.

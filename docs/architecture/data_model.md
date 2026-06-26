# Data Model Notes

The canonical model deliberately separates concepts — **do not collapse it into "one PDF = one
paper."** Implemented tables (`backend/app/models/`, schema from `backend/alembic/versions/`):

```text
Work             -> conceptual paper            WorkVersion  -> specific version
File             -> physical file identity      FileSegment  -> page range within a file
FileWorkLink     -> file/work many-to-many      Location     -> where a file can be obtained
Source           -> configured ingestion source ImportBatch  -> one import run + stats
Reference        -> bibliography item of a work CitationMention -> in-text citation + context
RawTeiDocument   -> raw GROBID TEI per extraction
Shelf / Rack / RackShelf / ShelfWork           -> collections (works->shelves->racks)
Tag / TagLink    -> labels on works/shelves/racks
MetadataAssertion-> provenance-aware metadata value (source + confidence + selected_as_canonical)
Summary          -> per-entity summary (tier0/1; provenance via model_name+prompt_version)
TopicAssignment  -> topic-model output (one collapsed table; see divergence below)
Embedding        -> JSON vector per entity for semantic search (not pgvector; see divergence)
DuplicateCandidate -> dup/version/multiwork review queue
Agent / AgentEnrollmentToken -> local-agent enrollment (hashed tokens)
User / UserSession -> accounts + revocable bearer sessions
AuditEvent       -> auth/activity/change log
```

## Two definitions of the schema — keep them in sync

The ORM models (`Base.metadata`) and the Alembic migrations are **independent** definitions. Tests
build the schema from the models on SQLite; production runs the migrations on Postgres. They have
drifted — see `docs/AUDIT.md` C2/C3/C4. **When you change a model, write the matching migration and
verify `alembic upgrade head` + autogenerate-clean on Postgres.** Known drift today:

- Foreign keys are declared in migrations but mostly **not** in the models (only `UserSession`).
- `audit_events.details` (and other JSON columns) are `JSONB` in migrations, generic `JSON` in
  models.
- `summaries`/`topic_assignments` had no migration until `0010` (the bug that prompted this note).

## Divergences from SPECIFICATION.md §9.3 (decide: implement vs. amend spec)

- `works`: no persisted `arxiv_base_id`, no UNIQUE on `doi`/arXiv base (version stripped at query
  time in `metadata_enrichment._arxiv_base`).
- `Reference`: no `resolution_status` enum (graph resolution is computed per-request, not stored).
- `CitationMention`: 4 float coord columns instead of `pdf_coordinates jsonb` (blocks PDF.js anchors;
  also coordinates are never extracted — GROBID `teiCoordinates` isn't requested).
- Topics: single `topic_assignments` table instead of `topic_models`/`topics`/`work_topics`.
- `metadata_assertions`: scalar `value` string, no `conflict_status` (spec wants `field_value jsonb`).
- `users`/`agents`: missing several spec fields (see AUDIT.md §3).
- `Embedding.vector`: JSON array + Python cosine, not a pgvector column (spec §9.3 wants `vector`).

See `docs/AUDIT.md` for severities, rationale, and the prioritized fix order.

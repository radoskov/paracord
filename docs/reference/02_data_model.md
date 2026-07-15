# 02 — Data Model

[← Architecture](01_architecture.md) · [Backend services →](03_backend_services.md)

Source of truth: `backend/app/models/*.py` (27 model files) and the 61 Alembic migrations in
`backend/alembic/versions/`. This documents the schema the ORM defines **today**.

---

## 2.1 Global conventions

- **Base / engine.** `Base` is a plain `DeclarativeBase` (`app/db/base.py`). `pgvector.sqlalchemy`
  is imported purely so schema reflection recognizes `vector` columns provisioned by raw DDL and
  kept off the ORM. The engine is a single sync `create_engine(..., pool_pre_ping=True)` with a
  `sessionmaker(autocommit=False, autoflush=False)`; `get_db()` is the FastAPI dependency.
- **Primary keys.** Almost every table uses `id: Uuid` with a Python-side `default=uuid.uuid4`.
  Association tables use composite PKs; `EmbeddingModelRegistry` is keyed on a text `slug`.
- **Timestamps.** `created_at`/`updated_at` are `DateTime(timezone=True)` with **Python-side**
  defaults (`datetime.now(UTC)`), not server defaults. Migration `6a310e33c3d6` converted all naive
  columns to `timestamptz`.
- **JSON portability.** The idiom `JSON().with_variant(JSONB(), "postgresql")` gives JSONB on
  Postgres and plain JSON on SQLite. `Work` also uses a `none_as_null` variant so Python `None`
  stores as SQL NULL.
- **Relationships are mostly FK-only.** Only `Reference ↔ ReferenceCitation` declare ORM
  `relationship()`. Every other cross-table link is a bare FK (or a *soft*, no-FK link) with joins
  written explicitly in services. This is a deliberate, pervasive choice — see the revision flag on
  [orphan integrity](11_future_and_revision_notes.md#data-model).

## 2.2 Entity–relationship overview

Solid edges are DB foreign keys; **dashed edges are *soft* links with no DB FK** (integrity enforced
only in the service layer). Association tables are shown as their own entities.

```mermaid
erDiagram
    users ||--o{ user_sessions : has
    users ||--o{ groups : "personal_user_id"
    users ||--o{ group_memberships : joins
    groups ||--o{ group_memberships : contains
    groups ||--o{ group_grants : grants
    users ||--o{ saved_filters : owns

    works ||--o{ work_versions : "has versions"
    works ||--o{ file_work_links : links
    files ||--o{ file_work_links : links
    files ||--o{ locations : "found at"
    files ||--o{ file_segments : "segmented into"
    works |o--o| files : "main_file_id"
    works |o--o| works : "merged_into_id (self)"
    import_batches ||--o{ works : produced
    sources ||--o{ import_batches : ran

    references ||--o{ reference_citations : "cited by"
    works ||--o{ reference_citations : cites
    references ||--o{ citation_mentions : "mentioned as"
    works ||--o{ citation_mentions : "cites (in-text)"
    external_papers ||--o{ external_citation_links : "cites (incoming)"
    works ||--o{ external_citation_links : "cited by external"
    works ||--o{ work_chunks : "chunked into"

    shelves ||--o{ shelf_works : contains
    works ||--o{ shelf_works : "member of"
    racks ||--o{ rack_shelves : contains
    shelves ||--o{ rack_shelves : "member of"
    tags ||--o{ tag_links : "attached via"

    agents ||--o{ agent_files : indexed
    agents ||--o{ agent_enrollment_tokens : issued
    import_staging_batches ||--o{ import_staging_items : contains

    works }o..o{ annotations : "soft: work_id"
    works }o..o{ metadata_assertions : "soft: entity_id"
    works }o..o{ embeddings : "soft: entity_id"
    works }o..o{ summaries : "soft: entity_id"
    works }o..o{ topic_assignments : "soft: work_id"
    works }o..o{ tag_links : "soft: entity_id"
```

**Singletons** (fixed-id single-row overlays over static `Settings`, no relationships):
`ai_config`, `app_config`, `web_find_settings`, `access_settings`.
`app_config` grew in the 2026-07-15 UX batches (migrations 0070–0073): matching acceptance
(`fuzzy_accept_threshold`, `use_high_confidence_auto_accept`), `citation_summary_item_cap`,
and the Elsevier API pair (`elsevier_api_key` — write-only through the admin API — and the
`elsevier_api_enabled` master switch); `users.elsevier_api_allowed` (0073, NULL→False) is the
per-user gate for that key.
**Standalone**: `web_find_allowed_hosts`, `import_roots`, `audit_events` (soft actor links),
`duplicate_candidates` (soft polymorphic pair), `embedding_model_registry`, `missing_work_decisions`,
`custom_themes`, `default_grants`.

## 2.3 The polymorphic (discriminator) pattern

A recurring shape — `entity_type: String` + `entity_id: Uuid` **with no foreign key** — attaches
rows to a work/shelf/rack/citation-context/search-result generically:

| Table | Discriminator columns |
|-------|----------------------|
| `tag_links` | `entity_type`, `entity_id` |
| `metadata_assertions` | `entity_type`, `entity_id`, `field_name` |
| `embeddings` | `entity_type`, `entity_id`, `model_name` |
| `summaries` | `entity_type`, `entity_id`, `summary_type` |
| `topic_assignments` | `scope_type`, `scope_id`, `work_id` |
| `duplicate_candidates` | `entity_a_type/id`, `entity_b_type/id` (a **pair**) |
| `audit_events` | `entity_type`, `entity_id` (as `String255`) |
| `group_grants`, `default_grants` | `target_type` (`rack`/`shelf`), `target_id` |
| `annotations` | implicit `work_id` (no FK) |

**Consequence:** deleting a parent (e.g. a `Work`) leaves these rows dangling unless a service
cleans them up. This is the single biggest integrity risk in the schema — see
[§11 data-model flags](11_future_and_revision_notes.md#data-model).

## 2.4 Domain groups

### Core library

```mermaid
classDiagram
    class Work {
        +UUID id
        +Text canonical_title
        +Text normalized_title
        +Text abstract
        +String doi
        +String arxiv_id / arxiv_base_id
        +String venue
        +int year
        +String work_type = "unknown"
        +String reading_status = "unread"
        +Text processing_error  «F2 per-paper job failure»
        +int queue_position
        +bool user_confirmed
        +JSONB confirmed_fields  «per-field locks»
        +JSONB keywords / topics
        +JSONB merge_record  «reversible-merge shadow»
        +UUID main_file_id → File
        +UUID merged_into_id → Work (self)
        +UUID import_batch_id → ImportBatch
        +UUID created_by_user_id  «soft; NULL = system»
        +UUID version_group_id  «soft»
        +int citation_count  «cached snapshot»
    }
    class File {
        +UUID id
        +String sha256  «unique, content-addressed»
        +int size_bytes
        +String mime_type
        +int page_count
        +String text_layer_quality = "unknown"
        +String status = "available"
        +Text preview_text
        +String text_fingerprint
        +datetime extraction_requested_at  «D7 owed marker»
        +int extraction_attempts  «F2 retry cap»
    }
    class FileWorkLink {
        +UUID file_id → File
        +UUID work_id → Work
        +UUID version_id → WorkVersion
        +UUID segment_id → FileSegment
        +String relationship_type = "primary"
        +int confidence = 100
        +String warning_state = "none"
    }
    class WorkVersion {
        +UUID work_id → Work
        +String version_label / version_type
        +String arxiv_version
        +String doi
    }
    class WorkLink {
        +UUID work_a_id → Work
        +UUID work_b_id → Work
        +String link_type = "related"
    }
    class Location {
        +UUID file_id → File
        +UUID source_id → Source
        +UUID agent_id → Agent
        +String location_type
        +String display_path / internal_uri
        +bool is_primary / is_available
    }
    class FileSegment {
        +UUID file_id → File
        +int page_start / page_end
        +String segment_type = "full_file"
    }
    class Source {
        +UUID id
        +String type / name
        +UUID owner_user_id / agent_id  «soft»
        +JSONB config
        +bool is_active
    }
    class ImportBatch {
        +UUID id
        +UUID source_id → Source
        +String input_type / status = "queued"
        +JSONB settings / stats
    }
    Work "1" --> "0..1" File : main_file
    Work "1" --> "*" FileWorkLink
    File "1" --> "*" FileWorkLink
    Work "1" --> "*" WorkVersion
    File "1" --> "*" Location
    File "1" --> "*" FileSegment
    ImportBatch "1" --> "*" Work
    Work ..> WorkLink : relates
```

The core deliberately separates concepts — **do not collapse it into "one PDF = one paper."** A
`File` is a physical content-addressed artifact (dedup by `sha256`); a `Work` is the conceptual
paper; `FileWorkLink` is the many-to-many with rich attributes (which version/segment, confidence,
warning state); `WorkVersion` and `FileSegment` model multi-version and multi-work-per-file cases.

### Organization

| Table | Class | Notes |
|-------|-------|-------|
| `shelves` | `Shelf` | `name`, `status`, **`access_level`** (`open`/`visible`/`private`), soft `created_by_user_id` |
| `racks` | `Rack` | same shape as Shelf incl. `access_level` |
| `tags` | `Tag` | `name`/`normalized_name` (unique), `color`, **`parent_tag_id`** (soft, no self-FK) |
| `shelf_works` | `ShelfWork` | PK `(shelf_id, work_id)`; `work_id` separately indexed for access filtering |
| `rack_shelves` | `RackShelf` | PK `(rack_id, shelf_id)`; `shelf_id` separately indexed |
| `tag_links` | `TagLink` | polymorphic PK `(tag_id, entity_type, entity_id)` |

Membership is many-to-many both ways: a work can be on many shelves; a shelf can be in many racks.
The `access_level` on shelves/racks is the anchor of the whole access-control model (§2.4 Access).

### Extraction & citations

```mermaid
classDiagram
    class Reference {
        +UUID id  «canonical, shared by all citers»
        +String dedup_key  «DOI→arXiv→title:year, NOT unique»
        +Text raw_citation / title
        +String doi / arxiv_id
        +int year
        +JSONB authors
        +UUID resolved_work_id → Work  «confirmed match»
        +UUID suggested_work_id → Work  «fuzzy guess»
        +float match_score
        +String resolution_status = "unresolved"
    }
    class ReferenceCitation {
        +UUID reference_id → Reference
        +UUID citing_work_id → Work
        +UUID source_tei_id → RawTeiDocument
    }
    class CitationMention {
        +UUID citing_work_id → Work
        +UUID reference_id → Reference
        +UUID resolved_cited_work_id → Work
        +String marker_text / section_label
        +Text context_before / context_sentence / context_after
        +int page
        +JSONB pdf_coordinates  «list of {page,x,y,w,h}»
    }
    class RawTeiDocument {
        +UUID file_id / work_id  «soft»
        +String source = "grobid"
        +Text tei_xml  «kept verbatim for reprocessing»
    }
    class ExternalPaper {
        +String dedup_key  «unique»
        +String source / external_id
        +String doi / arxiv_id / title / venue
        +Text authors  «"; "-joined»
        +int year
        +UUID resolved_work_id → Work  «in-library citer (local matcher), SET NULL»
    }
    class ExternalCitationLink {
        +UUID external_paper_id → ExternalPaper
        +UUID work_id → Work  «this external paper cites this local work»
    }
    class WorkChunk {
        +UUID work_id → Work
        +String section
        +int position  «unique per work»
        +Text text
        +int token_count
        +vector vec_minilm_vec_nomic  «Postgres-only, off-ORM»
    }
    Reference "1" --> "*" ReferenceCitation
    Work "1" --> "*" ReferenceCitation
    Reference "1" --> "*" CitationMention
    ExternalPaper "1" --> "*" ExternalCitationLink
    Work "1" --> "*" ExternalCitationLink
    Work "1" --> "*" WorkChunk
```

Two citation directions are modeled distinctly:
- **Outgoing** (what a work cites) → canonical shared `Reference` + per-work `ReferenceCitation`
  edges + per-work `CitationMention` (in-text context with PDF coordinates).
- **Incoming** (who cites a work) → `ExternalPaper` + `ExternalCitationLink`, fetched from
  OpenAlex/Semantic-Scholar. Since migration `0064`, each external paper also runs through the same
  local matcher as references: `resolved_work_id` marks a citing paper that IS a library work
  (recomputed on every fetch/rescan, repointed on merge, SET NULL on delete).

`MissingWorkDecision` records a per-user `import`/`ignore` decision on frequently-cited-but-missing
works, keyed by a stable normalized `missing_key`.

### AI

| Table | Class | Purpose |
|-------|-------|---------|
| `embeddings` | `Embedding` | Polymorphic dense vector. `vector` (JSON, source of truth, Python kNN) + optional Postgres-only `vector_pg`. Unique `(entity_type, entity_id, model_name)`. |
| `summaries` | `Summary` | Polymorphic summary with rich provenance (`model_name`, `provider_requested`/`provider_used`, `fallback`, `source_sections`, `content_hash`). |
| `topic_assignments` | `TopicAssignment` | Work→topic under a `(topic_model_id, scope_type, scope_id)` scope. |
| `ai_config` | `AIConfig` | **Singleton** provider config (embedding/summary/topic backends, OCR, Ollama URL). |
| `embedding_model_registry` | — | Dynamic model→pgvector-column map (PK = `slug`); drives runtime DDL. |

### Access control

```mermaid
classDiagram
    class User {
        +UUID id
        +String username  «unique»
        +String password_hash
        +String role = "reader"  «reader<contributor<editor<librarian<admin<owner»
        +bool is_bootstrap  «the single immutable owner»
        +String display_name / email
        +int papers_per_page
        +String theme
        +datetime last_login_at / disabled_at
    }
    class UserSession {
        +UUID user_id → User
        +String token_hash  «sha256, unique»
        +datetime expires_at / revoked_at
    }
    class Group {
        +String name  «unique»
        +bool is_personal
        +UUID personal_user_id → User
    }
    class GroupMembership {
        +UUID group_id → Group
        +UUID user_id → User
    }
    class GroupGrant {
        +UUID group_id → Group
        +String target_type  «rack|shelf»
        +UUID target_id  «soft, polymorphic»
    }
    class AccessSettings {
        +String default_access_level  «singleton, id=int(2)»
        +UUID default_shelf_id  «soft — the Inbox»
    }
    class AuditEvent {
        +UUID actor_user_id / actor_agent_id  «soft»
        +String event_type
        +String entity_type / entity_id(255)
        +JSONB details
    }
    User "1" --> "*" UserSession
    User "1" --> "0..1" Group : personal
    Group "1" --> "*" GroupMembership
    User "1" --> "*" GroupMembership
    Group "1" --> "*" GroupGrant
```

The access model (migrations `0028`/`0029`/`0039`) is Linux-like: every user gets an auto-managed
**personal group** seeded with admin-configured `DefaultGrant`s; `GroupGrant` attaches a group to a
rack/shelf; `AccessSettings` holds the global default access level and the ephemeral default/Inbox
shelf. See [08 — Security](08_security.md#82-authorization-authz) for how these compose into
SEE/MODIFY decisions.

### Agents

| Table | Class | Notes |
|-------|-------|-------|
| `agents` | `Agent` | `token_hash`, `status`, per-agent privilege booleans (`can_index`/`can_extract`/`can_teleport`=false/`can_be_requested`/`processing_visibility`/`server_status_visibility`), `capabilities` JSONB |
| `agent_enrollment_tokens` | `AgentEnrollmentToken` | single-use owner-issued token (hashed) |
| `agent_files` | `AgentFile` | `local_file_id`, `sha256`, display-only paths, `import_action` (`index_only`/`index_and_extract`/`teleport`), `teleport_policy`, `processing_state`, soft `file_id`/`work_id` |

### Config / staging / misc

`AppConfig` (runtime knobs), `WebFindSettings` (download policy), `WebFindAllowedHost` (egress
allowlist), `ImportRoot` (GUI-added scan roots), `CustomTheme` (runtime YAML themes),
`ImportStagingBatch`/`ImportStagingItem` (multi-PDF extract-before-store staging),
`MetadataAssertion` (provenance-aware candidate values), `Annotation` (reader annotations),
`DuplicateCandidate` (review queue), `SavedFilter` (per-user query/scope).

## 2.5 Notable schema-design decisions

1. **Canonical shared references + per-work link table** (migration `0059`): one `references` row
   per cited thing; `reference_citations` is the many-to-many onto citing works. Mirrors the
   incoming `external_papers`/`external_citation_links` normalization.
2. **User-corrected metadata protection.** `Work.confirmed_fields` (a JSONB list of *locked* field
   names) lets enrichment overwrite everything *except* fields the user confirmed — superseding the
   coarse `user_confirmed` bool. `MetadataAssertion.selected_as_canonical` + the multi-source
   assertion store back canonical selection.
3. **Two-tier reference matching.** `resolved_work_id` (confirmed, drives all graphs/metrics) vs
   `suggested_work_id` + `match_score` (unconfirmed fuzzy guess, never auto-promoted), gated by the
   runtime toggle `AppConfig.use_fuzzy_match_as_confirmed`.
4. **Raw TEI retention.** `RawTeiDocument.tei_xml` (and `ImportStagingItem.tei_xml`) keep full
   GROBID output so extraction can be reprocessed / a staged item committed without re-running
   GROBID.
5. **Dialect-agnostic ORM + Postgres-only accelerators.** Vectors live as JSON (source of truth,
   Python kNN); pgvector columns are additive raw-DDL accelerators kept off the ORM.
   `EmbeddingModelRegistry` makes the model→column mapping dynamic.
6. **Settings singletons.** `AIConfig`/`AppConfig`/`WebFindSettings`/`AccessSettings` are fixed-id
   single-row overlays; absent/NULL reproduces `Settings` defaults.
7. **Reversible merge.** `Work.merged_into_id` + `Work.merge_record` (JSONB, `none_as_null`)
   implement single-level reversible duplicate merges (a shadow is reversible iff both are set).
8. **Role stays VARCHAR** (no Postgres enum), so adding roles (`contributor`/`librarian` from the
   `0024` redesign) needs no schema change.

## 2.6 Migration history at a glance

64 revisions, from `0001` (users + audit) through `0063` (F2 per-paper processing error) plus one
hash-named `6a310e33c3d6` (timestamptz conversion). Milestones worth knowing:

- `0003` core library · `0004`/`0005` extraction + raw TEI + mentions · `0006` dup candidates
- `0013` citation PDF coordinates → JSONB · `0018` AI config · `0019` pgvector
- `0024` role redesign · `0028`/`0029`/`0039` access control · `0034`/`0035`/`0036` chunks + chunk
  vectors + model registry
- `0056` import staging · `0057`→`0058` external citations (denormalized → normalized) · `0059`
  canonical references · `0060` fuzzy-match toggle
- `0061` reference-rescan-on-startup toggle (F3a) · `0062` `files.extraction_attempts` (F2 retry cap)
  · `0063` `works.processing_error` (F2 loud per-paper failures)

> Two entries show schema churn worth noting when reading migration history: `0057` created a
> denormalized `external_citations` table that `0058` immediately restructured into
> `external_papers` + `external_citation_links`. See [§11](11_future_and_revision_notes.md#data-model).

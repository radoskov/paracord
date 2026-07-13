"""Squashed baseline: the complete PaRacORD schema as of revision 0067 (S-batch item 4)

This single migration replaces the historical chain 0001…0067 (68 files). It keeps the SAME
revision id as the old head, so:

* an EXISTING database stamped ``0067_citing_cap_ai_threshold`` (the oldest supported deployment
  floor, owner decision 2026-07-13) sees itself at head and nothing runs;
* a FRESH database gets the entire current schema in one step.

Schema source of truth: ``Base.metadata.create_all`` (the ORM models) plus the deliberately
off-ORM DDL the old chain added — the pgvector extension, the ``embeddings.vector_pg`` /
``work_chunks.vec_*`` vector columns with their HNSW indexes, the partial unique indexes on
``works.doi`` / ``works.arxiv_base_id`` — and the embedding-model registry seed rows (from 0036).
Equality with the old chain's schema is verified by diffing ``pg_dump --schema-only`` outputs
(see the 2026-07-13 handoff); constraint/index names are preserved so future migrations can
reference them on both old and fresh databases.

Older backups: none exist past-floor by owner statement; the logical backup system
(``services/backup.py``) restores by column-name intersection and does not depend on the
migration chain.

Revision ID: 0067_citing_cap_ai_threshold
Revises: None
Create Date: 2026-07-13 (squashed)
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from alembic import op

revision: str = "0067_citing_cap_ai_threshold"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Embedding-model registry seed (carried over from 0036): the two built-in pgvector-backed models.
_REGISTRY_SEED = (
    (
        "minilm",
        "st:sentence-transformers/all-MiniLM-L6-v2",
        "sentence_transformers",
        "sentence-transformers/all-MiniLM-L6-v2",
        384,
        "vec_minilm",
    ),
    (
        "nomic",
        "ollama:nomic-embed-text:latest",
        "ollama",
        "nomic-embed-text:latest",
        768,
        "vec_nomic",
    ),
)


def upgrade() -> None:
    """Create the full schema (fresh installs only — existing DBs are already at this revision)."""
    import app.models  # noqa: F401, PLC0415 - registers every model on Base.metadata
    from app.db.base import Base  # noqa: PLC0415

    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    Base.metadata.create_all(bind=bind)

    if is_postgres:
        # Off-ORM pgvector columns + ANN indexes (kept out of the models so SQLite tests work).
        op.execute("ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS vector_pg vector")
        op.execute("ALTER TABLE work_chunks ADD COLUMN IF NOT EXISTS vec_minilm vector(384)")
        op.execute("ALTER TABLE work_chunks ADD COLUMN IF NOT EXISTS vec_nomic vector(768)")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_work_chunks_vec_minilm ON work_chunks "
            "USING hnsw (vec_minilm vector_cosine_ops)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_work_chunks_vec_nomic ON work_chunks "
            "USING hnsw (vec_nomic vector_cosine_ops)"
        )
        # Partial unique identifier indexes (NULLs exempt) — kept off-ORM for SQLite compatibility.
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_works_doi ON works (doi) WHERE doi IS NOT NULL"
        )
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_works_arxiv_base_id ON works (arxiv_base_id) "
            "WHERE arxiv_base_id IS NOT NULL"
        )

        # --- Parity fix-ups vs the historical chain (verified by pg_dump diff) ---------------
        # Server-side column defaults the old chain declared but the ORM keeps Python-side.
        # They matter for raw INSERTs (data migrations, the logical backup restore).
        for stmt in (
            "ALTER TABLE agent_files ALTER COLUMN import_action SET DEFAULT 'index_only'",
            "ALTER TABLE agent_files ALTER COLUMN processing_state SET DEFAULT 'indexed'",
            "ALTER TABLE agent_files ALTER COLUMN size_bytes SET DEFAULT 0",
            "ALTER TABLE agent_files ALTER COLUMN teleport_policy SET DEFAULT 'ask'",
            "ALTER TABLE agent_files ALTER COLUMN teleport_status SET DEFAULT 'none'",
            "ALTER TABLE audit_events ALTER COLUMN created_at SET DEFAULT now()",
            "ALTER TABLE embedding_model_registry ALTER COLUMN active SET DEFAULT true",
            "ALTER TABLE file_segments ALTER COLUMN confidence SET DEFAULT 100",
            "ALTER TABLE file_segments ALTER COLUMN created_at SET DEFAULT now()",
            "ALTER TABLE file_segments ALTER COLUMN created_by SET DEFAULT 'system'",
            "ALTER TABLE file_segments ALTER COLUMN segment_type SET DEFAULT 'full_file'",
            "ALTER TABLE file_work_links ALTER COLUMN confidence SET DEFAULT 100",
            "ALTER TABLE file_work_links ALTER COLUMN created_at SET DEFAULT now()",
            "ALTER TABLE file_work_links ALTER COLUMN relationship_type SET DEFAULT 'primary'",
            "ALTER TABLE file_work_links ALTER COLUMN user_confirmed SET DEFAULT false",
            "ALTER TABLE file_work_links ALTER COLUMN warning_state SET DEFAULT 'none'",
            "ALTER TABLE files ALTER COLUMN created_at SET DEFAULT now()",
            "ALTER TABLE files ALTER COLUMN status SET DEFAULT 'available'",
            "ALTER TABLE files ALTER COLUMN text_layer_quality SET DEFAULT 'unknown'",
            "ALTER TABLE import_batches ALTER COLUMN created_at SET DEFAULT now()",
            "ALTER TABLE import_batches ALTER COLUMN status SET DEFAULT 'queued'",
            "ALTER TABLE locations ALTER COLUMN created_at SET DEFAULT now()",
            "ALTER TABLE locations ALTER COLUMN is_available SET DEFAULT true",
            "ALTER TABLE locations ALTER COLUMN is_primary SET DEFAULT true",
            "ALTER TABLE rack_shelves ALTER COLUMN added_at SET DEFAULT now()",
            "ALTER TABLE racks ALTER COLUMN created_at SET DEFAULT now()",
            "ALTER TABLE racks ALTER COLUMN status SET DEFAULT 'active'",
            "ALTER TABLE racks ALTER COLUMN updated_at SET DEFAULT now()",
            "ALTER TABLE shelf_works ALTER COLUMN added_at SET DEFAULT now()",
            "ALTER TABLE shelves ALTER COLUMN created_at SET DEFAULT now()",
            "ALTER TABLE shelves ALTER COLUMN status SET DEFAULT 'active'",
            "ALTER TABLE shelves ALTER COLUMN updated_at SET DEFAULT now()",
            "ALTER TABLE sources ALTER COLUMN created_at SET DEFAULT now()",
            "ALTER TABLE sources ALTER COLUMN is_active SET DEFAULT true",
            "ALTER TABLE summaries ALTER COLUMN fallback SET DEFAULT false",
            "ALTER TABLE tag_links ALTER COLUMN created_at SET DEFAULT now()",
            "ALTER TABLE tags ALTER COLUMN created_at SET DEFAULT now()",
            "ALTER TABLE user_sessions ALTER COLUMN created_at SET DEFAULT now()",
            "ALTER TABLE users ALTER COLUMN created_at SET DEFAULT now()",
            "ALTER TABLE work_chunks ALTER COLUMN token_count SET DEFAULT 0",
            "ALTER TABLE work_versions ALTER COLUMN created_at SET DEFAULT now()",
            "ALTER TABLE work_versions ALTER COLUMN version_type SET DEFAULT 'unknown'",
            "ALTER TABLE works ALTER COLUMN created_at SET DEFAULT now()",
            "ALTER TABLE works ALTER COLUMN reading_status SET DEFAULT 'unread'",
            "ALTER TABLE works ALTER COLUMN updated_at SET DEFAULT now()",
            "ALTER TABLE works ALTER COLUMN user_confirmed SET DEFAULT false",
            "ALTER TABLE works ALTER COLUMN work_type SET DEFAULT 'unknown'",
            # Constraint names must match the historical chain so future migrations can
            # reference them on BOTH old and fresh databases.
            "ALTER TABLE agent_files RENAME CONSTRAINT agent_files_work_id_fkey TO fk_agent_files_work_id",
            "ALTER TABLE external_papers RENAME CONSTRAINT external_papers_resolved_work_id_fkey TO fk_external_papers_resolved_work_id",
            "ALTER TABLE locations RENAME CONSTRAINT locations_agent_id_fkey TO fk_locations_agent_id",
            "ALTER TABLE citation_mentions RENAME CONSTRAINT citation_mentions_citing_work_id_fkey TO fk_mentions_citing_work",
            "ALTER TABLE citation_mentions RENAME CONSTRAINT citation_mentions_reference_id_fkey TO fk_mentions_reference",
            "ALTER TABLE citation_mentions RENAME CONSTRAINT citation_mentions_resolved_cited_work_id_fkey TO fk_mentions_resolved_work",
            "ALTER TABLE citation_mentions RENAME CONSTRAINT citation_mentions_source_tei_id_fkey TO fk_mentions_source_tei",
            'ALTER TABLE "references" RENAME CONSTRAINT references_resolved_work_id_fkey TO fk_references_resolved_work',
            "ALTER TABLE works RENAME CONSTRAINT works_main_file_id_fkey TO fk_works_main_file_id",
            "ALTER TABLE works RENAME CONSTRAINT works_merged_into_id_fkey TO fk_works_merged_into_id",
            # A constraint the old chain added that the ORM never declared.
            "ALTER TABLE agent_enrollment_tokens ADD CONSTRAINT uq_agent_enrollment_token_hash UNIQUE (token_hash)",
            # The old chain declared this default on the quoted "references" table.
            "ALTER TABLE \"references\" ALTER COLUMN resolution_status SET DEFAULT 'unresolved'",
            # Strict index parity with the old chain: the models want these three indexes but the
            # chain never created them / created them differently — a fresh install must match the
            # oldest running deployment exactly so a FUTURE migration adding/altering them behaves
            # identically on both. (Follow-up: one migration adding the two missing indexes
            # everywhere, then these three statements can go.)
            "DROP INDEX IF EXISTS ix_agents_created_by_user_id",
            "DROP INDEX IF EXISTS ix_works_doi",
            "DROP INDEX IF EXISTS ix_agent_enrollment_tokens_token_hash",
            "CREATE INDEX ix_agent_enrollment_tokens_token_hash ON agent_enrollment_tokens (token_hash)",
        ):
            op.execute(stmt)

    now = datetime.now(UTC)
    registry = Base.metadata.tables["embedding_model_registry"]
    op.bulk_insert(
        registry,
        [
            {
                "slug": slug,
                "model_name": model_name,
                "provider": provider,
                "raw_model": raw_model,
                "dim": dim,
                "column_name": column_name,
                "active": True,
                "created_at": now,
            }
            for slug, model_name, provider, raw_model, dim, column_name in _REGISTRY_SEED
        ],
    )


def downgrade() -> None:
    """Irreversible: this is the baseline (there is nothing before it)."""
    raise NotImplementedError("The squashed baseline cannot be downgraded")

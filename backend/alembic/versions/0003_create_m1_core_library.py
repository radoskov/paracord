"""create m1 core library tables

Revision ID: 0003_m1_core_library
Revises: 0002_user_sessions
Create Date: 2026-06-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_m1_core_library"
down_revision: str | None = "0002_user_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("path_alias", sa.Text(), nullable=True),
        sa.Column("canonical_root_hash", sa.String(length=128), nullable=True),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.create_index("ix_sources_agent_id", "sources", ["agent_id"])
    op.create_index("ix_sources_canonical_root_hash", "sources", ["canonical_root_hash"])
    op.create_index("ix_sources_is_active", "sources", ["is_active"])
    op.create_index("ix_sources_name", "sources", ["name"])
    op.create_index("ix_sources_owner_user_id", "sources", ["owner_user_id"])
    op.create_index("ix_sources_path_alias", "sources", ["path_alias"])
    op.create_index("ix_sources_type", "sources", ["type"])

    op.create_table(
        "works",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("canonical_title", sa.Text(), nullable=True),
        sa.Column("normalized_title", sa.Text(), nullable=True),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("arxiv_id", sa.String(length=64), nullable=True),
        sa.Column("venue", sa.Text(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("work_type", sa.String(length=64), server_default="unknown", nullable=False),
        sa.Column("canonical_metadata_source", sa.String(length=128), nullable=True),
        sa.Column("reading_status", sa.String(length=64), server_default="unread", nullable=False),
        sa.Column("user_confirmed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_works_arxiv_id", "works", ["arxiv_id"])
    op.create_index("ix_works_canonical_title", "works", ["canonical_title"])
    op.create_index("ix_works_doi", "works", ["doi"])
    op.create_index("ix_works_normalized_title", "works", ["normalized_title"])
    op.create_index("ix_works_reading_status", "works", ["reading_status"])
    op.create_index("ix_works_work_type", "works", ["work_type"])
    op.create_index("ix_works_year", "works", ["year"])

    op.create_table(
        "files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column(
            "text_layer_quality",
            sa.String(length=32),
            server_default="unknown",
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), server_default="available", nullable=False),
        sa.Column("preview_text", sa.Text(), nullable=True),
        sa.Column("text_fingerprint", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_files_sha256", "files", ["sha256"], unique=True)
    op.create_index("ix_files_status", "files", ["status"])
    op.create_index("ix_files_text_fingerprint", "files", ["text_fingerprint"])

    op.create_table(
        "locations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("location_type", sa.String(length=64), nullable=False),
        sa.Column("display_path", sa.Text(), nullable=True),
        sa.Column("internal_uri", sa.Text(), nullable=True),
        sa.Column("path_alias", sa.Text(), nullable=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_available", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_locations_agent_id", "locations", ["agent_id"])
    op.create_index("ix_locations_file_id", "locations", ["file_id"])
    op.create_index("ix_locations_source_id", "locations", ["source_id"])

    op.create_table(
        "work_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_label", sa.String(length=128), nullable=True),
        sa.Column("source", sa.String(length=128), nullable=True),
        sa.Column("publication_state", sa.String(length=64), nullable=True),
        sa.Column("version_type", sa.String(length=64), server_default="unknown", nullable=False),
        sa.Column("arxiv_version", sa.String(length=32), nullable=True),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_work_versions_doi", "work_versions", ["doi"])
    op.create_index("ix_work_versions_work_id", "work_versions", ["work_id"])

    op.create_table(
        "file_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("segment_type", sa.String(length=64), server_default="full_file", nullable=False),
        sa.Column("created_by", sa.String(length=32), server_default="system", nullable=False),
        sa.Column("confidence", sa.Integer(), server_default="100", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_file_segments_file_id", "file_segments", ["file_id"])

    op.create_table(
        "file_work_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("segment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "relationship_type",
            sa.String(length=64),
            server_default="primary",
            nullable=False,
        ),
        sa.Column("confidence", sa.Integer(), server_default="100", nullable=False),
        sa.Column("warning_state", sa.String(length=128), server_default="none", nullable=False),
        sa.Column("user_confirmed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["segment_id"], ["file_segments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["version_id"], ["work_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_file_work_links_file_id", "file_work_links", ["file_id"])
    op.create_index("ix_file_work_links_segment_id", "file_work_links", ["segment_id"])
    op.create_index("ix_file_work_links_version_id", "file_work_links", ["version_id"])
    op.create_index("ix_file_work_links_work_id", "file_work_links", ["work_id"])

    op.create_table(
        "shelves",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_shelves_created_by_user_id", "shelves", ["created_by_user_id"])
    op.create_index("ix_shelves_name", "shelves", ["name"])
    op.create_index("ix_shelves_status", "shelves", ["status"])

    op.create_table(
        "racks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_racks_created_by_user_id", "racks", ["created_by_user_id"])
    op.create_index("ix_racks_name", "racks", ["name"])
    op.create_index("ix_racks_status", "racks", ["status"])

    op.create_table(
        "tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("color", sa.String(length=32), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parent_tag_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_tags_name", "tags", ["name"], unique=True)
    op.create_index("ix_tags_normalized_name", "tags", ["normalized_name"], unique=True)

    op.create_table(
        "shelf_works",
        sa.Column("shelf_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("added_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("added_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["shelf_id"], ["shelves.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("shelf_id", "work_id"),
    )
    op.create_index("ix_shelf_works_added_by_user_id", "shelf_works", ["added_by_user_id"])

    op.create_table(
        "rack_shelves",
        sa.Column("rack_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shelf_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("added_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("added_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["rack_id"], ["racks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shelf_id"], ["shelves.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("rack_id", "shelf_id"),
    )
    op.create_index("ix_rack_shelves_added_by_user_id", "rack_shelves", ["added_by_user_id"])

    op.create_table(
        "tag_links",
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tag_id", "entity_type", "entity_id"),
    )
    op.create_index("ix_tag_links_created_by_user_id", "tag_links", ["created_by_user_id"])

    op.create_table(
        "import_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), server_default="queued", nullable=False),
        sa.Column("settings", sa.JSON(), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_import_batches_agent_id", "import_batches", ["agent_id"])
    op.create_index(
        "ix_import_batches_created_by_user_id",
        "import_batches",
        ["created_by_user_id"],
    )
    op.create_index("ix_import_batches_input_type", "import_batches", ["input_type"])
    op.create_index("ix_import_batches_source_id", "import_batches", ["source_id"])
    op.create_index("ix_import_batches_status", "import_batches", ["status"])


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index("ix_import_batches_status", table_name="import_batches")
    op.drop_index("ix_import_batches_source_id", table_name="import_batches")
    op.drop_index("ix_import_batches_input_type", table_name="import_batches")
    op.drop_index("ix_import_batches_created_by_user_id", table_name="import_batches")
    op.drop_index("ix_import_batches_agent_id", table_name="import_batches")
    op.drop_table("import_batches")
    op.drop_index("ix_tag_links_created_by_user_id", table_name="tag_links")
    op.drop_table("tag_links")
    op.drop_index("ix_rack_shelves_added_by_user_id", table_name="rack_shelves")
    op.drop_table("rack_shelves")
    op.drop_index("ix_shelf_works_added_by_user_id", table_name="shelf_works")
    op.drop_table("shelf_works")
    op.drop_index("ix_tags_normalized_name", table_name="tags")
    op.drop_index("ix_tags_name", table_name="tags")
    op.drop_table("tags")
    op.drop_index("ix_racks_status", table_name="racks")
    op.drop_index("ix_racks_name", table_name="racks")
    op.drop_index("ix_racks_created_by_user_id", table_name="racks")
    op.drop_table("racks")
    op.drop_index("ix_shelves_status", table_name="shelves")
    op.drop_index("ix_shelves_name", table_name="shelves")
    op.drop_index("ix_shelves_created_by_user_id", table_name="shelves")
    op.drop_table("shelves")
    op.drop_index("ix_file_work_links_work_id", table_name="file_work_links")
    op.drop_index("ix_file_work_links_version_id", table_name="file_work_links")
    op.drop_index("ix_file_work_links_segment_id", table_name="file_work_links")
    op.drop_index("ix_file_work_links_file_id", table_name="file_work_links")
    op.drop_table("file_work_links")
    op.drop_index("ix_file_segments_file_id", table_name="file_segments")
    op.drop_table("file_segments")
    op.drop_index("ix_work_versions_work_id", table_name="work_versions")
    op.drop_index("ix_work_versions_doi", table_name="work_versions")
    op.drop_table("work_versions")
    op.drop_index("ix_locations_source_id", table_name="locations")
    op.drop_index("ix_locations_file_id", table_name="locations")
    op.drop_index("ix_locations_agent_id", table_name="locations")
    op.drop_table("locations")
    op.drop_index("ix_files_text_fingerprint", table_name="files")
    op.drop_index("ix_files_status", table_name="files")
    op.drop_index("ix_files_sha256", table_name="files")
    op.drop_table("files")
    op.drop_index("ix_works_year", table_name="works")
    op.drop_index("ix_works_work_type", table_name="works")
    op.drop_index("ix_works_reading_status", table_name="works")
    op.drop_index("ix_works_normalized_title", table_name="works")
    op.drop_index("ix_works_doi", table_name="works")
    op.drop_index("ix_works_canonical_title", table_name="works")
    op.drop_index("ix_works_arxiv_id", table_name="works")
    op.drop_table("works")
    op.drop_index("ix_sources_type", table_name="sources")
    op.drop_index("ix_sources_path_alias", table_name="sources")
    op.drop_index("ix_sources_owner_user_id", table_name="sources")
    op.drop_index("ix_sources_name", table_name="sources")
    op.drop_index("ix_sources_is_active", table_name="sources")
    op.drop_index("ix_sources_canonical_root_hash", table_name="sources")
    op.drop_index("ix_sources_agent_id", table_name="sources")
    op.drop_table("sources")

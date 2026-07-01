"""Postgres-backed integration tests (WORKPLAN_NEXT Stage 9).

Exercises behavior SQLite can't: the C3/C4 FK cascades, ``timestamptz`` round-tripping, JSONB
containment queries, and the optional pgvector ranking path (H7). Skips automatically when no
Postgres is reachable, exactly like the migration-parity test; run via a stack with Postgres up.
"""

import os
import uuid
from pathlib import Path

import app.models  # noqa: F401 — register models on Base.metadata
import pytest
from alembic import command
from alembic.config import Config
from app.core.config import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

# Resolve relative to this file so the test works regardless of the process cwd
# (backend/tests/ -> parents[2] is the repo root that contains backend/alembic.ini).
ALEMBIC_INI = str(Path(__file__).resolve().parents[2] / "backend/alembic.ini")


@pytest.fixture(scope="module")
def pg_engine():
    server_url = get_settings().database_url
    if not server_url.startswith("postgresql"):
        pytest.skip("Postgres integration tests require a Postgres DATABASE_URL")
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        conn = admin.connect()
    except OperationalError:
        admin.dispose()
        pytest.skip("Postgres server not reachable")
    db_name = f"pgint_{uuid.uuid4().hex[:12]}"
    conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    conn.close()
    test_url = server_url.rsplit("/", 1)[0] + "/" + db_name
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_url
    get_settings.cache_clear()
    try:
        command.upgrade(Config(ALEMBIC_INI), "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()
    engine = create_engine(test_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()
        drop = admin.connect()
        drop.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        drop.close()
        admin.dispose()


def test_fk_cascade_deletes_references_and_mentions(pg_engine):
    from app.models.citation import CitationMention, Reference
    from app.models.work import Work

    with Session(pg_engine) as db:
        work = Work(canonical_title="Cascade", normalized_title="cascade")
        db.add(work)
        db.flush()
        ref = Reference(citing_work_id=work.id, raw_citation="r")
        db.add(ref)
        db.flush()
        db.add(CitationMention(citing_work_id=work.id, reference_id=ref.id))
        db.commit()
        work_id, ref_id = work.id, ref.id

        db.delete(db.get(Work, work_id))
        db.commit()
        # CASCADE removed the dependent rows.
        assert db.get(Reference, ref_id) is None
        mentions = db.execute(
            text("SELECT count(*) FROM citation_mentions WHERE citing_work_id = :w"),
            {"w": str(work_id)},
        ).scalar()
        assert mentions == 0


def test_timestamptz_is_timezone_aware(pg_engine):
    from app.models.work import Work

    with Session(pg_engine) as db:
        work = Work(canonical_title="TZ", normalized_title="tz")
        db.add(work)
        db.commit()
        fetched = db.get(Work, work.id)
        assert fetched.created_at.tzinfo is not None  # timestamptz, not naive


def test_jsonb_containment_query(pg_engine):
    from app.models.source import Source

    with Session(pg_engine) as db:
        db.add(Source(type="server_folder", name="s", config={"root_path": "/x", "k": "v"}))
        db.commit()
        hit = db.execute(
            text("SELECT count(*) FROM sources WHERE config @> :q"), {"q": '{"k": "v"}'}
        ).scalar()
        assert hit == 1


def test_pgvector_ranking_when_enabled(pg_engine, monkeypatch):
    from app.models.work import Work
    from app.services import semantic_search as ss

    monkeypatch.setattr(get_settings(), "pgvector_enabled", True, raising=False)
    with Session(pg_engine) as db:
        db.add_all(
            [
                Work(
                    canonical_title="Attention transformer model",
                    normalized_title="attention",
                    abstract="transformer attention mechanism",
                ),
                Work(
                    canonical_title="Sourdough bread baking",
                    normalized_title="bread",
                    abstract="fermenting baking artisan bread",
                ),
            ]
        )
        db.commit()
        # Index (dual-writes the pgvector column) then search via the pgvector path.
        ss.ensure_work_embeddings(db, provider=ss.HashBowProvider())
        db.commit()
        # The pgvector column was populated for the indexed works (module DB is shared, so >= 2).
        populated = db.execute(
            text("SELECT count(*) FROM embeddings WHERE vector_pg IS NOT NULL")
        ).scalar()
        assert populated >= 2
        hits = ss.semantic_search(db, "transformer attention", provider=ss.HashBowProvider())
        assert hits
        assert hits[0].work.canonical_title == "Attention transformer model"

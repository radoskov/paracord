"""Migration <-> model parity against a real Postgres (AUDIT.md C2).

The rest of the suite builds the schema from ``Base.metadata`` on in-memory SQLite, so it cannot
see whether the Alembic migrations actually produce that schema. This test runs
``alembic upgrade head`` against a throwaway Postgres database and asserts that every model table
and column exists in the migrated schema — the guard that would have caught the missing
``summaries`` / ``topic_assignments`` migration (C1).

It **skips automatically** when no Postgres server is reachable (the default SQLite-only test run
and current CI), so it never breaks those. Run it with Postgres up via ``make test-migrations``.
"""

import os
import uuid

import app.models  # noqa: F401  — registers every model on Base.metadata
import pytest
from alembic import command
from alembic.config import Config
from app.core.config import get_settings
from app.db.base import Base
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

ALEMBIC_INI = "backend/alembic.ini"


def _postgres_server_url() -> str | None:
    url = get_settings().database_url
    return url if url.startswith("postgresql") else None


@pytest.fixture(scope="module")
def migrated_postgres():
    """Create a throwaway DB, run all migrations into it, yield an engine, then drop it."""
    server_url = _postgres_server_url()
    if server_url is None:
        pytest.skip("migration parity test requires a Postgres DATABASE_URL")

    admin = create_engine(server_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        connection = admin.connect()
    except OperationalError:
        admin.dispose()
        pytest.skip("Postgres server not reachable")

    db_name = f"parity_{uuid.uuid4().hex[:12]}"
    connection.execute(text(f'CREATE DATABASE "{db_name}"'))
    connection.close()
    test_url = server_url.rsplit("/", 1)[0] + "/" + db_name

    # Alembic's env.py reads the URL from get_settings().database_url, so point it at the temp DB
    # via the env var (clearing the lru_cache), run the migrations, then restore immediately.
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

    test_engine = create_engine(test_url, poolclass=NullPool)
    try:
        yield test_engine
    finally:
        test_engine.dispose()
        drop = admin.connect()
        drop.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        drop.close()
        admin.dispose()


def test_every_model_table_exists_after_migration(migrated_postgres) -> None:
    db_tables = set(inspect(migrated_postgres).get_table_names())
    missing = sorted(set(Base.metadata.tables) - db_tables)
    assert not missing, f"model tables absent from the migrated Postgres schema: {missing}"


def test_every_model_column_exists_after_migration(migrated_postgres) -> None:
    inspector = inspect(migrated_postgres)
    db_tables = set(inspector.get_table_names())
    missing: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        if table_name not in db_tables:
            continue  # reported by the table-level test
        db_columns = {col["name"] for col in inspector.get_columns(table_name)}
        missing += [f"{table_name}.{c.name}" for c in table.columns if c.name not in db_columns]
    assert not missing, f"model columns absent from the migrated Postgres schema: {sorted(missing)}"


def test_model_foreign_keys_exist_after_migration(migrated_postgres) -> None:
    """Every FK declared on a model is present in the migrated schema (C3 — no weak FKs)."""
    inspector = inspect(migrated_postgres)
    db_tables = set(inspector.get_table_names())
    missing: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        if table_name not in db_tables:
            continue
        db_fk_cols = {
            tuple(fk["constrained_columns"]) for fk in inspector.get_foreign_keys(table_name)
        }
        for fk in table.foreign_key_constraints:
            cols = tuple(c.name for c in fk.columns)
            if cols not in db_fk_cols:
                missing.append(f"{table_name}({', '.join(cols)})")
    assert not missing, f"model foreign keys absent from the migrated Postgres schema: {missing}"


# Columns that live in the DB by design but not on a model (managed via raw SQL).
_DB_ONLY_COLUMNS = {("embeddings", "vector_pg")}


def test_autogenerate_has_no_table_or_column_drift(migrated_postgres) -> None:
    """No table/column add or drop is needed to match the models (C2 autogenerate-clean follow-up).

    Tolerates type/default/index/FK *modifications* (gated off / covered by the FK test); asserts
    only that no table or column is missing or extra — the structural parity that would have caught
    the original C1 (a model table with no migration).
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with migrated_postgres.connect() as conn:
        ctx = MigrationContext.configure(
            conn, opts={"compare_type": False, "compare_server_default": False}
        )
        diffs = compare_metadata(ctx, Base.metadata)

    structural: list[str] = []
    for diff in diffs:
        items = diff if isinstance(diff, list) else [diff]
        for d in items:
            op = d[0]
            if op in ("add_table", "remove_table"):
                structural.append(f"{op}:{d[1].name}")
            elif op in ("add_column", "remove_column"):
                col = d[3]
                if (d[2], col.name) in _DB_ONLY_COLUMNS:
                    continue
                structural.append(f"{op}:{d[2]}.{col.name}")
    assert not structural, f"schema drift vs models: {structural}"

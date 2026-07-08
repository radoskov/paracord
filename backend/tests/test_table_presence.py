"""Regression tests for the shared table-presence cache (``app.utils.table_presence``).

The bug this guards against: the previous per-service caches keyed on ``id(bind)``. CPython reuses
the memory address of a garbage-collected engine, so a later, narrower database whose engine reused
that address inherited a stale ``True`` answer and then queried a table that did not exist — an
order-dependent, flaky ``no such table`` failure in CI. Keying the cache on the live bind object
(held weakly) removes the aliasing.
"""

from __future__ import annotations

import gc

from app.utils.table_presence import clear_cache, table_present
from sqlalchemy import Column, Integer, MetaData, Table, create_engine
from sqlalchemy.orm import Session


def _engine_with(*table_names: str):
    """A fresh in-memory SQLite engine containing exactly the named single-column tables."""
    engine = create_engine("sqlite://")
    metadata = MetaData()
    for name in table_names:
        Table(name, metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(engine)
    return engine


def test_presence_is_independent_per_engine():
    """Two live engines must get independent answers — a cached ``True`` from one engine must not
    leak to another engine that lacks the table."""
    clear_cache()
    with_table = _engine_with("groups")
    without_table = _engine_with()  # no tables
    with Session(with_table) as db:
        assert table_present(db, "groups") is True
    with Session(without_table) as db:
        assert table_present(db, "groups") is False
    # And the first engine still answers correctly after the second was queried.
    with Session(with_table) as db:
        assert table_present(db, "groups") is True


def test_no_stale_answer_after_engine_gc():
    """After an engine with the table is dropped and collected, a fresh engine without the table
    (which may reuse the freed CPython address) must report the table absent, never the stale
    cached ``True`` the old ``id(bind)`` cache would have returned."""
    clear_cache()
    engine = _engine_with("groups")
    with Session(engine) as db:
        assert table_present(db, "groups") is True
        freed_addr = id(db.get_bind())
    del db, engine
    gc.collect()
    # Best-effort: churn fresh table-less engines to provoke address reuse; the correctness
    # assertion must hold on every iteration regardless of whether reuse actually occurs.
    for _ in range(500):
        fresh = _engine_with()
        with Session(fresh) as db:
            reused_freed_address = id(db.get_bind()) == freed_addr
            assert table_present(db, "groups") is False
            if reused_freed_address:
                break
        del db, fresh
        gc.collect()

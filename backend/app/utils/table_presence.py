"""Per-engine "does this optional table exist?" cache, safe against engine garbage collection.

Narrow service-level unit-test schemas (and the server-console bootstrap before migrations run)
may omit optional tables such as ``groups``, ``app_config`` or ``ai_config``. Presence-gated
service hooks must then no-op rather than blow up the caller's transaction, so they first ask this
module whether the table exists. Caching the answer avoids a per-call reflection round-trip on the
hot path.

The cache is keyed on the SQLAlchemy *bind* (engine/connection) via a :class:`WeakKeyDictionary`,
so an entry is dropped automatically when its engine is garbage-collected. A previous per-service
implementation keyed on ``id(bind)`` instead; CPython reuses the memory address of a freed engine,
so a later, narrower test database whose engine happened to reuse that address inherited the stale
``True`` answer and then queried a table that did not exist — an order-dependent, flaky
``sqlite3.OperationalError: no such table`` in CI. Keying on the live object (held weakly) removes
that aliasing entirely.
"""

from __future__ import annotations

from weakref import WeakKeyDictionary

from sqlalchemy import inspect
from sqlalchemy.orm import Session

# bind -> {table_name: present}. WeakKeyDictionary so a dead engine's entry is purged on GC.
_PRESENCE: WeakKeyDictionary = WeakKeyDictionary()


def table_present(db: Session, table_name: str) -> bool:
    """Return whether ``table_name`` exists in the session's database, memoized per engine.

    Reflects on the session's **live** connection (``db.connection()``), not a fresh one:
    inspecting the engine directly opens a separate connection which, under a SQLite ``StaticPool``
    single-connection test setup, can disrupt the caller's in-flight transaction (silently dropping
    pending inserts).
    """
    bind = db.get_bind()
    cache = _PRESENCE.get(bind)
    if cache is None:
        cache = {}
        _PRESENCE[bind] = cache
    present = cache.get(table_name)
    if present is None:
        present = inspect(db.connection()).has_table(table_name)
        cache[table_name] = present
    return present


def clear_cache() -> None:
    """Drop all memoized presence answers (test helper / post-schema-change invalidation)."""
    _PRESENCE.clear()

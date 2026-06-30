"""Owner-managed server import roots (batch 2 #19).

The "Server folder" import may only scan folders in the **merged** allowed-roots set: the read-only
``storage.server_allowed_roots`` entries from ``server.yaml`` PLUS the GUI-managed rows in the
``import_roots`` table. This module owns the listing + add/remove of the DB rows.

Safety: a GUI-added root is validated identically to a yaml root — it must be an absolute path that
resolves to an existing directory, and its alias must be unique across the whole merged set. A DB
row can never shadow / weaken a yaml entry (the yaml alias wins on a clash, see
:func:`app.services.storage.merged_server_roots`), and a yaml entry can never be removed via the GUI.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.import_root import ImportRoot
from app.services.storage import configured_server_roots, merged_server_roots


def list_merged_roots(db: Session, settings: Settings) -> list[dict]:
    """List the merged allowed roots, marking each as yaml-fixed or DB-removable.

    Returns one dict per alias: ``{alias, path, source ("yaml"|"db"), removable, id, exists}``.
    ``id`` is the DB row id for removable entries (``None`` for yaml). ``exists`` reports whether the
    path currently resolves to a directory (a yaml path may have gone away on disk).
    """
    yaml_roots = configured_server_roots(settings)
    db_rows = {row.alias: row for row in db.scalars(select(ImportRoot)).all()}

    items: list[dict] = []
    for alias, path in sorted(yaml_roots.items()):
        items.append(
            {
                "alias": alias,
                "path": str(path),
                "source": "yaml",
                "removable": False,
                "id": None,
                "exists": path.exists() and path.is_dir(),
            }
        )
    for alias, row in sorted(db_rows.items()):
        if alias in yaml_roots:
            # A yaml alias shadows the DB row (yaml wins); don't list the dead DB duplicate.
            continue
        resolved = Path(str(row.path)).expanduser().resolve()
        items.append(
            {
                "alias": alias,
                "path": str(resolved),
                "source": "db",
                "removable": True,
                "id": row.id,
                "exists": resolved.exists() and resolved.is_dir(),
            }
        )
    return items


def add_import_root(
    db: Session,
    *,
    settings: Settings,
    alias: str,
    path: str,
    created_by_user_id: uuid.UUID,
) -> ImportRoot:
    """Add a GUI-managed import root after validating it the same way a yaml root is validated.

    Raises ``ValueError`` if the alias/path is blank, the alias already exists in the merged set, or
    the path does not resolve to an existing directory.
    """
    alias_clean = (alias or "").strip()
    if not alias_clean:
        raise ValueError("Alias is required")
    if not (path or "").strip():
        raise ValueError("Path is required")

    resolved = Path(str(path).strip()).expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError("Path must be an existing directory on the server")

    # Alias must be unique across the whole MERGED set (yaml + every DB row), so a GUI root can
    # never collide with — or appear to override — a yaml-fixed alias.
    if alias_clean in merged_server_roots(db, settings):
        raise ValueError("Alias already in use")

    root = ImportRoot(alias=alias_clean, path=str(resolved), created_by_user_id=created_by_user_id)
    db.add(root)
    db.flush()
    return root


def remove_import_root(db: Session, *, root_id: uuid.UUID) -> None:
    """Remove a GUI-managed import root by id.

    Raises ``ValueError`` if no such DB row exists (yaml-fixed entries have no DB row, so they can
    never be targeted here).
    """
    root = db.get(ImportRoot, root_id)
    if root is None:
        raise ValueError("Import root not found")
    db.delete(root)
    db.flush()

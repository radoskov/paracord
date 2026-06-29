"""Resolve a backend-readable filesystem path for a stored :class:`File`.

A single resolver shared by PDF streaming (``api/v1/endpoints/files.py``) and GROBID
extraction (``services/extraction.py``) so both honour the same location-type support and
root-escape guards. Previously the two diverged: streaming served ``server_path`` *and*
``managed_path`` with root validation, while extraction resolved ``server_path`` only and did
no root check — so uploaded (``managed_path``) PDFs could never be extracted (AUDIT A1).

The resolver raises :class:`FileLocationError` (never an HTTP exception) so service-layer
callers such as the extraction worker stay framework-agnostic; the API layer translates the
error into the appropriate status code via the ``kind`` attribute.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.file import File, Location
from app.models.source import Source

# Location types that point at a filesystem path the backend can read directly.
_READABLE_LOCATION_TYPES = ["server_path", "managed_path"]


class FileLocationError(ValueError):
    """A file has no resolvable, in-bounds backend-readable path.

    Subclasses ``ValueError`` so existing service-layer callers that expect a ``ValueError``
    keep working. ``kind`` distinguishes a missing/unavailable location (``"not_found"``)
    from a stored path that escapes its configured root (``"forbidden"``) so the API layer
    can map it to 404 vs 403.
    """

    def __init__(self, message: str, *, kind: str = "not_found") -> None:
        super().__init__(message)
        self.kind = kind


def resolve_backend_readable_pdf_path(
    db: Session,
    *,
    file: File,
    settings: Settings,
) -> Path:
    """Return the validated on-disk PDF path for ``file``.

    Picks the primary, most-recently-created *available* location of a readable type and
    validates it against the appropriate root:

    * ``managed_path`` → must live under ``settings.managed_library_root``;
    * ``server_path``  → must live under the owning active server-folder source's root.

    Raises :class:`FileLocationError` when no readable location exists, the source is
    unavailable, or the stored path escapes its configured root.
    """
    location = db.scalar(
        select(Location)
        .where(
            Location.file_id == file.id,
            Location.location_type.in_(_READABLE_LOCATION_TYPES),
            Location.is_available.is_(True),
        )
        .order_by(Location.is_primary.desc(), Location.created_at.desc())
    )
    if location is None or not location.internal_uri:
        raise FileLocationError("No readable PDF location available for file")

    if location.location_type == "managed_path":
        return _validated_path(
            location.internal_uri,
            root=Path(settings.managed_library_root),
            escape_msg="File location escapes managed library root",
        )

    # server_path: the file lives under a configured server-folder source root.
    if location.source_id is None:
        raise FileLocationError("Server-path location has no associated source")
    source = db.get(Source, location.source_id)
    if source is None or source.type != "server_folder" or not source.is_active:
        raise FileLocationError("Server-folder source not available")
    raw_root = (source.config or {}).get("root_path")
    if not raw_root:
        raise FileLocationError("Server-folder source root not configured")
    return _validated_path(
        location.internal_uri,
        root=Path(str(raw_root)),
        escape_msg="File location escapes configured source root",
    )


def _validated_path(internal_uri: str, *, root: Path, escape_msg: str) -> Path:
    """Resolve ``internal_uri`` and assert it stays within ``root`` (no path traversal)."""
    resolved_root = root.expanduser().resolve()
    path = Path(internal_uri).expanduser().resolve()
    try:
        path.relative_to(resolved_root)
    except ValueError as exc:
        raise FileLocationError(escape_msg, kind="forbidden") from exc
    return path

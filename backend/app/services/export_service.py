"""Citation and bibliography export service."""

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.organization import RackShelf, ShelfWork
from app.models.work import Work

SUPPORTED_FORMATS = {"bibtex", "biblatex", "ris", "csl-json", "markdown", "html", "text"}


def export_bibliography(
    db: Session,
    *,
    scope_type: str,
    output_format: str,
    scope_id: str | None = None,
) -> str:
    """Export bibliography content for a scope."""
    if output_format not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported export format: {output_format}")
    works = _resolve_works(db, scope_type=scope_type, scope_id=scope_id)
    if output_format == "bibtex":
        return "\n\n".join(_work_to_bibtex(work, key) for work, key in _citation_keys(works))
    if output_format == "text":
        return "\n".join(_work_to_text(work) for work in works)
    raise ValueError(f"Export format not implemented yet: {output_format}")


def _resolve_works(db: Session, *, scope_type: str, scope_id: str | None) -> list[Work]:
    if scope_type == "work":
        if not scope_id:
            raise ValueError("scope_id is required for work export")
        work = db.get(Work, uuid.UUID(scope_id))
        return [work] if work else []
    if scope_type == "shelf":
        if not scope_id:
            raise ValueError("scope_id is required for shelf export")
        stmt = (
            select(Work)
            .join(ShelfWork, ShelfWork.work_id == Work.id)
            .where(ShelfWork.shelf_id == uuid.UUID(scope_id))
            .order_by(ShelfWork.position, Work.year, Work.canonical_title)
        )
        return list(db.scalars(stmt).all())
    if scope_type == "rack":
        if not scope_id:
            raise ValueError("scope_id is required for rack export")
        stmt = (
            select(Work)
            .join(ShelfWork, ShelfWork.work_id == Work.id)
            .join(RackShelf, RackShelf.shelf_id == ShelfWork.shelf_id)
            .where(RackShelf.rack_id == uuid.UUID(scope_id))
            .distinct()
            .order_by(Work.year, Work.canonical_title)
        )
        return list(db.scalars(stmt).all())
    raise ValueError(f"Unsupported export scope: {scope_type}")


def _citation_keys(works: list[Work]) -> list[tuple[Work, str]]:
    counts: dict[str, int] = {}
    keyed: list[tuple[Work, str]] = []
    for work in works:
        base = _base_key(work)
        count = counts.get(base, 0) + 1
        counts[base] = count
        keyed.append((work, base if count == 1 else f"{base}{count}"))
    return keyed


def _base_key(work: Work) -> str:
    title = work.canonical_title or "work"
    words = re.findall(r"[A-Za-z0-9]+", title)
    stem = (words[0] if words else "work").lower()
    year = str(work.year or "nd")
    return f"{stem}{year}"


def _work_to_bibtex(work: Work, key: str) -> str:
    fields = [f"  title = {{{_escape_bibtex(work.canonical_title or 'Untitled')}}}"]
    if work.year:
        fields.append(f"  year = {{{work.year}}}")
    if work.doi:
        fields.append(f"  doi = {{{_escape_bibtex(work.doi)}}}")
    if work.venue:
        fields.append(f"  journal = {{{_escape_bibtex(work.venue)}}}")
    return "@article{" + key + ",\n" + ",\n".join(fields) + "\n}"


def _work_to_text(work: Work) -> str:
    parts = [work.canonical_title or "Untitled"]
    if work.year:
        parts.append(str(work.year))
    if work.doi:
        parts.append(work.doi)
    return " - ".join(parts)


def _escape_bibtex(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")

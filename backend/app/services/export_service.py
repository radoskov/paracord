"""Citation and bibliography export service."""

import json
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.metadata import MetadataAssertion
from app.models.organization import RackShelf, ShelfWork
from app.models.work import Work
from app.services.audit import record_event

# format -> (file extension, content type)
FORMAT_MEDIA: dict[str, tuple[str, str]] = {
    "bibtex": ("bib", "application/x-bibtex"),
    "biblatex": ("bib", "application/x-bibtex"),
    "ris": ("ris", "application/x-research-info-systems"),
    "csl-json": ("json", "application/vnd.citationstyles.csl+json"),
    "markdown": ("md", "text/markdown"),
    "html": ("html", "text/html"),
    "text": ("txt", "text/plain"),
}
SUPPORTED_FORMATS = set(FORMAT_MEDIA)


@dataclass
class _Entry:
    """A work plus its resolved citation key and author list, ready to render."""

    work: Work
    authors: list[str] = field(default_factory=list)
    key: str = ""


def media_for(output_format: str) -> tuple[str, str]:
    """Return the (extension, content_type) for a supported export format."""
    if output_format not in FORMAT_MEDIA:
        raise ValueError(f"Unsupported export format: {output_format}")
    return FORMAT_MEDIA[output_format]


def export_bibliography(
    db: Session,
    *,
    scope_type: str,
    output_format: str,
    scope_id: str | None = None,
    work_ids: list[str] | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> str:
    """Export bibliography content for a scope.

    When ``actor_user_id`` is given, a ``paper.exported`` audit event is recorded in the
    current transaction (the caller is responsible for committing).
    """
    if output_format not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported export format: {output_format}")
    works = _resolve_works(db, scope_type=scope_type, scope_id=scope_id, work_ids=work_ids)
    entries = [_Entry(work=work, authors=_work_authors(db, work)) for work in works]
    _assign_keys(entries)
    content = _RENDERERS[output_format](entries)
    if actor_user_id is not None:
        record_event(
            db,
            "paper.exported",
            actor_user_id=actor_user_id,
            entity_type=scope_type,
            entity_id=scope_id,
            details={"format": output_format, "work_count": len(works)},
        )
    return content


def _resolve_works(
    db: Session, *, scope_type: str, scope_id: str | None, work_ids: list[str] | None = None
) -> list[Work]:
    if scope_type in ("selection", "search"):
        # An explicit set of works (multi-select in the library, or a search result set).
        if not work_ids:
            raise ValueError(f"work_ids is required for {scope_type} export")
        ids = [uuid.UUID(w) for w in work_ids]
        found = {w.id: w for w in db.scalars(select(Work).where(Work.id.in_(ids))).all()}
        return [found[i] for i in ids if i in found]  # preserve caller order
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


def _work_authors(db: Session, work: Work) -> list[str]:
    """Resolve a work's authors from its best metadata assertion (``A; B; C``)."""
    value = db.scalars(
        select(MetadataAssertion.value)
        .where(
            MetadataAssertion.entity_type == "work",
            MetadataAssertion.entity_id == work.id,
            MetadataAssertion.field_name == "authors",
        )
        .order_by(
            MetadataAssertion.selected_as_canonical.desc(),
            func.coalesce(MetadataAssertion.confidence, 0).desc(),
        )
    ).first()
    if not value:
        return []
    return [name.strip() for name in value.split(";") if name.strip()]


def _assign_keys(entries: list[_Entry]) -> None:
    """Assign each entry a unique ``authorYEAR`` (or ``titlewordYEAR``) citation key."""
    counts: dict[str, int] = {}
    for entry in entries:
        base = _base_key(entry)
        counts[base] = counts.get(base, 0) + 1
        entry.key = base if counts[base] == 1 else f"{base}{counts[base]}"


def _base_key(entry: _Entry) -> str:
    if entry.authors:
        first = entry.authors[0]
        surname = first.partition(",")[0] if "," in first else first.split()[-1]
        stem = re.sub(r"[^A-Za-z0-9]", "", surname).lower() or "work"
    else:
        words = re.findall(r"[A-Za-z0-9]+", entry.work.canonical_title or "work")
        stem = (words[0] if words else "work").lower()
    year = str(entry.work.year or "nd")
    return f"{stem}{year}"


def _split_name(name: str) -> dict[str, str]:
    """Best-effort split of a free-text author name into CSL family/given parts."""
    name = name.strip()
    if "," in name:
        family, _, given = name.partition(",")
        parts = {"family": family.strip()}
        if given.strip():
            parts["given"] = given.strip()
        return parts
    tokens = name.split()
    if len(tokens) <= 1:
        return {"family": name}
    return {"family": tokens[-1], "given": " ".join(tokens[:-1])}


# --- renderers (one per format; each takes the resolved entry list) ----------


def _render_bibtex(entries: list[_Entry]) -> str:
    return "\n\n".join(_entry_to_bibtex(e, biblatex=False) for e in entries)


def _render_biblatex(entries: list[_Entry]) -> str:
    return "\n\n".join(_entry_to_bibtex(e, biblatex=True) for e in entries)


def _entry_to_bibtex(entry: _Entry, *, biblatex: bool) -> str:
    work = entry.work
    fields: list[tuple[str, str]] = []
    if entry.authors:
        fields.append(("author", " and ".join(entry.authors)))
    fields.append(("title", work.canonical_title or "Untitled"))
    if work.year:
        fields.append(("date" if biblatex else "year", str(work.year)))
    if work.venue:
        fields.append(("journaltitle" if biblatex else "journal", work.venue))
    if work.doi:
        fields.append(("doi", work.doi))
    body = ",\n".join(f"  {name} = {{{_escape_bibtex(value)}}}" for name, value in fields)
    return "@article{" + entry.key + ",\n" + body + "\n}"


def _render_ris(entries: list[_Entry]) -> str:
    return "\n".join(_entry_to_ris(e) for e in entries)


def _entry_to_ris(entry: _Entry) -> str:
    work = entry.work
    lines = ["TY  - JOUR"]
    lines += [f"AU  - {author}" for author in entry.authors]
    lines.append(f"TI  - {work.canonical_title or 'Untitled'}")
    if work.year:
        lines.append(f"PY  - {work.year}")
    if work.venue:
        lines.append(f"JO  - {work.venue}")
    if work.doi:
        lines.append(f"DO  - {work.doi}")
    if work.abstract:
        lines.append(f"AB  - {work.abstract}")
    lines.append("ER  - ")
    return "\n".join(lines) + "\n"


def _render_csl_json(entries: list[_Entry]) -> str:
    items = []
    for entry in entries:
        work = entry.work
        item: dict = {"id": entry.key, "type": "article-journal"}
        if work.canonical_title:
            item["title"] = work.canonical_title
        if entry.authors:
            item["author"] = [_split_name(name) for name in entry.authors]
        if work.year:
            item["issued"] = {"date-parts": [[work.year]]}
        if work.venue:
            item["container-title"] = work.venue
        if work.doi:
            item["DOI"] = work.doi
        if work.abstract:
            item["abstract"] = work.abstract
        items.append(item)
    return json.dumps(items, indent=2, ensure_ascii=False)


def _render_markdown(entries: list[_Entry]) -> str:
    lines = ["# Bibliography", ""]
    for entry in entries:
        lines.append("- " + _entry_inline(entry, markdown=True))
    return "\n".join(lines) + "\n"


def _render_html(entries: list[_Entry]) -> str:
    items = "\n".join(f"  <li>{_entry_inline(e, markdown=False)}</li>" for e in entries)
    return (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        '  <meta charset="utf-8">\n  <title>Bibliography</title>\n'
        "</head>\n<body>\n<h1>Bibliography</h1>\n<ol>\n"
        f"{items}\n</ol>\n</body>\n</html>\n"
    )


def _render_text(entries: list[_Entry]) -> str:
    return "\n".join(_work_to_text(entry.work) for entry in entries)


def _entry_inline(entry: _Entry, *, markdown: bool) -> str:
    """One-line citation used by the Markdown and HTML renderers."""
    work = entry.work
    title = work.canonical_title or "Untitled"
    venue = work.venue
    if markdown:
        title = f"**{title}**"
        venue = f"*{venue}*" if venue else None
    else:
        title = f"<strong>{_escape_html(title)}</strong>"
        venue = f"<em>{_escape_html(venue)}</em>" if venue else None
    parts = [title]
    if work.year:
        parts[0] += f" ({work.year})."
    else:
        parts[0] += "."
    if entry.authors:
        authors = ", ".join(entry.authors)
        parts.append(authors if markdown else _escape_html(authors) + ".")
        if markdown:
            parts[-1] += "."
    if venue:
        parts.append(venue + ".")
    if work.doi:
        url = f"https://doi.org/{work.doi}"
        if markdown:
            parts.append(f"DOI: [{work.doi}]({url})")
        else:
            parts.append(f'DOI: <a href="{_escape_html(url)}">{_escape_html(work.doi)}</a>')
    return " ".join(parts)


def _work_to_text(work: Work) -> str:
    parts = [work.canonical_title or "Untitled"]
    if work.year:
        parts.append(str(work.year))
    if work.doi:
        parts.append(work.doi)
    return " - ".join(parts)


def _escape_bibtex(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


_RENDERERS = {
    "bibtex": _render_bibtex,
    "biblatex": _render_biblatex,
    "ris": _render_ris,
    "csl-json": _render_csl_json,
    "markdown": _render_markdown,
    "html": _render_html,
    "text": _render_text,
}

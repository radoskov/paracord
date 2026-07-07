"""Citation and bibliography export service."""

import csv
import io
import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.citation import Reference
from app.models.metadata import MetadataAssertion
from app.models.organization import RackShelf, ShelfWork
from app.models.work import Work
from app.services import csl
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
    "styled": ("txt", "text/plain"),  # rendered in a named citation style (see `style`)
    "latex": ("tex", "application/x-tex"),  # \cite commands + a thebibliography block
    "pandoc": ("md", "text/markdown"),  # Pandoc [@key] citations + a references list
}
SUPPORTED_FORMATS = set(FORMAT_MEDIA)


@dataclass
class _Entry:
    """A work plus its resolved citation key, author list, and extra citation metadata."""

    work: Work
    authors: list[str] = field(default_factory=list)
    key: str = ""
    # Extra bibliographic fields resolved from MetadataAssertions (volume/issue/pages/publisher),
    # used to enrich the CSL-JSON item for styled/csl-json rendering when available.
    meta: dict[str, str] = field(default_factory=dict)


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
    saved_filter_work_ids: list[uuid.UUID] | None = None,
    style: str | None = None,
    citation_keys: dict[str, str] | None = None,
    actor_user_id: uuid.UUID | None = None,
    visible_ids: set[uuid.UUID] | None = None,
) -> str:
    """Export bibliography content for a scope.

    ``citation_keys`` maps ``work_id`` → a user-chosen citation key, overriding the auto-assigned
    one. When ``actor_user_id`` is given, a ``paper.exported`` audit event is recorded in the
    current transaction (the caller is responsible for committing). ``visible_ids`` (Phase H access
    control) restricts the export to works the caller may see; ``None`` means unrestricted.
    ``saved_filter_work_ids`` (Phase B7) supplies the resolved id set for the ``saved_filter``
    scope (the endpoint loads + clamps the filter and passes the ids in); those works are still run
    through the ``visible_ids`` clamp here as a second belt-and-suspenders check.
    """
    if output_format not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported export format: {output_format}")
    if scope_type == "missing_references":
        # A dedicated target (SPEC §8.13): the unresolved citation references (they have no local
        # work, so no citation key) rendered as raw reference strings rather than a bibliography.
        refs = _resolve_unresolved_references(db, scope_id=scope_id, visible_ids=visible_ids)
        content = _render_missing_references(refs, output_format)
        if actor_user_id is not None:
            record_event(
                db,
                "paper.exported",
                actor_user_id=actor_user_id,
                entity_type=scope_type,
                entity_id=scope_id,
                details={"format": output_format, "reference_count": len(refs)},
            )
        return content
    works = _resolve_works(
        db,
        scope_type=scope_type,
        scope_id=scope_id,
        work_ids=work_ids,
        saved_filter_work_ids=saved_filter_work_ids,
        visible_ids=visible_ids,
    )
    authors_by_work = _authors_by_work(db, works)
    meta_by_work = _extra_meta_by_work(db, works)
    entries = [
        _Entry(
            work=work,
            authors=authors_by_work.get(work.id, []),
            meta=meta_by_work.get(work.id, {}),
        )
        for work in works
    ]
    _assign_keys(entries)
    if citation_keys:
        for entry in entries:
            override = citation_keys.get(str(entry.work.id))
            if override:
                entry.key = override
    if output_format == "styled":
        content = render_styled(entries, style or "apa")
    else:
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
    db: Session,
    *,
    scope_type: str,
    scope_id: str | None,
    work_ids: list[str] | None = None,
    saved_filter_work_ids: list[uuid.UUID] | None = None,
    visible_ids: set[uuid.UUID] | None = None,
) -> list[Work]:
    def _filter(works: list[Work]) -> list[Work]:
        # Always drop merged shadows (Batch D) — never exported for anyone — then apply the
        # per-user visibility clamp (``visible_ids`` is None for admin/owner).
        return [
            w
            for w in works
            if w.merged_into_id is None and (visible_ids is None or w.id in visible_ids)
        ]

    if scope_type == "saved_filter":
        # A saved filter resolved to its work ids by the endpoint (already visibility-clamped for
        # the actor). Fetch + re-clamp here, ordered like the library (year, title).
        if not saved_filter_work_ids:
            return []
        stmt = (
            select(Work)
            .where(Work.id.in_(saved_filter_work_ids))
            .order_by(Work.year, Work.canonical_title)
        )
        return _filter(list(db.scalars(stmt).all()))
    if scope_type in ("selection", "search"):
        # An explicit set of works (multi-select in the library, or a search result set).
        if not work_ids:
            raise ValueError(f"work_ids is required for {scope_type} export")
        ids = [uuid.UUID(w) for w in work_ids]
        found = {w.id: w for w in db.scalars(select(Work).where(Work.id.in_(ids))).all()}
        ordered = [found[i] for i in ids if i in found]  # preserve caller order
        return _filter(ordered)
    if scope_type == "library":
        # Whole-library export (also the library-scoped graph/insights view).
        return _filter(
            list(db.scalars(select(Work).order_by(Work.year, Work.canonical_title)).all())
        )
    if scope_type == "work":
        if not scope_id:
            raise ValueError("scope_id is required for work export")
        work = db.get(Work, uuid.UUID(scope_id))
        return _filter([work]) if work else []
    if scope_type == "shelf":
        if not scope_id:
            raise ValueError("scope_id is required for shelf export")
        stmt = (
            select(Work)
            .join(ShelfWork, ShelfWork.work_id == Work.id)
            .where(ShelfWork.shelf_id == uuid.UUID(scope_id))
            .order_by(ShelfWork.position, Work.year, Work.canonical_title)
        )
        return _filter(list(db.scalars(stmt).all()))
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
        return _filter(list(db.scalars(stmt).all()))
    if scope_type == "import_batch":
        # All works created by one import activity (Work.import_batch_id), for a per-batch export.
        if not scope_id:
            raise ValueError("scope_id is required for import_batch export")
        stmt = (
            select(Work)
            .where(Work.import_batch_id == uuid.UUID(scope_id))
            .order_by(Work.year, Work.canonical_title)
        )
        return _filter(list(db.scalars(stmt).all()))
    raise ValueError(f"Unsupported export scope: {scope_type}")


def _resolve_unresolved_references(
    db: Session,
    *,
    scope_id: str | None = None,
    visible_ids: set[uuid.UUID] | None = None,
) -> list[Reference]:
    """Resolve unresolved citation references (no local ``resolved_work_id``).

    ``scope_id`` (optional) narrows to a single citing work; otherwise every citing work is
    considered. ``visible_ids`` clamps to references whose *citing* work the caller may see
    (``None`` = unrestricted). Ordered by citing work then creation for a stable listing.
    """
    stmt = select(Reference).where(Reference.resolved_work_id.is_(None))
    if scope_id:
        stmt = stmt.where(Reference.citing_work_id == uuid.UUID(scope_id))
    if visible_ids is not None:
        stmt = stmt.where(Reference.citing_work_id.in_(visible_ids))
    stmt = stmt.order_by(Reference.citing_work_id, Reference.created_at)
    return list(db.scalars(stmt).all())


def _split_authors(value: str | None) -> list[str]:
    return [name.strip() for name in (value or "").split(";") if name.strip()] if value else []


def _authors_by_work(db: Session, works: list[Work]) -> dict[uuid.UUID, list[str]]:
    """Resolve authors for all works in **one** query (E1: avoids the per-work N+1).

    Orders by entity then best-assertion (canonical, then confidence), so the first row seen for
    each work is its best ``authors`` assertion.
    """
    ids = [w.id for w in works]
    if not ids:
        return {}
    rows = db.execute(
        select(MetadataAssertion.entity_id, MetadataAssertion.value)
        .where(
            MetadataAssertion.entity_type == "work",
            MetadataAssertion.entity_id.in_(ids),
            MetadataAssertion.field_name == "authors",
        )
        .order_by(
            MetadataAssertion.entity_id,
            MetadataAssertion.selected_as_canonical.desc(),
            func.coalesce(MetadataAssertion.confidence, 0).desc(),
        )
    ).all()
    best: dict[uuid.UUID, str] = {}
    for entity_id, value in rows:
        if entity_id not in best:  # first row per work wins (ordering above)
            best[entity_id] = value
    return {wid: _split_authors(value) for wid, value in best.items()}


# Extra CSL-relevant fields that may be recorded as MetadataAssertions (not columns on ``Work``).
# ``number`` is an alias some importers use for the journal issue.
_EXTRA_META_FIELDS = ("volume", "issue", "number", "pages", "page", "publisher")


def _extra_meta_by_work(db: Session, works: list[Work]) -> dict[uuid.UUID, dict[str, str]]:
    """Resolve extra bibliographic fields (volume/issue/pages/publisher) in one query.

    Only some importers record these as assertions, so they are best-effort: absent fields simply
    don't appear on the CSL-JSON item. Best assertion per (work, field) wins (canonical, then
    confidence), mirroring :func:`_authors_by_work`.
    """
    ids = [w.id for w in works]
    if not ids:
        return {}
    rows = db.execute(
        select(
            MetadataAssertion.entity_id,
            MetadataAssertion.field_name,
            MetadataAssertion.value,
        )
        .where(
            MetadataAssertion.entity_type == "work",
            MetadataAssertion.entity_id.in_(ids),
            MetadataAssertion.field_name.in_(_EXTRA_META_FIELDS),
        )
        .order_by(
            MetadataAssertion.entity_id,
            MetadataAssertion.field_name,
            MetadataAssertion.selected_as_canonical.desc(),
            func.coalesce(MetadataAssertion.confidence, 0).desc(),
        )
    ).all()
    out: dict[uuid.UUID, dict[str, str]] = {}
    for entity_id, field_name, value in rows:
        if not value:
            continue
        bucket = out.setdefault(entity_id, {})
        # First row per (work, field) wins; normalize ``number`` -> ``issue``, ``page`` -> ``pages``.
        key = {"number": "issue", "page": "pages"}.get(field_name, field_name)
        bucket.setdefault(key, value.strip())
    return out


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


# Map heterogeneous ``Work.work_type`` values (bibtex entry types, RIS codes, CSL types) to a CSL
# item type. Anything unrecognized falls back to ``article-journal``.
_CSL_TYPE_MAP = {
    # already-CSL types (pass through)
    "article-journal": "article-journal",
    "paper-conference": "paper-conference",
    "book": "book",
    "chapter": "chapter",
    "thesis": "thesis",
    "report": "report",
    "dataset": "dataset",
    # bibtex entry types
    "article": "article-journal",
    "inproceedings": "paper-conference",
    "conference": "paper-conference",
    "proceedings": "paper-conference",
    "incollection": "chapter",
    "inbook": "chapter",
    "book_chapter": "chapter",
    "phdthesis": "thesis",
    "mastersthesis": "thesis",
    "techreport": "report",
    "misc": "article",
    # RIS type codes
    "jour": "article-journal",
    "conf": "paper-conference",
    "cpaper": "paper-conference",
    "chap": "chapter",
    "thes": "thesis",
    "rprt": "report",
    "data": "dataset",
    # PaRacORD internal / source hints
    "preprint": "article-journal",
    "journal_article": "article-journal",
    "conference_paper": "paper-conference",
}


def _csl_type(work: Work) -> str:
    raw = (work.work_type or "").strip().lower()
    if not raw:
        # arXiv-only records with no venue are effectively preprints/journal articles.
        return "article-journal"
    return _CSL_TYPE_MAP.get(raw, "article-journal")


def _entry_to_csl_item(entry: _Entry) -> dict:
    """Map a resolved :class:`_Entry` to a single CSL-JSON item.

    Missing fields are simply omitted so citeproc renders gracefully. Author names are split into
    family/given parts; ``issued`` uses year date-parts; ``container-title`` carries the venue; and
    volume/issue/pages/publisher/DOI are included when resolved from assertions or columns.
    """
    work = entry.work
    item: dict = {"id": entry.key, "type": _csl_type(work)}
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
    # Extra bibliographic fields resolved from MetadataAssertions, when present.
    for src_key, csl_key in (
        ("volume", "volume"),
        ("issue", "issue"),
        ("pages", "page"),
        ("publisher", "publisher"),
    ):
        value = entry.meta.get(src_key)
        if value:
            item[csl_key] = value
    return item


def _render_csl_json(entries: list[_Entry]) -> str:
    items = []
    for entry in entries:
        item = _entry_to_csl_item(entry)
        # csl-json export also carries the abstract (not used by rendered styles).
        if entry.work.abstract:
            item["abstract"] = entry.work.abstract
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


# Citation styles are rendered with real CSL via citeproc-py (Phase B4). ``STYLES`` is the ordered
# tuple of supported style keys (``apa``/``ieee``/``chicago`` kept from before, plus mla/harvard/
# vancouver/nature); the .csl files + license attribution live under ``app/services/csl/``.
STYLES = csl.CITATION_STYLES


def render_styled(entries: list[_Entry], style: str) -> str:
    """Render a human-readable reference list in a named CSL style.

    Delegates to citeproc-py via :mod:`app.services.csl`. Each entry is mapped to a CSL-JSON item;
    rendering is per-item defensive (a malformed record degrades to a minimal safe string instead
    of failing the whole export). Raises ``ValueError`` for an unknown style key.
    """
    items = [_entry_to_csl_item(entry) for entry in entries]
    return csl.render_bibliography(items, style or "apa")


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


def _render_latex(entries: list[_Entry]) -> str:
    """Emit LaTeX ``\\cite{...}`` commands plus a ``thebibliography`` block (SPEC §8.13).

    The leading ``\\cite`` collects every key (a ready-to-paste multi-cite); each work then gets a
    ``\\bibitem`` in the ``thebibliography`` environment. Values are LaTeX-escaped.
    """
    if not entries:
        return ""
    keys = ",".join(entry.key for entry in entries)
    lines = [f"\\cite{{{keys}}}", "", "\\begin{thebibliography}{99}"]
    for entry in entries:
        lines.append(f"\\bibitem{{{entry.key}}} {_latex_reference(entry)}")
    lines.append("\\end{thebibliography}")
    return "\n".join(lines) + "\n"


def _latex_reference(entry: _Entry) -> str:
    """One ``\\bibitem`` body: ``Authors. Title. \\emph{Venue}, Year. DOI: x.`` (LaTeX-escaped)."""
    work = entry.work
    parts: list[str] = []
    if entry.authors:
        parts.append(_escape_latex(", ".join(entry.authors)) + ".")
    parts.append(_escape_latex(work.canonical_title or "Untitled") + ".")
    if work.venue:
        venue = f"\\emph{{{_escape_latex(work.venue)}}}"
        parts.append(f"{venue}, {work.year}." if work.year else f"{venue}.")
    elif work.year:
        parts.append(f"{work.year}.")
    if work.doi:
        parts.append(f"DOI: {_escape_latex(work.doi)}.")
    return " ".join(parts)


def _render_pandoc(entries: list[_Entry]) -> str:
    """Emit Pandoc-Markdown citations ``[@key; @key]`` plus a references list (SPEC §8.13).

    The leading ``[@...]`` is a ready-to-paste combined citation; the ``# References`` list gives
    each key its rendered reference so the output is self-contained without a separate .bib file.
    """
    if not entries:
        return ""
    combined = "; ".join(f"@{entry.key}" for entry in entries)
    lines = [f"[{combined}]", "", "# References", ""]
    for entry in entries:
        lines.append(f"- [@{entry.key}]: {_entry_inline(entry, markdown=True)}")
    return "\n".join(lines) + "\n"


def _reference_string(reference: Reference) -> str:
    """Render one unresolved reference as a human string (raw citation, else composed metadata)."""
    if reference.raw_citation and reference.raw_citation.strip():
        return reference.raw_citation.strip()
    parts: list[str] = []
    if reference.title:
        parts.append(reference.title.strip())
    if reference.year:
        parts.append(f"({reference.year})")
    if reference.doi:
        parts.append(f"DOI: {reference.doi}")
    if reference.arxiv_id:
        parts.append(f"arXiv:{reference.arxiv_id}")
    return " ".join(parts) or "Untitled reference"


def _render_missing_references(references: list[Reference], output_format: str) -> str:
    """Render unresolved-reference strings for the ``missing_references`` target.

    These references have no local work (hence no citation key), so the citation-format families
    collapse to a plain string listing: a Markdown bullet list for ``markdown``/``pandoc``, a JSON
    array for ``csl-json``, and one string per line otherwise.
    """
    strings = [_reference_string(reference) for reference in references]
    if output_format in ("markdown", "pandoc"):
        return "\n".join(["# Unresolved references", "", *(f"- {s}" for s in strings)]) + "\n"
    if output_format == "csl-json":
        return json.dumps([{"raw": s} for s in strings], indent=2, ensure_ascii=False)
    return "\n".join(strings) + ("\n" if strings else "")


def _work_to_text(work: Work) -> str:
    parts = [work.canonical_title or "Untitled"]
    if work.year:
        parts.append(str(work.year))
    if work.doi:
        parts.append(work.doi)
    return " - ".join(parts)


def _escape_bibtex(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


# LaTeX special characters → their escaped forms. Backslash is handled first (its replacement
# introduces backslashes that must not be re-escaped), so this is applied char-by-char.
_LATEX_SPECIALS = {
    "\\": "\\textbackslash{}",
    "&": "\\&",
    "%": "\\%",
    "$": "\\$",
    "#": "\\#",
    "_": "\\_",
    "{": "\\{",
    "}": "\\}",
    "~": "\\textasciitilde{}",
    "^": "\\textasciicircum{}",
}


def _escape_latex(value: str) -> str:
    return "".join(_LATEX_SPECIALS.get(char, char) for char in value)


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
    "latex": _render_latex,
    "pandoc": _render_pandoc,
}


# --- frequently-cited-but-missing list export (Track C C3b) ------------------------------------
#
# These entries are aggregated citation references (not local works), so only what the reference
# carried is known — a citation key, title, year, DOI/arXiv, and how often the scope cites them.


class _MissingItem(Protocol):
    """Structural type for a frequently-cited-missing entry (``citation_summary.MissingWork``)."""

    key: str
    title: str
    doi: str | None
    arxiv_id: str | None
    year: int | None
    cited_by_count: int
    mention_count: int


# format -> (file extension, content type) for the missing-list export.
MISSING_EXPORT_FORMATS: dict[str, tuple[str, str]] = {
    "bibtex": ("bib", "application/x-bibtex"),
    "csv": ("csv", "text/csv"),
}


def _missing_citation_key(item: _MissingItem, used: set[str]) -> str:
    """A unique ``firstwordYEAR`` citation key for a missing entry (deduped within the batch)."""
    words = re.findall(r"[A-Za-z0-9]+", item.title or "")
    stem = (words[0] if words else "work").lower()
    base = f"{stem}{item.year or 'nd'}"
    key = base
    suffix = 1
    while key in used:
        suffix += 1
        key = f"{base}{suffix}"
    used.add(key)
    return key


def render_missing_works(items: Sequence[_MissingItem], output_format: str) -> str:
    """Render the frequently-cited-but-missing list as BibTeX or CSV (Track C C3b)."""
    if output_format == "bibtex":
        return _render_missing_bibtex(items)
    if output_format == "csv":
        return _render_missing_csv(items)
    raise ValueError(f"Unsupported missing-list export format: {output_format}")


def _render_missing_bibtex(items: Sequence[_MissingItem]) -> str:
    used: set[str] = set()
    entries: list[str] = []
    for item in items:
        key = _missing_citation_key(item, used)
        fields: list[tuple[str, str]] = [("title", item.title or "Untitled")]
        if item.year:
            fields.append(("year", str(item.year)))
        if item.doi:
            fields.append(("doi", item.doi))
        if item.arxiv_id:
            fields.append(("eprint", item.arxiv_id))
            fields.append(("archivePrefix", "arXiv"))
        fields.append(("note", f"Cited by {item.cited_by_count} paper(s) in the scope"))
        body = ",\n".join(f"  {name} = {{{_escape_bibtex(value)}}}" for name, value in fields)
        entries.append("@misc{" + key + ",\n" + body + "\n}")
    return "\n\n".join(entries)


def _render_missing_csv(items: Sequence[_MissingItem]) -> str:
    used: set[str] = set()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["key", "title", "authors", "year", "doi", "arxiv", "cited_by_count", "mention_count"]
    )
    for item in items:
        key = _missing_citation_key(item, used)
        writer.writerow(
            [
                key,
                item.title or "",
                "",  # authors are not carried on aggregated references
                item.year or "",
                item.doi or "",
                item.arxiv_id or "",
                item.cited_by_count,
                item.mention_count,
            ]
        )
    return buffer.getvalue()

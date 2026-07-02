"""BibTeX import (SPEC §8.1/§10.4).

A small, dependency-free BibTeX reader: it scans ``@type{key, field = value, ...}`` blocks with
balanced-brace handling and maps each entry to a Work, recording authors as a provenance-tagged
MetadataAssertion (source ``bibtex``). Entries are de-duplicated against the existing library by
normalized DOI and normalized title so re-importing the same file does not create duplicates.

Imported works are left ``user_confirmed=False`` so later identifier-based enrichment can still
fill gaps (the import is treated as a strong but not user-locked source).
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.metadata import MetadataAssertion
from app.models.source import ImportBatch
from app.models.user import User
from app.models.work import Work
from app.services.audit import record_event
from app.services.identifiers import arxiv_base_id as _arxiv_base_id
from app.utils.normalization import normalize_doi, normalize_title

_ENTRY_START = re.compile(r"@(\w+)\s*\{", re.IGNORECASE)
_IGNORED_TYPES = {"comment", "string", "preamble"}
_VENUE_FIELDS = ("journal", "booktitle", "journaltitle", "publisher")


@dataclass
class BibtexEntry:
    entry_type: str
    key: str
    fields: dict[str, str] = field(default_factory=dict)


def parse_bibtex(content: str) -> list[BibtexEntry]:
    """Parse BibTeX text into entries (ignoring @comment/@string/@preamble)."""
    entries: list[BibtexEntry] = []
    pos = 0
    while True:
        match = _ENTRY_START.search(content, pos)
        if match is None:
            break
        body, pos = _read_balanced(content, match.end())
        entry_type = match.group(1).lower()
        if entry_type in _IGNORED_TYPES:
            continue
        entry = _parse_entry_body(entry_type, body)
        if entry is not None and entry.fields:
            entries.append(entry)
    return entries


def parse_bibtex_authors(value: str | None) -> list[str]:
    """Split a BibTeX ``author`` field on `` and ``, normalizing ``Last, First`` to ``First Last``."""
    if not value:
        return []
    authors: list[str] = []
    for raw in re.split(r"\s+and\s+", value):
        name = raw.strip()
        if not name:
            continue
        if "," in name:
            last, _, first = name.partition(",")
            name = f"{first.strip()} {last.strip()}".strip()
        authors.append(" ".join(name.split()))
    return authors


def import_bibtex(db: Session, content: str, *, actor: User, target_shelf_id=None) -> ImportBatch:
    """Create works from BibTeX content and record an import batch + audit event.

    ``target_shelf_id`` (additive — Phase J item 6) optionally adds every created/matched work to a
    shelf through the shared ACL-checked helper (404/403 abort the whole import before partial state).
    """
    entries = parse_bibtex(content)
    from app.services.app_config import enforce_batch_limit

    enforce_batch_limit(db, len(entries))
    created = 0
    matched = 0
    skipped = 0
    added_to_shelf = 0
    now = datetime.now(UTC)
    # Create the batch up front so each new work can carry its import_batch_id (Phase B6).
    batch = ImportBatch(
        created_by_user_id=actor.id,
        input_type="bibtex",
        status="running",
        started_at=now,
    )
    db.add(batch)
    db.flush()

    def _add_to_shelf(work_id) -> None:
        nonlocal added_to_shelf
        if target_shelf_id is None:
            from app.services.default_shelf import place_on_default_if_loose

            place_on_default_if_loose(
                db, work_id, actor_id=actor.id
            )  # no free-floating papers (#1)
            return
        from app.services.shelf_membership import add_work_to_shelf_checked

        add_work_to_shelf_checked(db, shelf_id=target_shelf_id, work_id=work_id, actor=actor)
        added_to_shelf += 1

    for entry in entries:
        title = entry.fields.get("title")
        if not title:
            skipped += 1
            continue
        raw_doi = entry.fields.get("doi")
        doi = normalize_doi(raw_doi) if raw_doi else None
        normalized = normalize_title(title)
        existing = _find_existing(db, doi=doi, normalized_title=normalized)
        if existing is not None:
            matched += 1
            _add_to_shelf(existing.id)
            continue
        raw_arxiv_id = _arxiv_id(entry.fields)
        work = Work(
            canonical_title=title,
            normalized_title=normalized,
            year=_parse_year(entry.fields.get("year")),
            doi=doi,
            arxiv_id=raw_arxiv_id,
            arxiv_base_id=_arxiv_base_id(raw_arxiv_id),
            venue=_first_field(entry.fields, _VENUE_FIELDS),
            abstract=entry.fields.get("abstract"),
            work_type=entry.entry_type,
            canonical_metadata_source="bibtex",
            created_by_user_id=actor.id,
            import_batch_id=batch.id,
        )
        db.add(work)
        db.flush()
        authors = parse_bibtex_authors(entry.fields.get("author"))
        if authors:
            db.add(
                MetadataAssertion(
                    entity_type="work",
                    entity_id=work.id,
                    field_name="authors",
                    value="; ".join(authors),
                    source="bibtex",
                    confidence=1.0,
                    selected_as_canonical=True,
                )
            )
        created += 1
        _add_to_shelf(work.id)

    stats = {"entries": len(entries), "created": created, "matched": matched, "skipped": skipped}
    if target_shelf_id is not None:
        stats["added_to_shelf"] = added_to_shelf
        stats["target_shelf_id"] = str(target_shelf_id)
    batch.status = "completed"
    batch.stats = stats
    batch.finished_at = datetime.now(UTC)
    db.flush()
    record_event(
        db,
        "import.bibtex",
        actor_user_id=actor.id,
        entity_type="import_batch",
        entity_id=str(batch.id),
        details=stats,
    )
    return batch


def _read_balanced(content: str, start: int) -> tuple[str, int]:
    """Return (body, index-after-close) for a brace block whose opening ``{`` was at start-1."""
    depth = 1
    index = start
    length = len(content)
    while index < length and depth > 0:
        char = content[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    return content[start : index - 1], index


def _parse_entry_body(entry_type: str, body: str) -> BibtexEntry | None:
    parts = _split_top_level(body)
    if not parts:
        return None
    key = parts[0].strip()
    fields: dict[str, str] = {}
    for chunk in parts[1:]:
        if "=" not in chunk:
            continue
        name, _, value = chunk.partition("=")
        name = name.strip().lower()
        cleaned = _clean_value(value)
        if name and cleaned:
            fields[name] = cleaned
    return BibtexEntry(entry_type=entry_type, key=key, fields=fields)


def _split_top_level(text: str) -> list[str]:
    """Split on commas that are at brace-depth 0 and outside double quotes."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_quote = False
    for char in text:
        if char == '"' and depth == 0:
            in_quote = not in_quote
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        if char == "," and depth == 0 and not in_quote:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def _clean_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '{"':
        value = value[1:-1]
    value = value.replace("{", "").replace("}", "")
    return " ".join(value.split())


def _parse_year(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"\d{4}", value)
    return int(match.group()) if match else None


def _first_field(fields: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        if fields.get(name):
            return fields[name]
    return None


def _arxiv_id(fields: dict[str, str]) -> str | None:
    if fields.get("arxiv"):
        return fields["arxiv"]
    archive = (fields.get("archiveprefix") or fields.get("eprinttype") or "").lower()
    if "arxiv" in archive and fields.get("eprint"):
        return fields["eprint"]
    return None


def _find_existing(db: Session, *, doi: str | None, normalized_title: str | None) -> Work | None:
    if doi:
        # doi is expected to be already normalized (bare, lowercase, no prefix).
        existing = db.scalar(select(Work).where(Work.doi == doi))
        if existing is not None:
            return existing
    if normalized_title:
        existing = db.scalar(select(Work).where(Work.normalized_title == normalized_title))
        if existing is not None:
            return existing
    return None

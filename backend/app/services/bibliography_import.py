"""RIS and CSL-JSON bibliography import.

Both formats are parsed into a common ``BiblioRecord`` and ingested through one upsert path
that mirrors the BibTeX importer (dedup by normalized DOI / title, author assertion,
``ImportBatch`` + audit event), so all three ingestion routes behave consistently.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.metadata import MetadataAssertion
from app.models.source import ImportBatch
from app.models.user import User
from app.models.work import Work
from app.services.audit import record_event
from app.utils.normalization import normalize_doi, normalize_title


@dataclass
class BiblioRecord:
    title: str
    doi: str | None = None
    year: int | None = None
    venue: str | None = None
    abstract: str | None = None
    work_type: str | None = None
    authors: list[str] = field(default_factory=list)


# --- RIS --------------------------------------------------------------------

_RIS_TITLE = ("TI", "T1")
_RIS_VENUE = ("JO", "JF", "T2", "BT")
_RIS_ABSTRACT = ("AB", "N2")


def parse_ris(content: str) -> list[BiblioRecord]:
    """Parse RIS text into records. Tags are ``XY  - value``; ``ER`` ends a record."""
    records: list[BiblioRecord] = []
    current: dict[str, list[str]] | None = None
    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        if len(line) < 6 or line[2:6] != "  - ":
            continue
        tag = line[:2].upper()
        value = line[6:].strip()
        if tag == "TY":
            current = {"TY": [value]}
            continue
        if current is None:
            continue
        if tag == "ER":
            records.append(_ris_to_record(current))
            current = None
            continue
        current.setdefault(tag, []).append(value)
    if current is not None:
        records.append(_ris_to_record(current))
    return [r for r in records if r.title]


def _ris_first(fields: dict[str, list[str]], tags: tuple[str, ...]) -> str | None:
    for tag in tags:
        if fields.get(tag):
            return fields[tag][0]
    return None


def _ris_to_record(fields: dict[str, list[str]]) -> BiblioRecord:
    year_raw = _ris_first(fields, ("PY", "Y1", "DA"))
    year = None
    if year_raw:
        digits = "".join(ch for ch in year_raw[:4] if ch.isdigit())
        year = int(digits) if len(digits) == 4 else None
    return BiblioRecord(
        title=_ris_first(fields, _RIS_TITLE) or "",
        doi=_ris_first(fields, ("DO",)),
        year=year,
        venue=_ris_first(fields, _RIS_VENUE),
        abstract=_ris_first(fields, _RIS_ABSTRACT),
        work_type=_ris_first(fields, ("TY",)),
        authors=[a for a in (fields.get("AU", []) + fields.get("A1", [])) if a],
    )


# --- CSL JSON ---------------------------------------------------------------


def parse_csl(content: str) -> list[BiblioRecord]:
    """Parse CSL-JSON (an array of items, or a single item) into records."""
    data = json.loads(content)
    items = data if isinstance(data, list) else [data]
    records: list[BiblioRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        record = _csl_to_record(item)
        if record.title:
            records.append(record)
    return records


def _csl_to_record(item: dict) -> BiblioRecord:
    authors: list[str] = []
    for author in item.get("author", []) or []:
        if not isinstance(author, dict):
            continue
        name = " ".join(p for p in (author.get("given"), author.get("family")) if p).strip()
        name = name or (author.get("literal") or "")
        if name:
            authors.append(name)

    year = None
    issued = item.get("issued")
    if isinstance(issued, dict):
        parts = issued.get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            try:
                year = int(parts[0][0])
            except (TypeError, ValueError):
                year = None

    container = item.get("container-title")
    if isinstance(container, list):
        container = container[0] if container else None

    return BiblioRecord(
        title=str(item.get("title") or ""),
        doi=item.get("DOI"),
        year=year,
        venue=container if isinstance(container, str) else None,
        abstract=item.get("abstract"),
        work_type=item.get("type"),
        authors=authors,
    )


# --- shared ingestion -------------------------------------------------------


def _find_existing(db: Session, *, doi: str | None, normalized_title: str | None) -> Work | None:
    if doi:
        existing = db.scalar(select(Work).where(Work.doi == doi))
        if existing:
            return existing
    if normalized_title:
        existing = db.scalar(select(Work).where(Work.normalized_title == normalized_title))
        if existing:
            return existing
    return None


def import_records(
    db: Session,
    records: list[BiblioRecord],
    *,
    actor: User,
    input_type: str,
    event_type: str,
) -> ImportBatch:
    """Upsert records into works (dedup by DOI/title) and record an import batch."""
    created = 0
    matched = 0
    skipped = 0
    for record in records:
        if not record.title:
            skipped += 1
            continue
        doi = normalize_doi(record.doi) if record.doi else None
        normalized = normalize_title(record.title)
        if _find_existing(db, doi=doi, normalized_title=normalized) is not None:
            matched += 1
            continue
        work = Work(
            canonical_title=record.title,
            normalized_title=normalized,
            year=record.year,
            doi=doi,
            venue=record.venue,
            abstract=record.abstract,
            work_type=record.work_type,
            canonical_metadata_source=input_type,
        )
        db.add(work)
        db.flush()
        if record.authors:
            db.add(
                MetadataAssertion(
                    entity_type="work",
                    entity_id=work.id,
                    field_name="authors",
                    value="; ".join(record.authors),
                    source=input_type,
                    confidence=1.0,
                    selected_as_canonical=True,
                )
            )
        created += 1

    now = datetime.now(UTC)
    stats = {"entries": len(records), "created": created, "matched": matched, "skipped": skipped}
    batch = ImportBatch(
        created_by_user_id=actor.id,
        input_type=input_type,
        status="completed",
        stats=stats,
        started_at=now,
        finished_at=now,
    )
    db.add(batch)
    db.flush()
    record_event(
        db,
        event_type,
        actor_user_id=actor.id,
        entity_type="import_batch",
        entity_id=str(batch.id),
        details=stats,
    )
    return batch


def import_ris(db: Session, content: str, *, actor: User) -> ImportBatch:
    """Create works from RIS content."""
    return import_records(
        db, parse_ris(content), actor=actor, input_type="ris", event_type="import.ris"
    )


def import_csl(db: Session, content: str, *, actor: User) -> ImportBatch:
    """Create works from CSL-JSON content."""
    return import_records(
        db, parse_csl(content), actor=actor, input_type="csl", event_type="import.csl"
    )

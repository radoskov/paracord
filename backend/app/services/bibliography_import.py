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
    target_shelf_id=None,
) -> ImportBatch:
    """Upsert records into works (dedup by DOI/title) and record an import batch.

    ``target_shelf_id`` (additive — Phase J item 6) optionally adds every created/matched work to a
    shelf through the shared ACL-checked helper, so a missing shelf (404) or lack of modify access
    (403) aborts the whole import before any partial state.
    """
    from app.services.app_config import enforce_batch_limit

    enforce_batch_limit(db, len(records))
    created = 0
    matched = 0
    skipped = 0
    added_to_shelf = 0
    now = datetime.now(UTC)
    # Create the batch up front so each new work can carry its import_batch_id (Phase B6).
    batch = ImportBatch(
        created_by_user_id=actor.id,
        input_type=input_type,
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

    for record in records:
        if not record.title:
            skipped += 1
            continue
        doi = normalize_doi(record.doi) if record.doi else None
        normalized = normalize_title(record.title)
        existing = _find_existing(db, doi=doi, normalized_title=normalized)
        if existing is not None:
            matched += 1
            _add_to_shelf(existing.id)
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
            created_by_user_id=actor.id,
            import_batch_id=batch.id,
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
        _add_to_shelf(work.id)

    stats = {"entries": len(records), "created": created, "matched": matched, "skipped": skipped}
    if target_shelf_id is not None:
        stats["added_to_shelf"] = added_to_shelf
        stats["target_shelf_id"] = str(target_shelf_id)
    batch.status = "completed"
    batch.stats = stats
    batch.finished_at = datetime.now(UTC)
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


def import_ris(db: Session, content: str, *, actor: User, target_shelf_id=None) -> ImportBatch:
    """Create works from RIS content."""
    return import_records(
        db,
        parse_ris(content),
        actor=actor,
        input_type="ris",
        event_type="import.ris",
        target_shelf_id=target_shelf_id,
    )


def import_csl(db: Session, content: str, *, actor: User, target_shelf_id=None) -> ImportBatch:
    """Create works from CSL-JSON content."""
    return import_records(
        db,
        parse_csl(content),
        actor=actor,
        input_type="csl",
        event_type="import.csl",
        target_shelf_id=target_shelf_id,
    )

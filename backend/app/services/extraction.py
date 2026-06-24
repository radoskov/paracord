"""Persist GROBID extraction results as provenance-aware metadata and references.

Follows the spec rule that external sources never silently overwrite user-confirmed data:
every value is recorded as a MetadataAssertion, and canonical fields are only promoted when
the work has not been user-confirmed and the field is empty or filename-derived.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.citation import Reference
from app.models.file import File, FileWorkLink, Location
from app.models.metadata import MetadataAssertion
from app.models.work import Work
from app.services.audit import record_event
from app.services.tei_parser import ParsedPaper, parse_tei
from app.utils.normalization import normalize_title

# fetch_tei takes the PDF path and returns raw TEI XML (the GROBID call, injected for testing).
TeiFetcher = Callable[[Path], str]


def store_parsed_extraction(
    db: Session,
    *,
    work: Work,
    parsed: ParsedPaper,
    source: str = "grobid",
) -> dict[str, Any]:
    """Record assertions, promote safe canonical fields, and (re)create references."""
    promotable = not work.user_confirmed
    promoted: list[str] = []

    def assert_field(field_name: str, value: str | None, *, canonical: bool) -> None:
        if not value:
            return
        db.add(
            MetadataAssertion(
                entity_type="work",
                entity_id=work.id,
                field_name=field_name,
                value=value,
                source=source,
                selected_as_canonical=canonical,
            )
        )

    title_canonical = bool(
        promotable
        and parsed.title
        and work.canonical_metadata_source in (None, "filename")
    )
    if title_canonical:
        work.canonical_title = parsed.title
        work.normalized_title = normalize_title(parsed.title or "")
        work.canonical_metadata_source = source
        promoted.append("title")
    assert_field("title", parsed.title, canonical=title_canonical)

    abstract_canonical = bool(promotable and parsed.abstract and not work.abstract)
    if abstract_canonical:
        work.abstract = parsed.abstract
        promoted.append("abstract")
    assert_field("abstract", parsed.abstract, canonical=abstract_canonical)

    doi_canonical = bool(promotable and parsed.doi and not work.doi)
    if doi_canonical:
        work.doi = parsed.doi
        promoted.append("doi")
    assert_field("doi", parsed.doi, canonical=doi_canonical)

    if parsed.authors:
        assert_field("authors", "; ".join(parsed.authors), canonical=False)

    work.updated_at = datetime.utcnow()

    # References are owned by the extraction: replace prior extracted references idempotently.
    db.execute(delete(Reference).where(Reference.citing_work_id == work.id))
    for reference in parsed.references:
        db.add(
            Reference(
                citing_work_id=work.id,
                raw_citation=reference.raw_citation,
                title=reference.title,
                doi=reference.doi,
                year=reference.year,
            )
        )

    return {
        "promoted": promoted,
        "reference_count": len(parsed.references),
        "author_count": len(parsed.authors),
    }


def extract_and_store(
    db: Session,
    *,
    file: File,
    fetch_tei: TeiFetcher,
    actor_user_id=None,
) -> dict[str, Any]:
    """Run extraction for a file: locate its work + path, fetch/parse TEI, persist."""
    link = db.scalar(select(FileWorkLink).where(FileWorkLink.file_id == file.id))
    if link is None:
        raise ValueError("File has no linked work to attach extraction to")
    work = db.get(Work, link.work_id)
    if work is None:
        raise ValueError("Linked work not found")

    location = db.scalar(
        select(Location).where(
            Location.file_id == file.id,
            Location.location_type == "server_path",
        )
    )
    if location is None or not location.internal_uri:
        raise ValueError("No server-path location available for extraction")

    tei_xml = fetch_tei(Path(location.internal_uri))
    parsed = parse_tei(tei_xml)
    summary = store_parsed_extraction(db, work=work, parsed=parsed)

    record_event(
        db,
        "extraction.completed",
        actor_user_id=actor_user_id,
        entity_type="work",
        entity_id=str(work.id),
        details={"file_id": str(file.id), "source": "grobid", **summary},
    )
    return summary

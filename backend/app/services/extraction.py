"""Persist GROBID extraction results as provenance-aware metadata and references.

Follows the spec rule that external sources never silently overwrite user-confirmed data:
every value is recorded as a MetadataAssertion, and canonical fields are only promoted when
the work has not been user-confirmed and the field is empty or filename-derived.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.citation import CitationMention, RawTeiDocument, Reference
from app.models.file import File, FileWorkLink
from app.models.metadata import MetadataAssertion
from app.models.work import Work
from app.services.audit import record_event
from app.services.file_paths import resolve_backend_readable_pdf_path
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
    file: File | None = None,
    raw_tei_xml: str | None = None,
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
        promotable and parsed.title and work.canonical_metadata_source in (None, "filename")
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

    work.updated_at = datetime.now(UTC)

    source_tei: RawTeiDocument | None = None
    if file is not None and raw_tei_xml:
        db.execute(
            delete(RawTeiDocument).where(
                RawTeiDocument.file_id == file.id,
                RawTeiDocument.work_id == work.id,
                RawTeiDocument.source == source,
            )
        )
        source_tei = RawTeiDocument(
            file_id=file.id,
            work_id=work.id,
            source=source,
            tei_xml=raw_tei_xml,
        )
        db.add(source_tei)
        db.flush()

    # References and mentions are owned by the extraction: replace idempotently.
    db.execute(delete(CitationMention).where(CitationMention.citing_work_id == work.id))
    db.execute(delete(Reference).where(Reference.citing_work_id == work.id))
    reference_by_key: dict[str, Reference] = {}
    for index, reference in enumerate(parsed.references):
        saved = Reference(
            citing_work_id=work.id,
            raw_citation=reference.raw_citation,
            title=reference.title,
            doi=reference.doi,
            year=reference.year,
            source_tei_id=source_tei.id if source_tei else None,
        )
        db.add(saved)
        db.flush()
        if reference.key:
            reference_by_key[reference.key] = saved
        reference_by_key[f"b{index}"] = saved

    mention_count = 0
    for mention in parsed.citation_mentions:
        reference = reference_by_key.get(mention.reference_key)
        if reference is None:
            continue
        db.add(
            CitationMention(
                citing_work_id=work.id,
                reference_id=reference.id,
                resolved_cited_work_id=reference.resolved_work_id,
                marker_text=mention.marker_text,
                section_label=mention.section_label,
                context_before=mention.context_before,
                context_sentence=mention.context_sentence,
                context_after=mention.context_after,
                page=mention.page,
                pdf_coordinates=mention.pdf_coordinates or None,
                source_tei_id=source_tei.id if source_tei else None,
            )
        )
        mention_count += 1

    return {
        "promoted": promoted,
        "reference_count": len(parsed.references),
        "citation_mention_count": mention_count,
        "author_count": len(parsed.authors),
        "raw_tei_stored": source_tei is not None,
    }


def extract_and_store(
    db: Session,
    *,
    file: File,
    fetch_tei: TeiFetcher,
    actor_user_id=None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Run extraction for a file: locate its work + path, fetch/parse TEI, persist.

    The PDF path is resolved through the shared resolver, so both server-folder
    (``server_path``) and uploaded managed-library (``managed_path``) files are extractable
    and validated against their configured roots (AUDIT A1).
    """
    settings = settings or get_settings()
    link = db.scalar(select(FileWorkLink).where(FileWorkLink.file_id == file.id))
    if link is None:
        raise ValueError("File has no linked work to attach extraction to")
    work = db.get(Work, link.work_id)
    if work is None:
        raise ValueError("Linked work not found")

    pdf_path = resolve_backend_readable_pdf_path(db, file=file, settings=settings)

    tei_xml = fetch_tei(pdf_path)
    parsed = parse_tei(tei_xml)
    summary = store_parsed_extraction(db, work=work, parsed=parsed, file=file, raw_tei_xml=tei_xml)

    record_event(
        db,
        "extraction.completed",
        actor_user_id=actor_user_id,
        entity_type="work",
        entity_id=str(work.id),
        details={"file_id": str(file.id), "source": "grobid", **summary},
    )
    return summary

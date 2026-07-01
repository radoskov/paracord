"""Persist GROBID extraction results as provenance-aware metadata and references.

Follows the spec rule that external sources never silently overwrite user-confirmed data:
every value is recorded as a MetadataAssertion, and canonical fields are only promoted when
the work has not been user-confirmed and the field is empty or filename-derived.
"""

import contextlib
import tempfile
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
from app.services import ocr as ocr_service
from app.services.ai_config import get_ai_config
from app.services.audit import record_event
from app.services.file_paths import resolve_backend_readable_pdf_path
from app.services.keyword_extraction import extract_keywords
from app.services.tei_parser import ParsedPaper, extract_body_text, parse_tei
from app.utils.normalization import normalize_title

# fetch_tei takes the PDF path and returns raw TEI XML (the GROBID call, injected for testing).
TeiFetcher = Callable[[Path], str]


def _text_layer_quality(body_text: str, abstract: str | None, page_count: int | None) -> str:
    """Classify a PDF's text layer (SPEC §8.3): ``poor`` likely needs OCR, else ``good``."""
    chars = len(body_text or "") + len(abstract or "")
    pages = page_count or 1
    # Heuristic: a born-digital PDF yields hundreds+ of chars per page; near-empty ⇒ scanned.
    if chars < max(200, 100 * pages // 4):
        return "poor"
    return "good"


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

    # Deterministic keyword extraction (SPEC §8.15.1) over the richest available text, and a
    # text-layer / needs-OCR signal for the file (SPEC §8.3).
    body_text = extract_body_text(raw_tei_xml) if raw_tei_xml else ""
    keyword_source = " ".join(part for part in (parsed.abstract, body_text) if part)
    if keyword_source:
        work.keywords = extract_keywords(keyword_source, top_k=12)
    if file is not None:
        file.text_layer_quality = _text_layer_quality(body_text, parsed.abstract, file.page_count)

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

    # Effective OCR/advanced-extraction backend: DB row (runtime toggle) overlaid on Settings,
    # honouring the admin's choice like the topic job does.
    ocr_backend = get_ai_config(db, settings=settings).ocr_backend

    # OCR pre-step: when OCRmyPDF is selected and the text layer is poor/none/unknown, add a
    # searchable text layer to a *transient* copy and feed that to GROBID. OCR never fails the
    # extraction (maybe_ocr swallows errors and returns the original path on failure/skip).
    ocr_result: ocr_service.OcrResult | None = None
    tei_source_path = pdf_path
    if ocr_backend == "ocrmypdf" and ocr_service.needs_ocr(file.text_layer_quality):
        with tempfile.TemporaryDirectory(prefix="paracord-ocr-") as scratch:
            ocr_result = ocr_service.maybe_ocr(
                pdf_path,
                text_layer_quality=file.text_layer_quality,
                out_dir=Path(scratch),
                timeout=settings.ocr_timeout_seconds,
                language=settings.ocr_language,
                skip_if_good=settings.ocr_skip_if_text_layer_good,
            )
            tei_source_path = ocr_result.output_pdf_path
            tei_xml = fetch_tei(tei_source_path)
    elif ocr_backend == "full_ml" and ocr_service.ml_extraction_available(
        settings.extraction_backend
    ):
        # Activate-when-present: an installed ML extractor would return searchable text here. It is
        # not wired yet (run_ml_extraction raises), so we degrade to GROBID — ML failure or absence
        # never fails extraction, and we never install the dep at runtime.
        with contextlib.suppress(Exception):  # degrade to GROBID; ML failure never fails extraction
            _ = ocr_service.run_ml_extraction(pdf_path, backend=settings.extraction_backend)
        tei_xml = fetch_tei(pdf_path)
    else:
        tei_xml = fetch_tei(pdf_path)

    parsed = parse_tei(tei_xml)
    summary = store_parsed_extraction(db, work=work, parsed=parsed, file=file, raw_tei_xml=tei_xml)

    # OCR provenance in the summary + audit. When OCR actually ran, "ocr_added" wins over the
    # post-GROBID text-layer recompute (the file now carries a text layer we added).
    ocr_ran = bool(ocr_result and ocr_result.ran)
    if ocr_ran and ocr_result is not None:
        file.text_layer_quality = ocr_result.text_layer_quality or "ocr_added"
    summary["ocr_backend"] = ocr_backend
    summary["ocr_ran"] = ocr_ran
    summary["ocr_engine"] = ocr_result.engine if ocr_result else None
    summary["ocr_error"] = ocr_result.error if ocr_result else None

    record_event(
        db,
        "extraction.completed",
        actor_user_id=actor_user_id,
        entity_type="work",
        entity_id=str(work.id),
        details={"file_id": str(file.id), "source": "grobid", **summary},
    )
    return summary

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
from app.services.file_paths import resolve_backend_readable_pdf_path, save_derived_ocr_pdf
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


def _resolve_ocr_engine(
    ocr_backend: str, *, text_layer_quality: str | None, force_ocr: bool
) -> str | None:
    """Pick the OCR pre-step engine (``"ocrmypdf"`` / ``"pymupdf"``) or ``None``.

    Gated on the engine being installed AND either the admin selected an OCR backend and the text
    layer needs OCR, OR the caller forced it (#22). ``pymupdf`` is used only when that backend is
    selected; ``ocrmypdf`` covers the ``ocrmypdf`` backend and is the fallback engine for a forced
    re-OCR under a non-OCR backend (``none`` / ``full_ml``), preserving the prior force behaviour.
    """
    needs = ocr_service.needs_ocr(text_layer_quality)
    if ocr_backend == "pymupdf":
        if (force_ocr or needs) and ocr_service.pymupdf_available():
            return "pymupdf"
        return None
    if (force_ocr or (ocr_backend == "ocrmypdf" and needs)) and ocr_service.ocrmypdf_available():
        return "ocrmypdf"
    return None


# Below this many chars, GROBID's parsed body is treated as "weak" (e.g. a scan it couldn't read),
# so the PyMuPDF hard-extracted text is folded in to enrich keyword extraction + the text-layer signal.
_WEAK_BODY_THRESHOLD = 200


def store_parsed_extraction(
    db: Session,
    *,
    work: Work,
    parsed: ParsedPaper,
    source: str = "grobid",
    file: File | None = None,
    raw_tei_xml: str | None = None,
    extra_text: str | None = None,
) -> dict[str, Any]:
    """Record assertions, promote safe canonical fields, and (re)create references.

    ``extra_text`` is optional PyMuPDF hard-extracted text (full_ml route); it enriches the keyword
    input and the text-layer signal when GROBID's own body is weak/empty.
    """
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
    body_text = (extract_body_text(raw_tei_xml) if raw_tei_xml else "") or ""
    # When GROBID's body is weak (e.g. a scanned PDF it couldn't parse), fold in the PyMuPDF
    # hard-extracted text so keyword extraction + the text-layer-quality signal still have material.
    if extra_text and len(body_text) < _WEAK_BODY_THRESHOLD:
        body_text = f"{body_text}\n{extra_text}".strip() if body_text else extra_text
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
    force_ocr: bool = False,
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

    # Effective OCR/advanced-extraction backend + languages: DB row (runtime toggle) overlaid on
    # Settings, honouring the admin's choice like the topic job does. ocr_language is tesseract
    # syntax and may be multi like "eng+spa".
    ai_config = get_ai_config(db, settings=settings)
    ocr_backend = ai_config.ocr_backend
    ocr_language = ai_config.ocr_language

    # OCR pre-step: when an OCR backend is selected and the text layer is poor/none/unknown, add a
    # searchable text layer to a *transient* copy and feed that to GROBID. OCR never fails the
    # extraction (the OCR helpers swallow errors and return the original path on failure/skip).
    ocr_result: ocr_service.OcrResult | None = None
    tei_source_path = pdf_path
    hard_text: str | None = (
        None  # PyMuPDF hard-extracted text (full_ml route) to enrich weak GROBID
    )
    # Pick the OCR engine to run (or None): ocrmypdf or pymupdf, gated on availability + the
    # text-layer-quality/#22-force rule. force_ocr uses the selected OCR engine (pymupdf when that
    # backend is chosen, else ocrmypdf) regardless of the current quality.
    ocr_engine = _resolve_ocr_engine(
        ocr_backend, text_layer_quality=file.text_layer_quality, force_ocr=force_ocr
    )
    if ocr_engine is not None:
        with tempfile.TemporaryDirectory(prefix="paracord-ocr-") as scratch:
            if ocr_engine == "pymupdf":
                ocr_result = ocr_service.pymupdf_ocr(
                    pdf_path,
                    out_dir=Path(scratch),
                    language=ocr_language,
                    timeout=settings.ocr_timeout_seconds,
                )
            else:
                ocr_result = ocr_service.maybe_ocr(
                    pdf_path,
                    text_layer_quality=file.text_layer_quality,
                    out_dir=Path(scratch),
                    timeout=settings.ocr_timeout_seconds,
                    language=ocr_language,
                    skip_if_good=settings.ocr_skip_if_text_layer_good and not force_ocr,
                )
            tei_source_path = ocr_result.output_pdf_path
            tei_xml = fetch_tei(tei_source_path)
            # Persist the searchable OCR'd copy to a DERIVED location so the reader can serve
            # selectable text (never mutate the content-addressed original). Best-effort; the temp
            # copy is about to be deleted, so it must be persisted inside this block.
            if ocr_result.ran and tei_source_path != pdf_path:
                save_derived_ocr_pdf(settings, file.sha256, tei_source_path)
    elif ocr_backend == "full_ml":
        # Hard-extraction route: GROBID stays the structured extractor, but we also pull raw text via
        # the PyMuPDF core extractor (get_text + OCR fallback) to enrich keywords/body when GROBID's
        # body is weak. Never fails extraction (run_ml_extraction with the pymupdf backend swallows).
        tei_xml = fetch_tei(pdf_path)
        with contextlib.suppress(Exception):  # hard-extraction failure never fails extraction
            hard_text = ocr_service.run_ml_extraction(
                pdf_path, backend="pymupdf", language=ocr_language
            )
    else:
        tei_xml = fetch_tei(pdf_path)

    parsed = parse_tei(tei_xml)
    summary = store_parsed_extraction(
        db, work=work, parsed=parsed, file=file, raw_tei_xml=tei_xml, extra_text=hard_text
    )

    # OCR provenance in the summary + audit. When OCR actually ran, "ocr_added" wins over the
    # post-GROBID text-layer recompute (the file now carries a text layer we added).
    ocr_ran = bool(ocr_result and ocr_result.ran)
    if ocr_ran and ocr_result is not None:
        file.text_layer_quality = ocr_result.text_layer_quality or "ocr_added"
    summary["ocr_backend"] = ocr_backend
    summary["ocr_ran"] = ocr_ran
    summary["ocr_engine"] = ocr_result.engine if ocr_result else None
    summary["ocr_error"] = ocr_result.error if ocr_result else None
    # Surface availability + text-layer quality so the UI can explain a textless scan (#22):
    # "OCR not installed" vs "OCR ran" vs "text layer already good".
    summary["ocr_available"] = (
        ocr_service.pymupdf_available()
        if ocr_backend == "pymupdf"
        else ocr_service.ocrmypdf_available()
    )
    summary["ocr_forced"] = force_ocr
    summary["text_layer_quality"] = file.text_layer_quality

    record_event(
        db,
        "extraction.completed",
        actor_user_id=actor_user_id,
        entity_type="work",
        entity_id=str(work.id),
        details={"file_id": str(file.id), "source": "grobid", **summary},
    )
    return summary
